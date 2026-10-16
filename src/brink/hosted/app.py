"""The hosted JSON API (FastAPI). Served on Vercel by api/index.py; locally with
`BRINK_DEV_AUTH=1 uvicorn brink.hosted.app:app` (see docs/HOSTING.md).

Every endpoint is JSON. Signed-in endpoints take `Authorization: Bearer <Supabase access token>`.
Errors are `{"error": "<message>"}` with a 4xx/5xx status.
"""
from __future__ import annotations

import datetime as dt
import decimal
import json
import logging
import os
import uuid
from typing import Callable
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from .. import tutorial, workshop
from . import email as mail
from . import email_templates as T
from . import service as svc
from .auth import AuthError, User, bearer, dev_auth_enabled, verify_token
from .db import connect

log = logging.getLogger("brink.hosted")
app = FastAPI(title="Brink hosted API", docs_url=None, redoc_url=None, openapi_url=None)


def _default(o):
    if isinstance(o, (dt.datetime, dt.date)):
        return o.isoformat()
    if isinstance(o, uuid.UUID):
        return str(o)
    if isinstance(o, decimal.Decimal):
        return float(o)
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def ok(obj, status: int = 200) -> Response:
    return Response(json.dumps(obj, default=_default, separators=(",", ":")), status_code=status,
                    media_type="application/json", headers={"Cache-Control": "no-store"})


def err(status: int, message: str) -> Response:
    return ok({"error": message}, status)


async def _body(req: Request) -> dict:
    raw = await req.body()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        raise svc.bad("the request body is not JSON")
    if not isinstance(data, dict):
        raise svc.bad("the request body must be a JSON object")
    return data


def _user(req: Request, required: bool = True) -> User | None:
    token = bearer(req.headers.get("authorization"))
    if not token:
        if required:
            raise AuthError("sign in first")
        return None
    return verify_token(token)


def run(fn: Callable, *args) -> Response:
    try:
        with connect() as conn:
            return ok(fn(conn, *args))
    except svc.ApiError as e:
        return err(e.status, e.message)
    except AuthError as e:
        return err(401, str(e))
    except RuntimeError as e:
        log.exception("server error")
        return err(503, str(e))
    except Exception as e:   # pragma: no cover - surfaced as a clean JSON error
        log.exception("server error")
        return err(500, f"server error: {type(e).__name__}")


async def handle(req: Request, fn: Callable, *args, auth: str = "required", body: bool = False) -> Response:
    """auth: 'required' | 'optional' | 'none'."""
    try:
        user = _user(req, required=auth == "required") if auth != "none" else None
        b = await _body(req) if body else None
    except svc.ApiError as e:
        return err(e.status, e.message)
    except AuthError as e:
        return err(401, str(e))
    call = [a for a in ((user,) if auth != "none" else ())] + list(args) + ([b] if body else [])
    return await run_in_threadpool(run, fn, *call)   # DB and engine work is blocking


# ---------------------------------------------------------------------------------------------- public
@app.get("/api/health")
async def health():
    try:
        with connect() as conn:
            conn.execute("select 1")
        return ok({"ok": True, "db": True})
    except Exception as e:
        return ok({"ok": False, "db": False, "error": type(e).__name__}, 503)


@app.get("/api/config")
async def config():
    """Public client configuration (the Supabase URL and anon key are public by design)."""
    return ok({
        "supabase_url": os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL"),
        "supabase_anon_key": os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY"),
        "dev_auth": dev_auth_enabled(),
    })


@app.get("/api/lobby")
async def lobby(req: Request):
    return await handle(req, svc.lobby, auth="none")


@app.get("/api/invite/{code}")
async def invite(req: Request, code: str):
    return await handle(req, svc.invite, code, auth="none")


@app.post("/api/tutorial/{action}")
async def tutorial_sandbox(req: Request, action: str):
    """The tutorial's sandbox: hand-placed positions through the real engine (brink.tutorial). No sign-in,
    no database: the client holds the state."""
    try:
        b = await _body(req)
        return ok(await run_in_threadpool(tutorial.handle, svc.game_map(), action, b))
    except svc.ApiError as e:
        return err(e.status, e.message)
    except tutorial.TutorialError as e:
        return err(400, str(e))


@app.api_route("/api/cron/tick", methods=["GET", "POST"])
async def tick(req: Request):
    return await handle(req, svc.tick, auth="none")


