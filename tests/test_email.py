"""Turn emails (brink.hosted.email) against the embedded Postgres, with Resend replaced by an httpx mock."""
from __future__ import annotations

import datetime as dt
import json
import threading

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("pgserver")
httpx = pytest.importorskip("httpx")

from brink.hosted import email as mail  # noqa: E402
from brink.hosted import email_templates as T  # noqa: E402
from brink.hosted.db import connect  # noqa: E402
from test_hosted import client, db_url, players, set_deadline_past, started  # noqa: E402,F401


class Resend:
    """Collects what would have gone to Resend; `fail` makes every call fail (a status or an exception)."""

    def __init__(self):
        self.calls: list[list[dict]] = []
        self.fail: int | Exception | None = None
        self.lock = threading.Lock()

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/emails/batch"
        assert request.headers["authorization"] == "Bearer re_test_key"
        if isinstance(self.fail, Exception):
            raise self.fail
        body = json.loads(request.content)
        with self.lock:
            self.calls.append(body)
        if self.fail:
            return httpx.Response(self.fail, json={"message": "nope"})
        return httpx.Response(200, json={"data": [{"id": f"em_{len(self.calls)}_{i}"} for i in range(len(body))]})

    @property
    def sent(self) -> list[dict]:
        return [m for batch in self.calls for m in batch]

    def to(self) -> list[str]:
        return sorted(m["to"][0] for m in self.sent)


@pytest.fixture()
def resend(monkeypatch):
    r = Resend()
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("BRINK_EMAIL_SECRET", "email-secret-for-tests")
    monkeypatch.setenv("BRINK_SITE_URL", "https://brink.example")
    monkeypatch.setattr(mail, "_client", lambda: httpx.Client(transport=httpx.MockTransport(r.handler)))
    return r


def log_rows(db_url, gid):
    with connect(db_url) as conn:
        return conn.execute("select l.*, u.email from email_log l join auth.users u on u.id = l.user_id "
                            "where game_id = %s order by u.email, step, kind", (gid,)).fetchall()


def game_row(db_url, gid):
    with connect(db_url) as conn:
        return conn.execute("select * from games where id = %s", (gid,)).fetchone()


# ---------------------------------------------------------------------------------------------- due
def test_due_email_on_phase_open_for_humans_only(resend, db_url, players):
    gid, g = started(players, 2)                                # ada USA, bob CHN, five bots
    assert resend.to() == ["ada@example.com", "bob@example.com"]
    assert len(resend.calls) == 1                               # one batch
    m = next(m for m in resend.sent if m["to"] == ["ada@example.com"])
    assert m["from"] == "Brink <turns@brinkgame.app>"
    assert m["subject"] == "Friday game: Spring 2031 orders due by " + \
        f"{g['game']['deadline'] and dt.datetime.fromisoformat(g['game']['deadline']).astimezone(dt.timezone.utc):%a %H:%M} UTC"
    assert f"https://brink.example/#/g/{gid}" in m["html"] and f"https://brink.example/#/g/{gid}" in m["text"]
    assert "United States" in m["html"] and ">USA<" in m["html"]
    unsub = m["headers"]["List-Unsubscribe"]
    assert unsub.startswith("<https://brink.example/api/email/unsubscribe?t=") and unsub.endswith(">")
    assert m["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert unsub[1:-1] in m["html"] and unsub[1:-1] in m["text"]
    assert "<script" not in m["html"].lower()
    rows = log_rows(db_url, gid)
    assert [(r["email"], r["kind"], r["status"]) for r in rows] == [
        ("ada@example.com", "due", "sent"), ("bob@example.com", "due", "sent")]
    assert all(r["provider_id"] and r["sent_at"] for r in rows)


def test_due_email_after_advance_and_not_for_a_ready_player(resend, db_url, players):
    a, b, _ = players
    gid, g = started(players, 2)
    step = g["game"]["step"]
    a.ok("POST", f"/api/games/{gid}/submit", {"step": step, "orders": [], "ready": True})
    b.ok("POST", f"/api/games/{gid}/submit", {"step": step, "orders": [], "ready": True})   # advances
    new = game_row(db_url, gid)
    assert new["step"] > step
    due_new = [r for r in log_rows(db_url, gid) if r["step"] == new["step"]]
    assert sorted(r["email"] for r in due_new) == sorted(
        e for e, p in (("ada@example.com", "USA"), ("bob@example.com", "CHN")) if p in new["pending"]["powers"])
    # a player already Ready for the open step is skipped (and nobody is emailed twice)
    with connect(db_url) as conn:
        conn.execute("delete from email_log where game_id = %s and step = %s", (gid, new["step"]))
        conn.execute("insert into submissions (game_id, power, step, orders, ready) values (%s, 'USA', %s, '[]', true) "
                     "on conflict (game_id, power, step) do update set ready = true", (gid, new["step"]))
    before = len(resend.sent)
    with connect(db_url) as conn:
        mail.send_due(conn, gid)
        mail.send_due(conn, gid)
    sent = resend.sent[before:]
    assert [m["to"] for m in sent] == ([["bob@example.com"]] if "CHN" in new["pending"]["powers"] else [])


def test_solo_games_send_no_turn_emails(resend, db_url, players):
    a = players[0]
    gid, g = started(players, 1)                                # ada alone against six bots
    a.ok("POST", f"/api/games/{gid}/submit", {"step": g["game"]["step"], "orders": [], "ready": True})
    assert game_row(db_url, gid)["step"] > g["game"]["step"]   # resolved at once
    set_deadline_past(db_url, gid)
    with connect(db_url) as conn:
        mail.send_due(conn, gid)
        mail.send_reminders(conn, now=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30))
    assert resend.sent == []


