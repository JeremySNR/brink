"""Hosted multiplayer (brink.hosted) against a real Postgres: an embedded server from `pgserver` with the
Supabase migration applied. Auth uses HS256 tokens signed with a test secret, as Supabase's legacy secret does."""
from __future__ import annotations

import time
import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("pgserver")
jwt = pytest.importorskip("jwt")

from fastapi.testclient import TestClient  # noqa: E402

from brink.hosted import localdb  # noqa: E402
from brink.hosted import service as svc  # noqa: E402
from brink.hosted.db import connect  # noqa: E402

SECRET = "test-secret-for-brink-hosted-tests-0123456789"
TABLES = "notifications, messages, submissions, game_seats, game_phases, games, profiles"


@pytest.fixture(scope="session")
def db_url(tmp_path_factory):
    srv, url = localdb.start(tmp_path_factory.mktemp("pg"))
    yield url
    srv.cleanup()


@pytest.fixture()
def client(db_url, monkeypatch):
    monkeypatch.setenv("BRINK_DATABASE_URL", db_url)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.delenv("BRINK_DEV_AUTH", raising=False)
    monkeypatch.setattr(svc, "BOT_KIND", "random")          # fast bots; the engine is tested elsewhere
    with connect(db_url) as conn:
        conn.execute(f"truncate {TABLES} cascade")
        conn.execute("delete from auth.users")
    from brink.hosted.app import app
    return TestClient(app)


def token(uid: str, email: str = "x@example.com", aud: str = "authenticated", exp: int | None = None,
          secret: str = SECRET) -> str:
    claims = {"sub": uid, "email": email, "aud": aud, "role": "authenticated",
              "exp": exp if exp is not None else int(time.time()) + 3600}
    return jwt.encode(claims, secret, algorithm="HS256")


class Player:
    def __init__(self, client, db_url, email):
        self.c = client
        self.id = str(uuid.uuid4())
        with connect(db_url) as conn:           # what Supabase Auth does on sign-up (the trigger makes the profile)
            conn.execute("insert into auth.users (id, email) values (%s, %s)", (self.id, email))
        self.h = {"Authorization": f"Bearer {token(self.id, email)}"}

    def get(self, path, **kw):
        return self.c.get(path, headers=self.h, **kw)

    def post(self, path, json=None):
        return self.c.post(path, headers=self.h, json=json or {})

    def ok(self, method, path, json=None):
        r = self.get(path) if method == "GET" else self.post(path, json)
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture()
def players(client, db_url):
    return [Player(client, db_url, e) for e in ("ada@example.com", "bob@example.com", "cy@example.com")]


def new_game(p: Player, **kw) -> dict:
    body = {"name": "Friday game", "visibility": "public", "deadline_hours": 24, "press": "full",
            "last_year": 2034, "power": "USA"} | kw
    return p.ok("POST", "/api/games", body)


def started(players, n=2, **kw):
    a = players[0]
    g = new_game(a, **kw)
    gid = g["game"]["id"]
    for p, power in zip(players[1:n], ["CHN", "EU"]):
        p.ok("POST", f"/api/games/{gid}/join", {"power": power, "code": g["game"]["invite_code"]})
    return gid, a.ok("POST", f"/api/games/{gid}/start")


def set_deadline_past(db_url, gid):
    with connect(db_url) as conn:
        conn.execute("update games set deadline = now() - interval '1 minute' where id = %s", (gid,))


# ---------------------------------------------------------------------------------------------- auth
def test_auth_rejections(client, players):
    a = players[0]
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    for bad in (token(a.id, secret="another-secret-entirely-0123456789abc"), token(a.id, aud="anon"),
                token(a.id, exp=int(time.time()) - 10), "dev:ada"):
        r = client.get("/api/me", headers={"Authorization": f"Bearer {bad}"})
        assert r.status_code == 401 and "error" in r.json()
    assert a.ok("GET", "/api/me")["display_name"] == "ada"


