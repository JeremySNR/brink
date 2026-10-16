"""The hosted game service: everything the API does, as functions over one Postgres connection.

A game's engine state lives in `games.state` (Game.to_state() minus "phases") plus the append-only
`game_phases` table, and is advanced with brink.session one decision point at a time:

    forming --start--> active --(every human ready | deadline passed)--> ... --> finished

Advancing (shared by submit, the cron tick and a lazy GET) reads the state, resolves the decision point
with the humans' saved orders (missing humans are in civil disorder), lets the bots play any decision
points that involve no human, then writes everything in one transaction guarded by
`UPDATE games ... WHERE step = <old step>`: if another request advanced first, nothing is written.
"""
from __future__ import annotations

import datetime as dt
import random
import re
import secrets
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable

from psycopg import Connection, Rollback
from psycopg.types.json import Jsonb

from .. import session as S
from ..game import Rules
from ..map import BrinkMap
from . import email as mail
from .auth import User

ROOT = Path(__file__).resolve().parents[3]
MAP_PATH = ROOT / "build" / "map.json"
DEADLINE_HOURS = (12, 24, 48, 72)
LAST_YEARS = range(2032, 2041)
BOT_KIND = "search"
INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,30}[A-Za-z0-9_.\-]$")
TICK_BUDGET_S = 40.0


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def bad(msg: str) -> ApiError:
    return ApiError(400, msg)


@lru_cache(maxsize=1)
def game_map() -> BrinkMap:
    return BrinkMap.load(MAP_PATH)


def powers() -> list[str]:
    return list(game_map().power_order)


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ---------------------------------------------------------------------------------------------- notify
# In-app notifications. A notifier can be added by appending a callable to NOTIFIERS: it receives the same
# arguments after the rows are written, inside the transaction. Turn emails are not a notifier: they go out
# after the advance commits (brink.hosted.email, called from start, advance_game and tick).
NOTIFIERS: list[Callable[..., None]] = []


def notify(conn: Connection, user_ids: Iterable[str | None], game_id: str | None, kind: str, body: str) -> None:
    ids = sorted({str(u) for u in user_ids if u})
    if not ids:
        return
    with conn.cursor() as cur:
        cur.executemany("insert into notifications (user_id, game_id, kind, body) values (%s, %s, %s, %s)",
                        [(u, game_id, kind, body) for u in ids])
    for fn in NOTIFIERS:
        fn(conn, ids, game_id, kind, body)


# ---------------------------------------------------------------------------------------------- profiles
def ensure_profile(conn: Connection, user: User) -> dict:
    row = conn.execute("select id, display_name, created_at from profiles where id = %s", (user.id,)).fetchone()
    if row:
        return row
    if user.dev:   # local dev only: there is no Supabase Auth, so stand in for its users table
        conn.execute("insert into auth.users (id, email) values (%s, %s) on conflict (id) do nothing",
                     (user.id, user.email))
    base = (user.email or "player").split("@")[0]
    conn.execute("insert into profiles (id, display_name) values (%s, brink_unique_name(%s)) "
                 "on conflict (id) do nothing", (user.id, base))
    return conn.execute("select id, display_name, created_at from profiles where id = %s", (user.id,)).fetchone()


def me(conn: Connection, user: User) -> dict:
    p = ensure_profile(conn, user)
    unread = conn.execute("select count(*) as n from notifications where user_id = %s and read_at is null",
                          (user.id,)).fetchone()["n"]
    pref = conn.execute("select email_notifications from profiles where id = %s", (user.id,)).fetchone()
    return {"id": str(p["id"]), "display_name": p["display_name"], "email": user.email, "unread": unread,
            "email_notifications": bool(pref["email_notifications"])}