def test_concurrent_senders_send_once(resend, db_url, players, monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY")
    gid, _ = started(players, 2)                                # no emails at start: no key
    assert resend.sent == [] and log_rows(db_url, gid) == []
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    go = threading.Barrier(4)

    def worker():
        with connect(db_url) as conn:
            go.wait()
            mail.send_due(conn, gid)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert resend.to() == ["ada@example.com", "bob@example.com"]


def test_no_key_is_a_no_op(db_url, players, monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    calls = []
    monkeypatch.setattr(mail, "_client", lambda: calls.append(1))
    gid, _ = started(players, 2)
    with connect(db_url) as conn:
        assert mail.send_due(conn, gid)["skipped"] == "disabled"
        assert mail.send_reminders(conn)["skipped"] == "disabled"
    assert calls == [] and log_rows(db_url, gid) == []


def test_resend_failure_does_not_break_advancing(resend, db_url, players):
    a, b, _ = players
    resend.fail = 500
    gid, g = started(players, 2)
    assert g["game"]["status"] == "active"
    assert {r["status"] for r in log_rows(db_url, gid)} == {"failed"}
    assert all("500" in r["error"] for r in log_rows(db_url, gid))
    resend.fail = httpx.ConnectError("no route to Resend")
    step = g["game"]["step"]
    a.ok("POST", f"/api/games/{gid}/submit", {"step": step, "orders": [], "ready": True})
    g2 = b.ok("POST", f"/api/games/{gid}/submit", {"step": step, "orders": [], "ready": True})
    assert g2["game"]["step"] > step                            # the advance happened anyway
    later = [r for r in log_rows(db_url, gid) if r["step"] == g2["game"]["step"]]
    assert later and all(r["status"] == "failed" and "ConnectError" in r["error"] for r in later)


# ---------------------------------------------------------------------------------------------- reminders
def _set_deadline(db_url, gid, delta, hours=None):
    with connect(db_url) as conn:
        conn.execute("update games set deadline = now() + %s, deadline_hours = coalesce(%s, deadline_hours) "
                     "where id = %s", (delta, hours, gid))


@pytest.mark.parametrize("hours, inside, outside", [(24, 5.5, 6.5), (48, 5.9, 7), (12, 2.5, 3.5)])
def test_reminder_window(resend, client, db_url, players, hours, inside, outside):
    a, _, _ = players
    gid, g = started(players, 2, deadline_hours=hours)
    a.ok("POST", f"/api/games/{gid}/submit", {"step": g["game"]["step"], "orders": [], "ready": True})  # ada ready
    resend.calls.clear()
    _set_deadline(db_url, gid, dt.timedelta(hours=outside))
    assert client.post("/api/cron/tick").json()["reminders"]["sent"] == 0
    _set_deadline(db_url, gid, dt.timedelta(hours=inside))
    r = client.post("/api/cron/tick").json()
    assert r["reminders"]["sent"] == 1 and resend.to() == ["bob@example.com"]       # not the ready player
    assert client.post("/api/cron/tick").json()["reminders"]["sent"] == 0           # once per player per step
    m = resend.sent[0]
    assert "left" in m["subject"] and "Not ready yet" in m["html"] and "Nothing saved yet." in m["text"]
    assert [x["kind"] for x in log_rows(db_url, gid) if x["kind"] == "reminder"] == ["reminder"]


def test_reminder_window_rule():
    assert mail.reminder_window(12) == dt.timedelta(hours=3)
    assert mail.reminder_window(24) == mail.reminder_window(72) == dt.timedelta(hours=6)


def test_concurrent_ticks_send_one_reminder(resend, db_url, players):
    gid, _ = started(players, 2)
    resend.calls.clear()
    _set_deadline(db_url, gid, dt.timedelta(hours=2))
    go = threading.Barrier(3)

    def worker():
        with connect(db_url) as conn:
            go.wait()
            mail.send_reminders(conn)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert resend.to() == ["ada@example.com", "bob@example.com"]


# ---------------------------------------------------------------------------------------------- opting out
def test_opt_out_is_respected(resend, db_url, players):
    a, b, _ = players
    assert a.ok("GET", "/api/me")["email_notifications"] is True
    r = a.c.put("/api/me", headers=a.h, json={"email_notifications": False})
    assert r.status_code == 200 and r.json()["email_notifications"] is False and r.json()["display_name"] == "ada"
    assert a.c.put("/api/me", headers=a.h, json={"email_notifications": "no"}).status_code == 400
    gid, _ = started(players, 2)
    assert resend.to() == ["bob@example.com"]
    resend.calls.clear()
    _set_deadline(db_url, gid, dt.timedelta(hours=1))
    with connect(db_url) as conn:
        mail.send_reminders(conn)
    assert resend.to() == ["bob@example.com"]


def test_unsubscribe_token_and_endpoint(resend, client, players):
    a, _, _ = players
    tok = mail.unsubscribe_token(a.id)
    assert mail.verify_unsubscribe_token(tok) == a.id
    uid, _, sig = tok.partition(".")
    other = players[1].id
    for forged in (f"{other}.{sig}", f"{uid}.{sig[:-2]}AA", f"{uid}.", uid, "", "nonsense.x", None):
        assert mail.verify_unsubscribe_token(forged) is None
    r = client.get(f"/api/email/unsubscribe?t={other}.{sig}")
    assert r.status_code == 400 and "doesn" in r.text
    r = client.get(f"/api/email/unsubscribe?t={tok}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html") and "stopped" in r.text
    assert a.ok("GET", "/api/me")["email_notifications"] is False
    r = client.post(f"/api/email/unsubscribe?t={tok}", content="action=resubscribe",
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200 and a.ok("GET", "/api/me")["email_notifications"] is True
    r = client.post(f"/api/email/unsubscribe?t={tok}", content="List-Unsubscribe=One-Click",
                    headers={"Content-Type": "application/x-www-form-urlencoded"})        # RFC 8058 one-click
    assert r.status_code == 200 and a.ok("GET", "/api/me")["email_notifications"] is False


def test_unsubscribe_secret_falls_back_to_the_jwt_secret(monkeypatch):
    monkeypatch.delenv("BRINK_EMAIL_SECRET", raising=False)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "jwt-secret")
    uid = "0b9e1d2c-4a5f-4c3e-8d7a-1e2f3a4b5c6d"
    tok = mail.unsubscribe_token(uid)
    assert mail.verify_unsubscribe_token(tok) == uid
    monkeypatch.setenv("BRINK_EMAIL_SECRET", "a-different-secret")
    assert mail.verify_unsubscribe_token(tok) is None


# ---------------------------------------------------------------------------------------------- sending
def test_batch_payload_shape_and_chunks(resend):
    msgs = [{"from": "Brink <x@y>", "to": [f"p{i}@example.com"], "subject": "s", "html": "<p>h</p>", "text": "t"}
            for i in range(150)]
    out = mail.resend_batch(msgs)
    assert [len(c) for c in resend.calls] == [100, 50]
    assert resend.calls[0][0] == msgs[0] and len(out) == 150 and all(pid and not err for pid, err in out)


def test_templates_render_from_real_state(monkeypatch):
    """Every decision kind renders from an engine game: real counts, no unfilled fields."""
    monkeypatch.setenv("BRINK_EMAIL_SECRET", "s")
    from brink.hosted.email_preview import contexts
    ctxs = contexts()
    assert {"due-orders", "due-retreats", "due-propose", "due-winter", "reminder-orders"} <= set(ctxs)
    for name, ctx in ctxs.items():
        subject, html, text = T.turn_email(ctx)
        assert "Atlantic Grudge" in subject and "{" not in subject and "None" not in text
        assert html.count("<table") == html.count("</table>") and "max-width:600px" in html
        assert ctx["power"] in html and ctx["need"] in text
    assert ctxs["due-orders"]["need"].startswith("Orders for ")
    assert ctxs["due-retreats"]["need"].startswith("Retreat ")
    assert "6 hours left" in T.turn_email(ctxs["reminder-orders"])[0]


def test_supabase_templates_are_current():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "supabase" / "templates"
    for kind in T.AUTH:
        html = T.auth_email(kind)[1]
        assert (root / f"{kind}.html").read_text() == html, f"regenerate supabase/templates/{kind}.html"
        assert "{{ .ConfirmationURL }}" in html and "{{ .Email }}" in html