def test_profile_names(players):
    a, b, _ = players
    assert a.ok("GET", "/api/me")["display_name"] == "ada"
    r = a.c.put("/api/me", headers=a.h, json={"display_name": "Ada L"})
    assert r.status_code == 200 and r.json()["display_name"] == "Ada L"
    r = b.c.put("/api/me", headers=b.h, json={"display_name": "ada l"})
    assert r.status_code == 409
    assert b.c.put("/api/me", headers=b.h, json={"display_name": "x"}).status_code == 400


# ---------------------------------------------------------------------------------------------- forming
def test_create_join_leave_lobby_invite(client, players):
    a, b, c = players
    g = new_game(a)
    gid = g["game"]["id"]
    assert g["game"]["status"] == "forming" and g["you"] == {"power": "USA", "creator": True, "signed_in": True}
    lobby = client.get("/api/lobby").json()
    assert [x["id"] for x in lobby] == [gid] and lobby[0]["players"] == 1
    priv = new_game(b, visibility="private", power=None)
    assert priv["you"]["power"] in svc.powers()
    assert [x["id"] for x in client.get("/api/lobby").json()] == [gid]          # private games stay out
    code = priv["game"]["invite_code"]
    assert client.get(f"/api/invite/{code}").json()["id"] == priv["game"]["id"]
    assert client.get("/api/invite/NOPE").status_code == 404
    assert c.post(f"/api/games/{priv['game']['id']}/join", {}).status_code == 403
    assert c.post(f"/api/games/{priv['game']['id']}/join", {"code": code})
    # public game: taken seat, switch seat, leave
    assert b.post(f"/api/games/{gid}/join", {"power": "USA"}).status_code == 409
    assert b.ok("POST", f"/api/games/{gid}/join", {"power": "RUS"})["you"]["power"] == "RUS"
    assert b.ok("POST", f"/api/games/{gid}/join", {"power": "IND"})["you"]["power"] == "IND"
    seats = {s["power"]: s for s in a.ok("GET", f"/api/games/{gid}")["seats"]}
    assert seats["IND"]["name"] == "bob" and seats["RUS"]["name"] is None and not seats["RUS"]["bot"]
    assert "invite_code" not in client.get(f"/api/games/{gid}").json()["game"]         # spectators don't get it
    assert b.ok("POST", f"/api/games/{gid}/leave")["you"]["power"] is None
    assert a.post(f"/api/games/{gid}/leave").status_code == 409                        # creator cancels instead
    assert b.post(f"/api/games/{gid}/start").status_code == 403
    assert any(n["kind"] == "joined" for n in a.ok("GET", "/api/notifications")["items"])


def test_start_seats_bots_and_sets_deadline(players):
    gid, g = started(players, 2)
    assert g["game"]["status"] == "active"
    assert g["pending"]["kind"] == "orders" and {"USA", "CHN"} <= set(g["pending"]["powers"])
    bots = [s["power"] for s in g["seats"] if s["bot"]]
    assert sorted(bots) == sorted(set(svc.powers()) - {"USA", "CHN"})
    assert g["decision"]["options"]["USA"]["units"] and g["phase_count"] == 0
    assert g["game"]["deadline"] is not None
    assert players[0].post(f"/api/games/{gid}/start").status_code == 409
    assert players[2].post(f"/api/games/{gid}/join", {}).status_code == 409