def update_me(conn: Connection, user: User, body: dict) -> dict:
    """Change the display name and/or the turn-email preference (either key may be left out)."""
    ensure_profile(conn, user)
    if "display_name" not in body and "email_notifications" not in body:
        raise bad("nothing to change: send display_name and/or email_notifications")
    if "email_notifications" in body:
        if not isinstance(body["email_notifications"], bool):
            raise bad("email_notifications must be true or false")
        mail.set_preference(conn, user.id, body["email_notifications"])
    if "display_name" in body:
        name = " ".join(str(body.get("display_name") or "").split())
        if not NAME_RE.match(name):
            raise bad("display name: 2 to 32 letters, digits, spaces, '.', '_' or '-'")
        taken = conn.execute("select 1 from profiles where lower(display_name) = lower(%s) and id <> %s",
                             (name, user.id)).fetchone()
        if taken:
            raise ApiError(409, f"the name {name!r} is taken")
        conn.execute("update profiles set display_name = %s where id = %s", (name, user.id))
    return me(conn, user)


# ---------------------------------------------------------------------------------------------- reading games
def _game(conn: Connection, game_id: str, lock: bool = False) -> dict:
    try:
        gid = str(uuid.UUID(str(game_id)))
    except ValueError:
        raise ApiError(404, "no such game")
    row = conn.execute("select * from games where id = %s" + (" for update" if lock else ""), (gid,)).fetchone()
    if not row:
        raise ApiError(404, "no such game")
    return row


def _seats(conn: Connection, game_id: str) -> list[dict]:
    rows = conn.execute(
        "select s.power, s.user_id, s.missed, s.joined_at, p.display_name from game_seats s "
        "left join profiles p on p.id = s.user_id where s.game_id = %s", (game_id,)).fetchall()
    order = {p: i for i, p in enumerate(powers())}
    return sorted(rows, key=lambda r: order.get(r["power"], 99))


def _my_power(seats: list[dict], user: User | None) -> str | None:
    if user is None:
        return None
    return next((s["power"] for s in seats if s["user_id"] is not None and str(s["user_id"]) == user.id), None)


def _humans(seats: list[dict]) -> list[str]:
    return [s["power"] for s in seats if s["user_id"] is not None]


def _summary(g: dict, seats: list[dict] | None = None, creator_name: str | None = None) -> dict:
    out = {
        "id": str(g["id"]), "name": g["name"], "status": g["status"], "visibility": g["visibility"],
        "deadline_hours": g["deadline_hours"], "press": g["press"], "last_year": g["last_year"],
        "phase_name": g["phase_name"], "deadline": g["deadline"], "step": g["step"],
        "created_at": g["created_at"], "started_at": g["started_at"], "finished_at": g["finished_at"],
        "created_by": str(g["created_by"]),
    }
    if creator_name is not None:
        out["created_by_name"] = creator_name
    if seats is not None:
        out["players"] = len(_humans(seats))
        out["open_seats"] = [s["power"] for s in seats if s["user_id"] is None]
    return out


def load_state(conn: Connection, g: dict) -> dict:
    """The full engine state: games.state plus every phase record."""
    rows = conn.execute("select record from game_phases where game_id = %s order by idx", (g["id"],)).fetchall()
    state = dict(g["state"])
    state["phases"] = [r["record"] for r in rows]
    return state


def lobby(conn: Connection) -> list[dict]:
    rows = conn.execute(
        "select g.*, p.display_name as creator_name from games g join profiles p on p.id = g.created_by "
        "where g.status = 'forming' and g.visibility = 'public' order by g.created_at desc limit 100").fetchall()
    seats = _seats_by_game(conn, [r["id"] for r in rows])
    return [_summary(r, seats.get(r["id"], []), r["creator_name"]) for r in rows]


def _seats_by_game(conn: Connection, ids: list) -> dict:
    if not ids:
        return {}
    out: dict = {}
    for r in conn.execute("select game_id, power, user_id from game_seats where game_id = any(%s)", (ids,)):
        out.setdefault(r["game_id"], []).append(r)
    return out


