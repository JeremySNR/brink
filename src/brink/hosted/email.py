"""Turn emails: "your orders are due" when a decision point opens, and one deadline reminder.

Two triggers, both after the game's state is committed:

    send_due(conn, game_id)   a decision point just opened (service.start / service.advance_game): one email
                              to each human whose power decides it, unless they are already Ready
    send_reminders(conn)      from the per-minute tick: humans in the open decision who haven't marked Ready
                              when the deadline is within REMINDER_WINDOW (6 hours; 3 for 12-hour games)

Dedupe: a row in `email_log` (unique per user, game, step and kind) is inserted and committed *before*
sending, with on-conflict-do-nothing, so two ticks or requests racing each other send once. Players who
turned turn emails off (`profiles.email_notifications`) get none; addresses come from `auth.users.email`.

Sending is Resend's HTTP API (`/emails/batch`, up to 100 per call) with short timeouts. Nothing here may
break the game: without RESEND_API_KEY (or a signing secret for the stop link) it logs and does nothing,
and every failure is logged and recorded on the email_log row, never raised.

Environment (names only): RESEND_API_KEY, BRINK_EMAIL_FROM, BRINK_SITE_URL, BRINK_EMAIL_SECRET (falls back to
SUPABASE_JWT_SECRET). See docs/HOSTING.md.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import logging
import math
import os
import uuid
from typing import Any, Iterable
from urllib.parse import urlsplit

import httpx

from . import email_templates as T

log = logging.getLogger("brink.hosted.email")

RESEND_URL = "https://api.resend.com"
DEFAULT_FROM = "Brink <turns@brinkgame.app>"
DEFAULT_SITE = "https://www.brinkgame.app"
BATCH_MAX = 100
TIMEOUT = httpx.Timeout(8.0, connect=4.0)
TRACK_WINDOW = 5          # years shown on the email's turn record
SEASONS = {"S": "Spring", "F": "Fall", "W": "Winter"}
HALF_ORDER = {"S": 0, "F": 1, "W": 2}


def reminder_window(deadline_hours: int) -> dt.timedelta:
    """How long before the deadline the reminder goes: 6 hours for daily-or-longer games, 3 for 12-hour."""
    return dt.timedelta(hours=6 if deadline_hours >= 24 else 3)


# ---------------------------------------------------------------------------------------------- config
def api_key() -> str | None:
    return os.environ.get("RESEND_API_KEY") or None


def sender() -> str:
    return os.environ.get("BRINK_EMAIL_FROM") or DEFAULT_FROM


def site_url() -> str:
    return (os.environ.get("BRINK_SITE_URL") or DEFAULT_SITE).rstrip("/")


def _secret() -> bytes | None:
    s = os.environ.get("BRINK_EMAIL_SECRET") or os.environ.get("SUPABASE_JWT_SECRET")
    return s.encode() if s else None


def enabled() -> bool:
    if not api_key():
        log.info("turn emails off: RESEND_API_KEY is not set")
        return False
    if not _secret():
        log.warning("turn emails off: set BRINK_EMAIL_SECRET (or SUPABASE_JWT_SECRET) to sign the stop links")
        return False
    return True


# ---------------------------------------------------------------------------------------------- stop links
def _sig(user_id: str, secret: bytes) -> str:
    mac = hmac.new(secret, f"brink-unsubscribe:v1:{user_id}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


def unsubscribe_token(user_id: str) -> str:
    secret = _secret()
    if not secret:
        raise RuntimeError("no signing secret: set BRINK_EMAIL_SECRET")
    uid = str(uuid.UUID(str(user_id)))
    return f"{uid}.{_sig(uid, secret)}"


def verify_unsubscribe_token(token: str | None) -> str | None:
    """The user id a stop-link token was signed for, or None if it is malformed or forged."""
    secret = _secret()
    if not secret or not token or "." not in token:
        return None
    uid, _, sig = str(token).strip().partition(".")
    try:
        uid = str(uuid.UUID(uid))
    except ValueError:
        return None
    return uid if hmac.compare_digest(sig, _sig(uid, secret)) else None


def unsubscribe_url(user_id: str) -> str:
    return f"{site_url()}/api/email/unsubscribe?t={unsubscribe_token(user_id)}"


def set_preference(conn, user_id: str, on: bool) -> None:
    conn.execute("update profiles set email_notifications = %s where id = %s", (on, user_id))


# ---------------------------------------------------------------------------------------------- the words
def phase_parts(name: str) -> tuple[str, int, str]:
    """'F2032R' -> ('F', 2032, 'R')."""
    return name[0], int(name[1:5]), name[5:] or "M"


def phase_label(name: str) -> str:
    season, year, _ = phase_parts(name)
    return f"{SEASONS.get(season, season)} {year}"


def record_label(rec: dict) -> str:
    label = f"{str(rec.get('season', '')).capitalize()} {rec.get('year')}"
    return label + (" retreats" if rec.get("kind") == "retreat" else "")


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def span(delta: dt.timedelta) -> str:
    """'18 hours', '2 hours', '45 minutes': rounded to the nearest hour from 90 minutes up."""
    minutes = max(0, delta.total_seconds() / 60)
    if minutes >= 90:
        return plural(int(math.floor(minutes / 60 + 0.5)), "hour")
    m = max(5, int(round(minutes / 5) * 5)) if minutes >= 5 else max(1, int(round(minutes)))
    return plural(m, "minute")


def when(t: dt.datetime) -> str:
    t = t.astimezone(dt.timezone.utc)
    return f"{t:%a} {t.day} {t:%b}, {t:%H:%M} UTC"


def _sentence(s: str) -> str:
    s = " ".join(str(s).split())
    return s if s.endswith((".", "!", "?", ")")) else s + "."


ORDER_RESULT_WORDS = {"bounce": "bounced", "cut": "cut", "void": "void", "dislodged": "dislodged",
                      "failed": "failed"}


def orders_summary(rec: dict, power: str) -> str | None:
    if rec.get("kind") != "movement":
        return None
    mine = [o for o in rec.get("orders", []) if o.get("power") == power]
    if not mine:
        return None
    label = record_label(rec)
    ok = sum(1 for o in mine if o.get("result") == "ok")
    if ok == len(mine):
        return f"All {plural(len(mine), 'order')} you gave in {label} succeeded." if len(mine) > 1 else \
            f"Your one order in {label} succeeded."
    rest = []
    for res, word in ORDER_RESULT_WORDS.items():
        n = sum(1 for o in mine if o.get("result") == res)
        if n:
            rest.append(f"{n} {'unit was' if n == 1 else 'units were'} dislodged" if res == "dislodged"
                        else f"{n} {word}")
    tail = ", ".join(rest[:-1]) + (" and " if len(rest) > 1 else "") + rest[-1]
    return f"In {label}, {ok} of your {len(mine)} orders succeeded; {tail}."


def pick_events(events: Iterable[str], power_name: str, adjective: str, limit: int) -> list[str]:
    """The events most about the player first (their name or adjective), then the Crisis, then the rest."""
    def rank(ev: str) -> int:
        if power_name in ev or adjective in ev:
            return 0
        return 1 if "Crisis" in ev or "Assembly" in ev else 2
    evs = [str(x) for x in events if str(x).strip()]
    return [_sentence(x) for x in sorted(evs, key=rank)[:max(0, limit)]]


def needs(gmap, pending: dict, power: str, state: dict | None) -> str:
    kind = pending["kind"]
    pos = pending.get("position") or {}
    if kind == "orders":
        n = sum(1 for u in pos.get("units", []) if u.get("power") == power)
        return f"Orders for {plural(n, 'unit')}" if n else "Your umbrella decisions"
    if kind == "retreats":
        n = sum(1 for u in pos.get("dislodged", []) if u.get("power") == power)
        return f"Retreat {plural(n, 'dislodged unit')}"
    if kind == "propose":
        return "Your proposal to the Assembly, as its chair (or none)"
    parts = []
    if pending.get("proposal"):
        parts.append(f"your vote on {pending['proposal'].removeprefix('PROPOSE ')}")
    change = 0
    if state is not None:
        from .. import session as S
        from ..game import Game
        g = Game.from_state(gmap, state)
        change = S._winter_powers(g)[1].get(power, (0, None))[0]
    if change > 0:
        parts.append(plural(change, "build"))
    elif change < 0:
        parts.append(plural(-change, "disband"))
    if gmap.nuclear(power):
        parts.append("your umbrellas")
    if not parts:
        return "Your Winter decisions"
    text = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return text[0].upper() + text[1:]


WHAT = {"orders": "orders", "retreats": "retreats", "propose": "proposal", "winter": "decisions"}
CTA = {"orders": "Enter your orders", "retreats": "Enter your retreats", "propose": "Make your proposal",
       "winter": "Enter your Winter orders"}
CARD = {"orders": "Your orders", "retreats": "Your retreats", "propose": "The Assembly", "winter": "Your Winter"}
RULE_NOTE = {
    "orders": "Saved orders count at the deadline, Ready or not. Units without orders hold.",
    "retreats": "Saved retreats count at the deadline. A dislodged unit without an order disbands.",
    "propose": "If nothing is proposed by the deadline, the Assembly has nothing to vote on this Winter.",
    "winter": "Saved orders count at the deadline. Without them you abstain, build nothing and keep your "
              "umbrellas; any disbands you owe are chosen for you.",
}


def track(first_year: int, last_year: int, phase: str) -> dict:
    season, year, _ = phase_parts(phase)
    lo = max(first_year, min(year - 1, last_year - TRACK_WINDOW + 1))
    hi = min(last_year, lo + TRACK_WINDOW - 1)
    cur = (year, HALF_ORDER.get(season, 0))
    years = []
    for y in range(lo, hi + 1):
        halves = []
        for s in "SFW":
            at = (y, HALF_ORDER[s])
            halves.append({"season": s, "state": "now" if at == cur else "past" if at < cur else "future"})
        years.append({"year": y, "halves": halves, "end": y == last_year})
    total = last_year - first_year + 1
    return {"years": years, "caption": f"Year {year - first_year + 1} of {total}",
            "first": first_year, "last": last_year}


def turn_context(gmap, game: dict, pending: dict, power: str, kind: str, *, now: dt.datetime,
                 last_record: dict | None, state: dict | None, humans: int, saved: int | None,
                 unsubscribe: str) -> dict:
    """Everything a turn email prints, from real game data only. kind: 'due' | 'reminder'."""
    info = gmap.powers[power]
    decision = pending["kind"]
    phase = pending["phase"]
    season, year, _ = phase_parts(phase)
    pos = pending.get("position") or {}
    site = site_url()
    game_url = f"{site}/#/g/{game['id']}"
    deadline = game["deadline"]
    left = deadline - now if deadline else dt.timedelta(0)
    need = needs(gmap, pending, power, state)

    # what happened: the Winter so far (when deciding in the Winter), else the last resolved phase
    happened: list[str] = []
    happened_label = ""
    happened_winter = False
    if decision in ("propose", "winter") and pending.get("events"):
        happened_label, happened_winter = f"Winter {year}", True
        happened = pick_events(pending["events"], info["name"], info.get("adjective", info["name"]), 3)
    elif last_record:
        happened_label = record_label(last_record)
        happened_winter = last_record.get("kind") == "winter"
        summary = orders_summary(last_record, power)
        happened = ([summary] if summary else []) + pick_events(
            last_record.get("events", []), info["name"], info.get("adjective", info["name"]), 3 - bool(summary))

    ownership = pos.get("ownership") or {}
    centres = sum(1 for o in ownership.values() if o == power)
    swing = sum(1 for s in gmap.swing_states() if ownership.get(s) == power)
    units = [u for u in pos.get("units", []) if u.get("power") == power]
    mil = sum(1 for u in units if u.get("type") in ("A", "F"))

    bots = len(gmap.power_order) - humans
    others = humans - 1
    seat_line = (f"You and {plural(others, 'other player')}" if others else "You alone") + \
        f" · {plural(bots, 'bot')} · {game['deadline_hours']}-hour deadlines"

    label = phase_label(phase)
    if kind == "reminder":
        lede = f"You haven't marked your {label} {WHAT[decision]} Ready yet."
        if saved:
            note = f"{plural(saved, 'order')} saved. They count at the deadline even if you don't mark Ready."
        else:
            note = "Nothing saved yet."
    else:
        note = None
        lede = {
            "orders": (f"{game['name']} has started. Write your first orders for {info['name']} on the board."
                       if not last_record else f"{happened_label or 'The last phase'} is resolved. "
                       f"Write your {label} orders for {info['name']} on the board."),
            "retreats": f"{info['name']} has units dislodged in {label}. Choose where they go.",
            "propose": f"{info['name']} chairs the Assembly this Winter: put one resolution to the vote, or none.",
            "winter": "The Winter is open: the Assembly vote, builds and disbands, and umbrellas.",
        }[decision]
    return {
        "kind": kind, "decision": decision, "power": power, "power_name": info["name"],
        "game_name": game["name"], "game_url": game_url, "site_url": site,
        "site_host": urlsplit(site).netloc or site, "unsubscribe_url": unsubscribe,
        "phase": phase, "phase_label": label, "phase_title": label,
        "season": season, "year": year, "lede": lede, "seat_line": seat_line,
        "need": need, "need_note": note, "cta": ("Finish your " + WHAT[decision]) if kind == "reminder" else CTA[decision],
        "card_title": CARD[decision], "rule_note": RULE_NOTE[decision],
        "deadline_abs": when(deadline) if deadline else "no deadline",
        "deadline_short": f"{deadline.astimezone(dt.timezone.utc):%a %H:%M} UTC" if deadline else "",
        "deadline_rel": f"in {span(left)}", "deadline_rel_short": span(left).upper(), "left": span(left),
        "track": track(int((gmap.start or {}).get("year", 2031)), game["last_year"], phase),
        "crisis": int(pos.get("crisis", 0)), "centres": centres,
        "centres_note": f"including {plural(swing, 'swing state')}" if swing else "all at home",
        "units": len(units), "units_note": f"{mil} military, {len(units) - mil} civil" if units else "",
        "happened": happened, "happened_label": happened_label, "happened_winter": happened_winter,
        "preheader": f"{need}. Deadline {when(deadline)}." if deadline else need,
    }


# ---------------------------------------------------------------------------------------------- sending
def _client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT)


def resend_batch(messages: list[dict]) -> list[tuple[str | None, str | None]]:
    """Send through Resend's batch endpoint; one (provider id, error) per message, in order. Never raises."""
    out: list[tuple[str | None, str | None]] = []
    key = api_key()
    for i in range(0, len(messages), BATCH_MAX):
        chunk = messages[i:i + BATCH_MAX]
        try:
            with _client() as c:
                r = c.post(f"{RESEND_URL}/emails/batch", json=chunk,
                           headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            if r.status_code >= 300:
                raise RuntimeError(f"Resend {r.status_code}: {r.text[:300]}")
            data = (r.json() or {}).get("data") or []
            ids = [d.get("id") if isinstance(d, dict) else None for d in data]
            out += [(ids[j] if j < len(ids) else None, None) for j in range(len(chunk))]
        except Exception as ex:   # the network, Resend, or its JSON: record it and carry on
            log.warning("turn emails: batch of %d failed: %s", len(chunk), ex)
            out += [(None, f"{type(ex).__name__}: {ex}"[:500])] * len(chunk)
    return out


def message(to: str, ctx: dict) -> dict:
    subject, html, text = T.turn_email(ctx)
    return {
        "from": sender(), "to": [to], "subject": subject, "html": html, "text": text,
        "headers": {"List-Unsubscribe": f"<{ctx['unsubscribe_url']}>",
                    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"},
        "tags": [{"name": "kind", "value": ctx["kind"]}, {"name": "decision", "value": ctx["decision"]}],
    }


def _claim(conn, game_id: str, step: int, kind: str, user_ids: list[str]) -> set[str]:
    """Insert the email_log rows and commit; the users this call won (others were already claimed)."""
    if not user_ids:
        return set()
    rows = conn.execute(
        "insert into email_log (user_id, game_id, step, kind) select u, %s, %s, %s from unnest(%s::uuid[]) as u "
        "on conflict (user_id, game_id, step, kind) do nothing returning user_id",
        (game_id, step, kind, user_ids)).fetchall()
    conn.commit()
    return {str(r["user_id"]) for r in rows}


def _record(conn, game_id: str, step: int, kind: str, results: list[tuple[str, str | None, str | None]]) -> None:
    for uid, pid, error in results:
        conn.execute(
            "update email_log set status = %s, provider_id = %s, error = %s, sent_at = case when %s then now() end "
            "where user_id = %s and game_id = %s and step = %s and kind = %s",
            ("failed" if error else "sent", pid, error, error is None, uid, game_id, step, kind))
    conn.commit()


def _candidates(conn, game: dict) -> list[dict]:
    """Humans in the open decision who haven't marked Ready and still want turn emails, with addresses."""
    return conn.execute(
        "select s.power, s.user_id, u.email, sub.orders, coalesce(sub.ready, false) as ready "
        "from game_seats s join profiles p on p.id = s.user_id join auth.users u on u.id = s.user_id "
        "left join submissions sub on sub.game_id = s.game_id and sub.power = s.power and sub.step = %s "
        "where s.game_id = %s and s.power = any(%s) and p.email_notifications and u.email is not null "
        "and u.email <> ''",
        (game["step"], game["id"], list((game.get("pending") or {}).get("powers") or []))).fetchall()


def _deliver(conn, game: dict, kind: str, now: dt.datetime) -> dict:
    from . import service as svc
    pend = game.get("pending")
    if game.get("status") != "active" or not pend:
        return {"sent": 0, "failed": 0}
    rows = [r for r in _candidates(conn, game) if not r["ready"]]
    humans = conn.execute("select count(*) as n from game_seats where game_id = %s and user_id is not null",
                          (game["id"],)).fetchone()["n"]
    if humans == 1:
        # a game against bots alone waits for nobody but you, and resolves the moment you're Ready:
        # turn emails are for games with other people in them
        rows = []
    won = _claim(conn, str(game["id"]), game["step"], kind, [str(r["user_id"]) for r in rows])
    rows = [r for r in rows if str(r["user_id"]) in won]
    if not rows:
        return {"sent": 0, "failed": 0}
    gmap = svc.game_map()
    last = conn.execute("select record from game_phases where game_id = %s order by idx desc limit 1",
                        (game["id"],)).fetchone()
    state = svc.load_state(conn, game) if pend.get("kind") == "winter" else None
    conn.commit()
    msgs, who = [], []
    for r in rows:
        try:
            ctx = turn_context(gmap, game, pend, r["power"], kind, now=now, last_record=last["record"] if last else None,
                               state=state, humans=humans, saved=len(r["orders"] or []) if r["orders"] is not None else None,
                               unsubscribe=unsubscribe_url(str(r["user_id"])))
            msgs.append(message(r["email"], ctx))
            who.append(str(r["user_id"]))
        except Exception as ex:
            log.exception("turn emails: rendering for %s failed", r["power"])
            _record(conn, str(game["id"]), game["step"], kind, [(str(r["user_id"]), None, f"render: {ex}"[:500])])
    results = resend_batch(msgs) if msgs else []
    _record(conn, str(game["id"]), game["step"], kind, [(u, pid, err) for u, (pid, err) in zip(who, results)])
    failed = sum(1 for _, err in results if err)
    return {"sent": len(results) - failed, "failed": failed}


def send_due(conn, game_id: str, now: dt.datetime | None = None) -> dict:
    """'Your orders are due' for the decision point the game is now at. Call after the advance has been
    committed: it commits as it goes. Never raises."""
    if not enabled():
        return {"sent": 0, "failed": 0, "skipped": "disabled"}
    try:
        conn.commit()
        g = conn.execute("select * from games where id = %s", (game_id,)).fetchone()
        if not g:
            return {"sent": 0, "failed": 0}
        return _deliver(conn, g, "due", now or dt.datetime.now(dt.timezone.utc))
    except Exception:
        log.exception("turn emails: due emails for %s failed", game_id)
        _safe_rollback(conn)
        return {"sent": 0, "failed": 0, "error": True}


def send_reminders(conn, now: dt.datetime | None = None, budget_s: float = 15.0) -> dict:
    """Deadline reminders for every game whose deadline is inside its reminder window. Never raises."""
    if not enabled():
        return {"games": 0, "sent": 0, "failed": 0, "skipped": "disabled"}
    import time
    t0 = time.monotonic()
    now = now or dt.datetime.now(dt.timezone.utc)
    sent = failed = 0
    try:
        conn.commit()
        games = conn.execute(
            "select g.* from games g where g.status = 'active' and g.pending is not null and g.deadline > %s "
            "and g.deadline <= %s + case when g.deadline_hours >= 24 then interval '6 hours' else interval '3 hours' end "
            "and exists (select 1 from game_seats s join profiles p on p.id = s.user_id "
            "  left join submissions sub on sub.game_id = s.game_id and sub.power = s.power and sub.step = g.step "
            "  where s.game_id = g.id and p.email_notifications and g.pending->'powers' ? s.power "
            "  and not coalesce(sub.ready, false) and not exists (select 1 from email_log l where l.user_id = s.user_id "
            "  and l.game_id = g.id and l.step = g.step and l.kind = 'reminder')) "
            "order by g.deadline limit 50", (now, now)).fetchall()
        conn.commit()
    except Exception:
        log.exception("turn emails: finding reminders failed")
        _safe_rollback(conn)
        return {"games": 0, "sent": 0, "failed": 0, "error": True}
    for g in games:
        if time.monotonic() - t0 > budget_s:
            break
        try:
            r = _deliver(conn, g, "reminder", now)
            sent += r["sent"]
            failed += r["failed"]
        except Exception:
            log.exception("turn emails: reminders for %s failed", g["id"])
            _safe_rollback(conn)
    return {"games": len(games), "sent": sent, "failed": failed}


def _safe_rollback(conn) -> None:
    try:
        conn.rollback()
    except Exception:
        pass