# ---------------------------------------------------------------------------------------------- playing
def test_submit_ready_advances_when_everyone_is_ready(players):
    a, b, c = players
    gid, g = started(players, 2)
    step = g["game"]["step"]
    usa_unit = g["decision"]["options"]["USA"]["units"][0]["unit"]
    order = f"{usa_unit['type']} {usa_unit['at']} H"
    r = a.ok("POST", f"/api/games/{gid}/validate", {"orders": [order, "A XXX - YYY"]})["results"]
    assert r[0]["ok"] and not r[1]["ok"]
    g = a.ok("POST", f"/api/games/{gid}/submit", {"step": step, "orders": [order], "ready": False})
    assert g["game"]["step"] == step and g["submission"]["orders"] == [order] and not g["submission"]["ready"]
    g = a.ok("POST", f"/api/games/{gid}/submit", {"step": step, "orders": [order], "ready": True})
    assert g["game"]["step"] == step                                      # CHN isn't ready yet
    seats = {s["power"]: s for s in b.ok("GET", f"/api/games/{gid}")["seats"]}
    assert seats["USA"]["ready"] is True and seats["CHN"]["ready"] is False
    assert "orders" not in seats["USA"]                                   # ready ticks, never orders
    mine = b.ok("GET", "/api/games/mine")
    assert mine[0]["my_turn"] is True and mine[0]["power"] == "CHN"
    assert a.ok("GET", "/api/games/mine")[0]["my_turn"] is False
    g = b.ok("POST", f"/api/games/{gid}/submit", {"step": step, "orders": [], "ready": True})
    assert g["game"]["step"] > step and g["phase_count"] >= 1
    first = g["phases"][0]
    assert first["name"] == "S2031M" and order in [o["text"] for o in first["orders"] if o["power"] == "USA"]
    assert b.post(f"/api/games/{gid}/submit", {"step": step, "orders": [], "ready": True}).status_code == 409
    assert c.post(f"/api/games/{gid}/submit", {"step": g["game"]["step"], "orders": []}).status_code == 403
    since = a.ok("GET", f"/api/games/{gid}?since={g['phase_count']}")
    assert since["phases"] == [] and since["phase_count"] == g["phase_count"]
    assert any(n["kind"] == "turn" for n in a.ok("GET", "/api/notifications")["items"])


def test_deadline_tick_civil_disorder_and_missed(client, db_url, players):
    a, b, _ = players
    gid, g = started(players, 2)
    step = g["game"]["step"]
    unit = g["decision"]["options"]["USA"]["units"][0]["unit"]
    order = f"{unit['type']} {unit['at']} H"
    a.ok("POST", f"/api/games/{gid}/submit", {"step": step, "orders": [order], "ready": False})  # saved, not ready
    assert client.post("/api/cron/tick").json()["advanced"] == []                                # not due yet
    set_deadline_past(db_url, gid)
    r = client.get("/api/cron/tick").json()
    assert r["advanced"] == [gid]
    assert client.get("/api/cron/tick").json()["advanced"] == []                                 # idempotent
    g = a.ok("GET", f"/api/games/{gid}")
    assert g["game"]["step"] > step
    seats = {s["power"]: s for s in g["seats"]}
    assert seats["USA"]["missed"] == 0 and seats["CHN"]["missed"] == 1
    usa = [o for o in g["phases"][0]["orders"] if o["power"] == "USA"]
    assert order in [o["text"] for o in usa]                     # a saved draft counts at the deadline


def test_lazy_get_advances_past_deadline(client, db_url, players):
    gid, g = started(players, 2)
    set_deadline_past(db_url, gid)
    g2 = client.get(f"/api/games/{gid}").json()                  # anonymous spectator triggers it too
    assert g2["game"]["step"] > g["game"]["step"] and g2["phase_count"] >= 1


def test_optimistic_lock_conflict(db_url, players, monkeypatch):
    gid, g = started(players, 2)
    set_deadline_past(db_url, gid)
    real = svc.S.advance
    raced = {}

    def racing_advance(*args, **kw):
        out = real(*args, **kw)
        if not raced:                                            # another request advances first
            raced["x"] = True
            with connect(db_url) as other:
                raced["won"] = svc.advance_game(other, gid, "deadline")
        return out

    monkeypatch.setattr(svc.S, "advance", racing_advance)
    with connect(db_url) as conn:
        assert svc.advance_game(conn, gid, "deadline") is False   # lost the race: wrote nothing
    assert raced["won"] is True
    with connect(db_url) as conn:
        row = conn.execute("select step from games where id = %s", (gid,)).fetchone()
        n = conn.execute("select count(*) as n from game_phases where game_id = %s", (gid,)).fetchone()["n"]
        missed = conn.execute("select missed from game_seats where game_id = %s and power = 'USA'", (gid,)).fetchone()
    assert row["step"] > g["game"]["step"] and n >= 1 and missed["missed"] == 1   # counted once