def my_games(conn: Connection, user: User) -> list[dict]:
    ensure_profile(conn, user)
    rows = conn.execute(
        "select g.*, s.power as my_power, coalesce(sub.ready, false) as my_ready, (sub.power is not null) as my_saved, "
        "p.display_name as creator_name "
        "from game_seats s join games g on g.id = s.game_id join profiles p on p.id = g.created_by "
        "left join submissions sub on sub.game_id = g.id and sub.power = s.power and sub.step = g.step "
        "where s.user_id = %s and g.status <> 'cancelled'", (user.id,)).fetchall()
    seats = _seats_by_game(conn, [r["id"] for r in rows])
    out = []
    for r in rows:
        pend = r["pending"] or {}
        involved = r["status"] == "active" and r["my_power"] in (pend.get("powers") or [])
        d = _summary(r, seats.get(r["id"], []), r["creator_name"])
        d.update(power=r["my_power"], kind=pend.get("kind"), my_turn=bool(involved and not r["my_ready"]),
                 involved=involved, ready=r["my_ready"], saved=r["my_saved"],
                 creator=str(r["created_by"]) == user.id, result=r["result"])
        out.append(d)
    status_rank = {"active": 0, "forming": 1, "finished": 2}
    far = dt.datetime.max.replace(tzinfo=dt.timezone.utc)
    out.sort(key=lambda d: (not d["my_turn"], status_rank.get(d["status"], 3), d["deadline"] or far,
                            -(d["created_at"].timestamp())))
    return out


# ---------------------------------------------------------------------------------------------- forming
def _invite_code() -> str:
    return "".join(secrets.choice(INVITE_ALPHABET) for _ in range(8))


def _power_arg(value) -> str | None:
    if value in (None, "", "random"):
        return None
    p = str(value).upper()
    if p not in powers():
        raise bad(f"power must be one of {', '.join(powers())}, or null for a random seat")
    return p


def create_game(conn: Connection, user: User, body: dict) -> dict:
    ensure_profile(conn, user)
    name = " ".join(str(body.get("name") or "").split())
    if not 1 <= len(name) <= 60:
        raise bad("name: 1 to 60 characters")
    visibility = body.get("visibility", "public")
    if visibility not in ("public", "private"):
        raise bad("visibility must be public or private")
    try:
        hours = int(body.get("deadline_hours", 24))
        last_year = int(body.get("last_year", 2040))
    except (TypeError, ValueError):
        raise bad("deadline_hours and last_year must be integers")
    if hours not in DEADLINE_HOURS:
        raise bad(f"deadline_hours must be one of {', '.join(map(str, DEADLINE_HOURS))}")
    if last_year not in LAST_YEARS:
        raise bad("last_year must be between 2032 and 2040")
    press = body.get("press", "full")
    if press not in ("full", "none"):
        raise bad("press must be full or none")
    power = _power_arg(body.get("power")) or random.choice(powers())
    gid = str(uuid.uuid4())
    conn.execute(
        "insert into games (id, name, created_by, visibility, invite_code, deadline_hours, press, last_year, "
        "bot_kind, seed) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (gid, name, user.id, visibility, _invite_code(), hours, press, last_year, BOT_KIND,
         random.randrange(1 << 30)))
    with conn.cursor() as cur:
        cur.executemany("insert into game_seats (game_id, power) values (%s, %s)", [(gid, p) for p in powers()])
    conn.execute("update game_seats set user_id = %s, joined_at = now() where game_id = %s and power = %s",
                 (user.id, gid, power))
    return get_game(conn, user, gid)


def invite(conn: Connection, code: str) -> dict:
    row = conn.execute(
        "select g.*, p.display_name as creator_name from games g join profiles p on p.id = g.created_by "
        "where g.invite_code = %s", (str(code).strip().upper(),)).fetchone()
    if not row:
        raise ApiError(404, "that invite code doesn't match a game")
    return _summary(row, _seats(conn, row["id"]), row["creator_name"])


def join(conn: Connection, user: User, game_id: str, body: dict) -> dict:
    prof = ensure_profile(conn, user)
    g = _game(conn, game_id, lock=True)
    if g["status"] != "forming":
        raise ApiError(409, "this game has already started")
    if g["visibility"] == "private" and str(body.get("code") or "").strip().upper() != g["invite_code"]:
        mine = _my_power(_seats(conn, g["id"]), user)
        if mine is None:
            raise ApiError(403, "this game is private: join it with its invite link")
    want = _power_arg(body.get("power"))
    seats = _seats(conn, g["id"])
    mine = _my_power(seats, user)
    free = [s["power"] for s in seats if s["user_id"] is None]
    if want is None:
        if mine:
            return get_game(conn, user, g["id"])
        if not free:
            raise ApiError(409, "every seat is taken")
        want = random.choice(free)
    elif want != mine and want not in free:
        raise ApiError(409, f"{want} is taken")
    if mine and mine != want:
        conn.execute("update game_seats set user_id = null, joined_at = null where game_id = %s and power = %s",
                     (g["id"], mine))
    if mine != want:
        conn.execute("update game_seats set user_id = %s, joined_at = now() where game_id = %s and power = %s",
                     (user.id, g["id"], want))
    if not mine and str(g["created_by"]) != user.id:
        notify(conn, [g["created_by"]], str(g["id"]), "joined", f"{prof['display_name']} joined {g['name']}")
    return get_game(conn, user, g["id"])


