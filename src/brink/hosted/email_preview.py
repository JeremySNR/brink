"""`brink email-preview`: render every email with realistic data from a sample game, to HTML and PNG.

The sample is a real engine game (bots on every seat) played until each kind of decision point has come up;
the turn emails are built by the same `email.turn_context` the service uses, so what you see is what a
player gets. PNGs come from headless Chrome at 700px and 375px wide; a `-fallback` variant swaps the web
fonts for the system fallbacks, to check the design survives clients that block them.
"""
from __future__ import annotations

import copy
import datetime as dt
import os
import shutil
import subprocess
from pathlib import Path

from . import email as mail
from . import email_templates as T

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
SAMPLE_ID = "6f1c2a8e-3b7d-4e55-9a10-2c4b8d7e9f01"
SAMPLE_USER = "0b9e1d2c-4a5f-4c3e-8d7a-1e2f3a4b5c6d"


def sample_snapshots(seed: int = 3, bot_kind: str = "strategist", min_year: int = 2033) -> dict[str, tuple]:
    """{decision kind: (state, pending, power)} from one bot game, each at or after `min_year` if it comes."""
    from .. import session as S
    from .service import game_map
    gmap = game_map()
    state = S.new_game(gmap, seed=seed, humans=[], bot_kind=bot_kind)
    found: dict[str, tuple] = {}
    for _ in range(400):
        if state.get("result") is not None:
            break
        p = S.pending(gmap, state, powers=[])
        kind = p["kind"]
        year = mail.phase_parts(p["phase"])[1]
        good = year >= min_year or kind == "retreats"
        if kind == "winter":
            good = good and bool(p.get("proposal"))
        if good and kind not in found:
            power = {"orders": "USA", "propose": p.get("chair"), "retreats": p["powers"][0],
                     "winter": next((x for x in p["powers"] if gmap.nuclear(x)), p["powers"][0])}[kind]
            if kind != "orders" or power in p["powers"]:
                found[kind] = (copy.deepcopy(state), p, power)
        if len(found) == 4:
            break
        state = S.advance(gmap, state, {})
    return found


def contexts(now: dt.datetime | None = None) -> dict[str, dict]:
    from .service import game_map
    gmap = game_map()
    now = now or dt.datetime(2026, 10, 14, 9, 12, tzinfo=dt.timezone.utc)
    os.environ.setdefault("BRINK_EMAIL_SECRET", "preview-only-secret")
    unsub = mail.unsubscribe_url(SAMPLE_USER)
    out = {}
    for kind, (state, pend, power) in sample_snapshots().items():
        game = {"id": SAMPLE_ID, "name": "Atlantic Grudge", "deadline_hours": 24, "last_year": 2040,
                "deadline": (now + dt.timedelta(hours=24)).replace(minute=0)}
        last = state["phases"][-1] if state["phases"] else None
        out[f"due-{kind}"] = mail.turn_context(gmap, game, pend, power, "due", now=now, last_record=last,
                                               state=state, humans=3, saved=None, unsubscribe=unsub)
        if kind == "orders":
            g2 = dict(game, deadline=now + dt.timedelta(hours=5, minutes=48))
            out["reminder-orders"] = mail.turn_context(gmap, g2, pend, power, "reminder", now=now, last_record=last,
                                                       state=state, humans=3, saved=4, unsubscribe=unsub)
            g3 = dict(game, deadline_hours=12, deadline=now + dt.timedelta(minutes=172))
            out["reminder-orders-12h"] = mail.turn_context(gmap, g3, pend, power, "reminder", now=now,
                                                           last_record=last, state=state, humans=3, saved=0,
                                                           unsubscribe=unsub)
    return out


def _fallback(html: str) -> str:
    """The same email as a client that blocks web fonts shows it."""
    return (html.replace(f'<link href="{T.FONTS_URL}" rel="stylesheet">', "")
            .replace("'Barlow Condensed',", "").replace("Archivo,", ""))


def screenshot(html_path: Path, png_path: Path, width: int, height: int) -> bool:
    if not Path(CHROME).exists():
        return False
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                    f"--screenshot={png_path.resolve()}", f"--window-size={width},{height}",
                    "--virtual-time-budget=4000", html_path.resolve().as_uri()],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
    return png_path.exists()


def render_all(out: Path, shots: bool = True, review_dir: Path | None = None) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    pages: dict[str, tuple[str, int]] = {}
    for name, ctx in contexts().items():
        subject, html, text = T.turn_email(ctx)
        (out / f"{name}.txt").write_text(f"Subject: {subject}\n\n{text}")
        pages[name] = (html, 1750)
    pages["due-orders-fallback"] = (_fallback(pages["due-orders"][0]), 1750)
    for kind in T.AUTH:
        subject, html = T.auth_email(kind)
        pages[f"auth-{kind}"] = (html.replace("{{ .ConfirmationURL }}", f"{mail.site_url()}/#access_token=…")
                                 .replace("{{ .Email }}", "ada@example.com")
                                 .replace("{{ .SiteURL }}", mail.site_url()), 700)
    pages["unsubscribe-stopped"] = (T.unsubscribe_page("stopped", "/api/email/unsubscribe?t=x", mail.site_url()), 600)
    pages["unsubscribe-invalid"] = (T.unsubscribe_page("invalid", site_url=mail.site_url()), 600)
    written = []
    for name, (html, height) in pages.items():
        path = out / f"{name}.html"
        path.write_text(html)
        written.append(path)
        if shots:
            # headless Chrome won't lay a window out narrower than ~500px: show the phone width in a frame
            frame = out / f".{name}-375.html"
            frame.write_text(f'<!doctype html><body style="margin:0"><iframe src="{path.name}" width="375" '
                             f'height="{int(height * 1.45)}" style="border:0;display:block"></iframe></body>')
            for src, width, h, suffix in ((path, 700, height, ""), (frame, 375, int(height * 1.45), "-375")):
                png = out / f"{name}{suffix}.png"
                if screenshot(src, png, width, h):
                    written.append(png)
                    if review_dir is not None and name in REVIEW:
                        review_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copy(png, review_dir / f"email-{name}{suffix}.png")
    return written


REVIEW = {"due-orders", "due-retreats", "due-propose", "due-winter", "reminder-orders", "due-orders-fallback",
          "auth-magic_link", "unsubscribe-stopped"}