@app.post("/api/workshop/{action}")
async def workshop_action(req: Request, action: str):
    try:
        raw = await req.body()
        if len(raw) > 2_100_000:
            return err(413, "That position is too large.")
        return ok(await run_in_threadpool(workshop.handle, svc.game_map(), action, await _body(req)))
    except svc.ApiError as e:
        return err(e.status, e.message)
    except workshop.WorkshopError as e:
        return err(400, str(e))


@app.get("/api/games/{game_id}/sandbox")
async def sandbox_copy(req: Request, game_id: str):
    return await handle(req, svc.sandbox_copy, game_id)


# ---------------------------------------------------------------------------------------------- account
@app.get("/api/me")
async def get_me(req: Request):
    return await handle(req, svc.me)


@app.put("/api/me")
async def put_me(req: Request):
    return await handle(req, svc.update_me, body=True)


@app.get("/api/notifications")
async def notifications(req: Request):
    return await handle(req, svc.notifications)


@app.post("/api/notifications/read")
async def notifications_read(req: Request):
    return await handle(req, svc.mark_read, body=True)


# ---------------------------------------------------------------------------------------------- games
@app.get("/api/games/mine")
async def my_games(req: Request):
    return await handle(req, svc.my_games)


@app.post("/api/games")
async def create_game(req: Request):
    return await handle(req, lambda conn, user, body: svc.create_game(conn, user, body), body=True)


@app.get("/api/games/{game_id}")
async def get_game(req: Request, game_id: str, since: int = 0):
    return await handle(req, lambda conn, user: svc.get_game(conn, user, game_id, since), auth="optional")


@app.post("/api/games/{game_id}/join")
async def join(req: Request, game_id: str):
    return await handle(req, lambda conn, user, body: svc.join(conn, user, game_id, body), body=True)


@app.post("/api/games/{game_id}/leave")
async def leave(req: Request, game_id: str):
    return await handle(req, lambda conn, user: svc.leave(conn, user, game_id))


@app.post("/api/games/{game_id}/cancel")
async def cancel(req: Request, game_id: str):
    return await handle(req, lambda conn, user: svc.cancel(conn, user, game_id))


@app.post("/api/games/{game_id}/start")
async def start(req: Request, game_id: str):
    return await handle(req, lambda conn, user: svc.start(conn, user, game_id))


@app.post("/api/games/{game_id}/validate")
async def validate(req: Request, game_id: str):
    return await handle(req, lambda conn, user, body: svc.validate_orders(conn, user, game_id, body), body=True)


@app.post("/api/games/{game_id}/submit")
async def submit(req: Request, game_id: str):
    return await handle(req, lambda conn, user, body: svc.submit(conn, user, game_id, body), body=True)


@app.get("/api/games/{game_id}/messages")
async def messages(req: Request, game_id: str, after: int = 0):
    return await handle(req, lambda conn, user: svc.list_messages(conn, user, game_id, after))


@app.post("/api/games/{game_id}/messages")
async def post_message(req: Request, game_id: str):
    return await handle(req, lambda conn, user, body: svc.post_message(conn, user, game_id, body), body=True)


# ---------------------------------------------------------------------------------------------- email
def _unsubscribe(token: str | None, on: bool) -> Response:
    uid = mail.verify_unsubscribe_token(token)
    if uid is None:
        return Response(T.unsubscribe_page("invalid", site_url=mail.site_url()), status_code=400,
                        media_type="text/html", headers={"Cache-Control": "no-store"})
    try:
        with connect() as conn:
            mail.set_preference(conn, uid, on)
    except Exception:
        log.exception("unsubscribe failed")
        return err(503, "could not save that just now: try the link again")
    action = f"/api/email/unsubscribe?t={token}"
    return Response(T.unsubscribe_page("resumed" if on else "stopped", action, mail.site_url()),
                    media_type="text/html", headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})


@app.get("/api/email/unsubscribe")
async def unsubscribe(t: str = ""):
    """The "Stop turn emails" link: turns them off and says so (with a button to turn them back on)."""
    return await run_in_threadpool(_unsubscribe, t, False)


@app.post("/api/email/unsubscribe")
async def unsubscribe_post(req: Request, t: str = ""):
    """One-click unsubscribe (RFC 8058: body `List-Unsubscribe=One-Click`) and the page's own buttons."""
    form = parse_qs((await req.body()).decode("utf-8", "replace"))
    on = (form.get("action") or [""])[0] == "resubscribe"
    return await run_in_threadpool(_unsubscribe, t, on)


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def not_found(rest: str):
    return err(404, f"no endpoint /api/{rest}")