def leave(conn: Connection, user: User, game_id: str) -> dict:
    g = _game(conn, game_id, lock=True)
    if g["status"] != "forming":
        raise ApiError(409, "you can only leave a game before it starts")
    if str(g["created_by"]) == user.id:
        raise ApiError(409, "you created this game: cancel it instead")
    conn.execute("update game_seats set user_id = null, joined_at = null where game_id = %s and user_id = %s",
                 (g["id"], user.id))
    return get_game(conn, user, g["id"])


def cancel(conn: Connection, user: User, game_id: str) -> dict:
    g = _game(conn, game_id, lock=True)
    if str(g["created_by"]) != user.id:
        raise ApiError(403, "only the game's creator can cancel it")
    if g["status"] != "forming":
        raise ApiError(409, "only a game that hasn't started can be cancelled")
    seats = _seats(conn, g["id"])
    conn.execute("update games set status = 'cancelled', updated_at = now() where id = %s", (g["id"],))
    notify(conn, [s["user_id"] for s in seats if str(s["user_id"]) != user.id], str(g["id"]), "cancelled",
           f"{g['name']} was cancelled")
    return get_game(conn, user, g["id"])


def start(conn: Connection, user: User, game_id: str) -> dict:
    g = _game(conn, game_id, lock=True)
    if str(g["created_by"]) != user.id:
        raise ApiError(403, "only the game's creator can start it")
    if g["status"] != "forming":
        raise ApiError(409, "this game has already started")
    seats = _seats(conn, g["id"])
    humans = _humans(seats)
    if not humans:
        raise ApiError(409, "a game needs at least one player")
    gmap = game_map()
    rules = Rules()
    rules.last_year = g["last_year"]
    state = S.new_game(gmap, seed=g["seed"], humans=humans, bot_kind=g["bot_kind"], rules=rules)
    state = S.run_bots_until_human(gmap, state)
    _write_state(conn, g, state, 0, started=True)
    notify(conn, [s["user_id"] for s in seats], str(g["id"]), "started",
           f"{g['name']} has started: write your orders")
    conn.commit()                                   # the first decision point is open: email it after commit
    mail.send_due(conn, str(g["id"]))
    return get_game(conn, user, g["id"])


# ---------------------------------------------------------------------------------------------- state writes
def _pending_summary(state: dict) -> dict | None:
    p = S.pending(game_map(), state, powers=[])
    if p is None:
        return None
    p.pop("options", None)
    return p


def _write_state(conn: Connection, g: dict, state: dict, old_phases: int, started: bool = False,
                 expect_step: int | None = None) -> bool:
    """Persist a state (new phases appended). With expect_step, only if the row is still at that step."""
    phases = state["phases"]
    slim = {k: v for k, v in state.items() if k != "phases"}
    pend = _pending_summary(state)
    finished = state.get("result") is not None
    deadline = None if finished else now() + dt.timedelta(hours=g["deadline_hours"])
    sql = ("update games set state = %s, step = %s, pending = %s, phase_name = %s, deadline = %s, result = %s, "
           "status = %s, updated_at = now(), started_at = coalesce(started_at, %s), "
           "finished_at = case when %s then now() else finished_at end where id = %s")
    args: list[Any] = [Jsonb(slim), state["step"], Jsonb(pend) if pend else None, pend["phase"] if pend else None,
                       deadline, Jsonb(state["result"]) if finished else None,
                       "finished" if finished else "active", now() if started else None, finished, g["id"]]
    if expect_step is not None:
        sql += " and step = %s and status = 'active'"
        args.append(expect_step)
    if conn.execute(sql + " returning id", args).fetchone() is None:
        return False
    if len(phases) > old_phases:
        with conn.cursor() as cur:
            cur.executemany("insert into game_phases (game_id, idx, record) values (%s, %s, %s)",
                            [(g["id"], i, Jsonb(phases[i])) for i in range(old_phases, len(phases))])
    return True