# ---------------------------------------------------------------------------------------------- press
def test_messages_visible_to_recipients_only(players):
    a, b, c = players
    gid, _ = started(players, 3)
    r = a.ok("POST", f"/api/games/{gid}/messages", {"recipients": ["CHN"], "body": "Hold Brazil?"})
    assert r["recipients"] == ["CHN", "USA"] and r["sender_power"] == "USA"
    assert [m["body"] for m in b.ok("GET", f"/api/games/{gid}/messages")["messages"]] == ["Hold Brazil?"]
    assert c.ok("GET", f"/api/games/{gid}/messages")["messages"] == []
    assert [m["body"] for m in a.ok("GET", f"/api/games/{gid}/messages")["messages"]] == ["Hold Brazil?"]
    assert a.post(f"/api/games/{gid}/messages", {"recipients": ["RUS"], "body": "hi"}).status_code == 400  # bot
    assert a.post(f"/api/games/{gid}/messages", {"recipients": ["XYZ"], "body": "hi"}).status_code == 400
    assert a.post(f"/api/games/{gid}/messages", {"recipients": ["CHN"], "body": "x" * 2001}).status_code == 400
    last = b.ok("GET", f"/api/games/{gid}/messages")["messages"][-1]["id"]
    assert b.ok("GET", f"/api/games/{gid}/messages?after={last}")["messages"] == []
    assert any(n["kind"] == "message" for n in b.ok("GET", "/api/notifications")["items"])


def test_no_press_games(players):
    a, b, _ = players
    gid, _ = started(players, 2, press="none")
    assert a.post(f"/api/games/{gid}/messages", {"recipients": ["CHN"], "body": "hi"}).status_code == 403
    assert b.get(f"/api/games/{gid}/messages").status_code == 403


def test_notifications_read(players):
    a, b, _ = players
    started(players, 2)
    n = b.ok("GET", "/api/notifications")
    assert n["unread"] >= 1
    assert b.ok("POST", "/api/notifications/read", {})["unread"] == 0


def test_game_runs_to_the_end(client, db_url, players):
    """Deadline after deadline with nobody ordering: civil disorder until the game is over."""
    gid, g = started(players, 2, last_year=2032)
    for _ in range(40):
        if g["game"]["status"] == "finished":
            break
        set_deadline_past(db_url, gid)
        client.post("/api/cron/tick")
        g = players[0].ok("GET", f"/api/games/{gid}")
    assert g["game"]["status"] == "finished" and g["result"] and g["pending"] is None
    assert g["phases"][-1]["year"] == 2032


def test_migration_is_idempotent(db_url):
    localdb.apply_migrations(db_url)
    with connect(db_url) as conn:
        rls = conn.execute("select relname from pg_class where relnamespace = 'public'::regnamespace "
                           "and relkind = 'r' and not relrowsecurity").fetchall()
        policies = conn.execute("select count(*) as n from pg_policies where schemaname = 'public'").fetchone()
    assert rls == [] and policies["n"] == 0          # RLS on everywhere, no policies: the Data API sees nothing


def test_dsn_cleaning_drops_parameters_libpq_rejects():
    from brink.hosted.db import clean_dsn
    url = "postgres://postgres.ref:pw@aws-0-eu-west-1.pooler.supabase.com:6543/postgres?sslmode=require&supa=base-pooler.x"
    assert clean_dsn(url) == "postgres://postgres.ref:pw@aws-0-eu-west-1.pooler.supabase.com:6543/postgres?sslmode=require"
    assert clean_dsn("postgres://u@h/db?pgbouncer=true&connect_timeout=5").endswith("/db?connect_timeout=5")


def test_dev_auth_cannot_run_on_vercel(monkeypatch):
    from brink.hosted import auth
    monkeypatch.setenv("BRINK_DEV_AUTH", "1")
    monkeypatch.delenv("VERCEL", raising=False)
    assert auth.verify_token("dev:ada").dev
    monkeypatch.setenv("VERCEL", "1")
    with pytest.raises(auth.AuthError):
        auth.verify_token("dev:ada")