def advance_game(conn: Connection, game_id: str, reason: str) -> bool:
    """Resolve the open decision point if it is due: reason 'ready' (every involved human is ready) or
    'deadline' (the deadline has passed). True if this call advanced the game."""
    conn.commit()                                   # read outside any earlier transaction
    g = _game(conn, game_id)
    if g["status"] != "active" or not g["pending"]:
        return False
    step = g["step"]
    seats = _seats(conn, g["id"])
    humans = set(_humans(seats))
    involved = [p for p in g["pending"]["powers"] if p in humans]
    subs = {r["power"]: r for r in conn.execute(
        "select power, orders, ready from submissions where game_id = %s and step = %s", (g["id"], step))}
    if reason == "ready":
        if not involved or not all(subs.get(p, {}).get("ready") for p in involved):
            return False
    elif reason == "deadline":
        if g["deadline"] is None or g["deadline"] > now():
            return False
    else:
        raise ValueError(reason)
    state = load_state(conn, g)
    old_phases = len(state["phases"])
    conn.commit()                                   # don't hold a transaction while the engine runs

    gmap = game_map()
    decisions = {p: list(subs[p]["orders"] or []) for p in involved if p in subs}
    missing = [p for p in involved if p not in subs]
    try:
        new = S.advance(gmap, state, decisions, expect_step=step)
    except (S.StaleState, S.GameOver):
        return False
    new = S.run_bots_until_human(gmap, new)

    advanced = False
    with conn.transaction():
        if not _write_state(conn, g, new, old_phases, expect_step=step):
            raise Rollback()                        # someone else advanced it first: write nothing
        if missing:
            conn.execute("update game_seats set missed = missed + 1 where game_id = %s and power = any(%s)",
                         (g["id"], missing))
        _notify_advanced(conn, g, seats, new)
        advanced = True
    if advanced and new.get("result") is None:     # committed: tell whoever decides next (never raises)
        mail.send_due(conn, str(g["id"]))
    return advanced


def _notify_advanced(conn: Connection, g: dict, seats: list[dict], state: dict) -> None:
    by_power = {s["power"]: s["user_id"] for s in seats if s["user_id"] is not None}
    if state.get("result") is not None:
        notify(conn, by_power.values(), str(g["id"]), "finished", f"{g['name']} is over: see the result")
        return
    pend = _pending_summary(state) or {}
    label = _phase_label(pend.get("phase", ""))
    what = {"retreats": "retreats", "propose": "you chair the Assembly",
            "winter": "vote, adjustments and umbrellas"}.get(pend.get("kind"), "orders")
    notify(conn, [by_power[p] for p in pend.get("powers", []) if p in by_power], str(g["id"]), "turn",
           f"Your move in {g['name']}: {label}, {what}")


def _phase_label(name: str) -> str:
    if len(name) < 6:
        return name
    season = {"S": "Spring", "F": "Fall", "W": "Winter"}.get(name[0], name[0])
    kind = {"M": "", "R": " retreats", "A": ""}.get(name[-1], "")
    return f"{season} {name[1:5]}{kind}"


def tick(conn: Connection) -> dict:
    """Advance every active game whose deadline has passed, then send deadline reminders. Idempotent;
    safe to call any time."""
    t0 = time.monotonic()
    ids = [r["id"] for r in conn.execute(
        "select id from games where status = 'active' and deadline <= now() order by deadline limit 50")]
    advanced = []
    for gid in ids:
        if time.monotonic() - t0 > TICK_BUDGET_S:
            break
        try:
            if advance_game(conn, str(gid), "deadline"):
                advanced.append(str(gid))
        except Exception:   # one broken game must not stop the others
            conn.rollback()
    budget = max(0.0, TICK_BUDGET_S + 10.0 - (time.monotonic() - t0))
    reminders = mail.send_reminders(conn, budget_s=min(15.0, budget)) if budget > 0 else {}
    return {"due": len(ids), "advanced": advanced, "reminders": reminders}


# ---------------------------------------------------------------------------------------------- playing
def get_game(conn: Connection, user: User | None, game_id: str, since: int = 0) -> dict:
    g = _game(conn, game_id)
    if g["status"] == "active" and g["deadline"] is not None and g["deadline"] <= now():
        if advance_game(conn, str(g["id"]), "deadline"):
            g = _game(conn, game_id)
    seats = _seats(conn, g["id"])
    mine = _my_power(seats, user)
    creator = user is not None and str(g["created_by"]) == user.id
    creator_name = next((s["display_name"] for s in seats if str(s["user_id"]) == str(g["created_by"])), None)
    pend = g["pending"] if g["status"] == "active" else None
    subs = {}
    if pend:
        subs = {r["power"]: r for r in conn.execute(
            "select power, orders, ready, updated_at from submissions where game_id = %s and step = %s",
            (g["id"], g["step"]))}
    involved = set(pend["powers"]) if pend else set()
    out_seats = []
    for s in seats:
        human = s["user_id"] is not None
        out_seats.append({
            "power": s["power"], "name": s["display_name"], "human": human,
            "bot": not human and g["status"] != "forming", "you": s["power"] == mine and mine is not None,
            "involved": s["power"] in involved, "ready": bool(subs.get(s["power"], {}).get("ready")) if human else None,
            "saved": s["power"] in subs if human else None, "missed": s["missed"],
        })
    game = _summary(g, seats, creator_name)
    if mine or creator:
        game["invite_code"] = g["invite_code"]
    out: dict[str, Any] = {
        "game": game, "seats": out_seats, "you": {"power": mine, "creator": creator, "signed_in": user is not None},
        "pending": pend, "decision": None, "submission": None, "result": g["result"], "phases": [],
        "phase_count": 0, "server_time": now(),
    }
    if g["status"] in ("active", "finished"):
        n = conn.execute("select count(*) as n from game_phases where game_id = %s", (g["id"],)).fetchone()["n"]
        out["phase_count"] = n
        since = max(0, int(since or 0))
        out["phases"] = [r["record"] for r in conn.execute(
            "select record from game_phases where game_id = %s and idx >= %s order by idx", (g["id"], since))]
    if pend and mine and mine in involved:
        state = load_state(conn, g)
        out["decision"] = S.pending(game_map(), state, powers=[mine])
        sub = subs.get(mine)
        if sub:
            out["submission"] = {"orders": sub["orders"], "ready": sub["ready"], "updated_at": sub["updated_at"]}
    return out


def _seated(conn: Connection, user: User, game_id: str) -> tuple[dict, list[dict], str]:
    g = _game(conn, game_id)
    seats = _seats(conn, g["id"])
    mine = _my_power(seats, user)
    if mine is None:
        raise ApiError(403, "you don't have a seat in this game")
    return g, seats, mine


def _orders_arg(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or len(value) > 200:
        raise bad("orders must be a list of order texts")
    out = [str(o).strip() for o in value if str(o).strip()]
    if any(len(o) > 200 for o in out):
        raise bad("an order is too long")
    return out


def sandbox_copy(conn: Connection, user: User, game_id: str) -> dict:
    from ..workshop import snapshot
    g, _, _ = _seated(conn, user, game_id)
    if g["status"] != "active":
        raise bad("Open an active game to copy its position.")
    # Read only: no deadline advancement, no submissions query, no press.
    return {"state": snapshot(load_state(conn, g), list(game_map().power_order))}


def validate_orders(conn: Connection, user: User, game_id: str, body: dict) -> dict:
    g, _, mine = _seated(conn, user, game_id)
    if g["status"] != "active":
        raise ApiError(409, "the game isn't running")
    orders = _orders_arg(body.get("orders"))
    return {"results": S.validate(game_map(), load_state(conn, g), mine, orders)}


def submit(conn: Connection, user: User, game_id: str, body: dict) -> dict:
    g, _, mine = _seated(conn, user, game_id)
    if g["status"] != "active":
        raise ApiError(409, "the game isn't running")
    try:
        step = int(body.get("step"))
    except (TypeError, ValueError):
        raise bad("step is required (the decision point you are answering)")
    if step != g["step"]:
        raise ApiError(409, "that phase has already been resolved")
    if mine not in (g["pending"] or {}).get("powers", []):
        raise ApiError(409, "you have no decision to make this phase")
    orders = _orders_arg(body.get("orders"))
    ready = bool(body.get("ready"))
    conn.execute(
        "insert into submissions (game_id, power, step, orders, ready, updated_at) values (%s, %s, %s, %s, %s, now()) "
        "on conflict (game_id, power, step) do update set orders = excluded.orders, ready = excluded.ready, "
        "updated_at = now()", (g["id"], mine, step, Jsonb(orders), ready))
    if ready:
        advance_game(conn, str(g["id"]), "ready")
    try:
        since = max(0, int(body.get("since") or 0))
    except (TypeError, ValueError):
        since = 0
    return get_game(conn, user, str(g["id"]), since)


# ---------------------------------------------------------------------------------------------- press
def _press_game(conn: Connection, user: User, game_id: str) -> tuple[dict, list[dict], str]:
    g, seats, mine = _seated(conn, user, game_id)
    if g["press"] != "full":
        raise ApiError(403, "this game is played without press")
    return g, seats, mine


def list_messages(conn: Connection, user: User, game_id: str, after: int = 0) -> dict:
    g, _, mine = _press_game(conn, user, game_id)
    rows = conn.execute(
        "select id, sender_power, recipients, body, phase_name, created_at from messages "
        "where game_id = %s and %s = any(recipients) and id > %s order by id limit 500",
        (g["id"], mine, int(after or 0))).fetchall()
    return {"power": mine, "messages": rows}


def post_message(conn: Connection, user: User, game_id: str, body: dict) -> dict:
    g, seats, mine = _press_game(conn, user, game_id)
    if g["status"] != "active":
        raise ApiError(409, "press is open while the game is running")
    text = str(body.get("body") or "").strip()
    if not 1 <= len(text) <= 2000:
        raise bad("a message is 1 to 2000 characters")
    rec = body.get("recipients")
    if not isinstance(rec, list) or not rec:
        raise bad("recipients: a list of powers")
    want = {str(p).upper() for p in rec} - {mine}
    humans = {s["power"]: s["user_id"] for s in seats if s["user_id"] is not None}
    for p in sorted(want):
        if p not in powers():
            raise bad(f"{p} is not a power in this game")
        if p not in humans:
            raise bad(f"{p} is played by a bot")
    if not want:
        raise bad("choose at least one other power")
    recipients = sorted(want | {mine})
    row = conn.execute(
        "insert into messages (game_id, sender_power, recipients, body, phase_name) values (%s, %s, %s, %s, %s) "
        "returning id, sender_power, recipients, body, phase_name, created_at",
        (g["id"], mine, recipients, text, g["phase_name"])).fetchone()
    notify(conn, [humans[p] for p in want], str(g["id"]), "message", f"Press from {mine} in {g['name']}")
    return row


# ---------------------------------------------------------------------------------------------- notifications
def notifications(conn: Connection, user: User) -> dict:
    ensure_profile(conn, user)
    rows = conn.execute(
        "select n.id, n.game_id, n.kind, n.body, n.read_at, n.created_at, g.name as game_name "
        "from notifications n left join games g on g.id = n.game_id "
        "where n.user_id = %s order by n.id desc limit 50", (user.id,)).fetchall()
    unread = conn.execute("select count(*) as n from notifications where user_id = %s and read_at is null",
                          (user.id,)).fetchone()["n"]
    return {"unread": unread, "items": rows}


def mark_read(conn: Connection, user: User, body: dict) -> dict:
    ids = body.get("ids")
    if ids is None:
        conn.execute("update notifications set read_at = now() where user_id = %s and read_at is null", (user.id,))
    else:
        if not isinstance(ids, list):
            raise bad("ids: a list of notification ids")
        conn.execute("update notifications set read_at = now() where user_id = %s and id = any(%s) "
                     "and read_at is null", (user.id, [int(i) for i in ids]))
    return notifications(conn, user)
