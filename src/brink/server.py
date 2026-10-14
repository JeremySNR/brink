"""`brink serve`: play Brink in the browser, one human against six bots.

A small JSON API on the standard library's ThreadingHTTPServer, plus the built web viewer (web/dist).
Each game runs `Game.run()` in its own thread; the human's seat is a HumanPlayer (brink.bots.human)
that blocks the game thread until the browser answers the open decision.

API (all JSON):

    GET    /api/health                      {"ok": true}
    GET    /api/games                       [{id, power, status, year, season}]
    POST   /api/games                       {power, seed?, alliances?, last_year?} -> game state
    GET    /api/games/{id}                  game state
    POST   /api/games/{id}/validate         {orders: [text]} -> {results: [{text, ok, order, reason}]}
    POST   /api/games/{id}/orders           {orders: [text], decision: id, submit: true}
                                            -> {results, accepted, state}; waits for the next decision
    DELETE /api/games/{id}                  stop the game thread
    POST   /api/tutorial/{start|validate|step}  the tutorial's sandbox (brink.tutorial)

Game state: {id, power, status: waiting|thinking|over|error, record (game.json format),
pending (the open decision, see HumanPlayer) | null, error}.
"""
from __future__ import annotations

import json
import mimetypes
import random
import secrets
import threading
import time
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import tutorial
from .bots.human import GameAborted, HumanPlayer
from .bots.search import SearchBot
from .bots.strategist import StrategistBot, Temperament
from .game import Game, Rules
from .map import BrinkMap

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAP = ROOT / "build" / "map.json"
DEFAULT_WEB = ROOT / "web" / "dist"
WAIT_SECONDS = 60.0


class PlaySession:
    """One game: the human's seat, six bots and the thread running the engine."""

    def __init__(self, sid: str, gmap: BrinkMap, power: str, seed: int | None = None, alliances: bool = False,
                 betrayal: float = 0.12, last_year: int | None = None, map_name: str = "brink-v0",
                 save_dir: Path | None = None, rules: Rules | None = None):
        if power not in gmap.power_order:
            raise ValueError(f"unknown power {power!r}")
        self.id = sid
        self.power = power
        self.seed = seed if seed is not None else random.randrange(1 << 30)
        self.created = time.time()
        self.save_dir = save_dir
        self.error: str | None = None
        self.human = HumanPlayer()
        rng = random.Random(self.seed)
        bots = [p for p in gmap.power_order if p != power]
        allies_fn = None
        self.alliances: list[str] = []
        if alliances:
            try:
                from .sim import Diplomat
                dip = Diplomat(bots, rng, betrayal=betrayal)
                allies_fn = dip.allies
                self.alliances = list(getattr(dip, "initial", []))
            except Exception:   # the sim's Diplomat is optional
                allies_fn = None
        players = {power: self.human}
        board: dict = {}
        for p in bots:
            players[p] = SearchBot(seed=rng.randrange(1 << 30), temperament=_temperament(rng), coalition=board,
                                   **({"allies": allies_fn} if allies_fn else {}))
        rules = rules or Rules()
        if last_year:
            rules.last_year = int(last_year)
        self.game = Game(gmap, players, seed=self.seed, rules=rules, map_name=map_name)
        self.thread = threading.Thread(target=self._run, name=f"brink-game-{sid}", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            self.game.run()
        except GameAborted:
            pass
        except Exception:   # surfaced through the API
            self.error = traceback.format_exc()
        finally:
            self.human.finish()
            self.save()

    # ------------------------------------------------------------------ state
    @property
    def status(self) -> str:
        if self.error:
            return "error"
        if self.game.result is not None:
            return "over"
        if self.human.decision is not None:
            return "waiting"
        if self.human.finished:
            return "stopped"
        return "thinking"

    def record(self) -> dict:
        g = self.game
        rec = {"map": g.map_name, "seed": g.seed,
               "players": {p: getattr(pl, "name", type(pl).__name__) for p, pl in g.players.items()},
               "phases": list(g.phases), "result": g.result}
        rec["human"] = self.power
        return rec

    def state(self) -> dict:
        d = self.human.decision
        return {"id": self.id, "power": self.power, "seed": self.seed, "status": self.status,
                "alliances": self.alliances, "record": self.record(),
                "pending": d.to_json() if d else None, "error": self.error}

    def summary(self) -> dict:
        return {"id": self.id, "power": self.power, "status": self.status, "year": self.game.year,
                "season": self.game.season, "created": self.created}

    # ------------------------------------------------------------------ actions
    def wait(self, after: int = 0, timeout: float = WAIT_SECONDS) -> None:
        self.human.wait_decision(timeout=timeout, after=after)

    def validate(self, orders: list[str]) -> list[dict]:
        return self.human.validate(self.game, orders)

    def submit(self, decision_id: int, orders: list[str]) -> bool:
        ok = self.human.answer(decision_id, orders)
        return ok

    def stop(self) -> None:
        self.human.abort()
        self.thread.join(timeout=5)

    def save(self) -> None:
        if not self.save_dir:
            return
        try:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            (self.save_dir / f"{self.id}.json").write_text(json.dumps(self.record()))
        except OSError:
            pass


def _temperament(rng: random.Random) -> Temperament:
    try:
        from .sim import draw_temperament
        return draw_temperament(rng)
    except Exception:
        return Temperament()


class PlayServer:
    """Sessions + HTTP handler factory. Usable in-process (tests) or through `serve()`."""

    def __init__(self, map_path: str | Path = DEFAULT_MAP, web_dir: str | Path | None = DEFAULT_WEB,
                 save_dir: str | Path | None = None, rules_factory=None, last_year: int | None = None):
        self.map = BrinkMap.load(map_path)
        self.map_name = "brink-v0"
        self.web_dir = Path(web_dir) if web_dir else None
        self.save_dir = Path(save_dir) if save_dir else None
        self.rules_factory = rules_factory or Rules
        self.sessions: dict[str, PlaySession] = {}
        self.lock = threading.Lock()
        self.last_year = last_year      # default for games created without one
        self.verbose = False

    def create(self, power: str, seed: int | None = None, alliances: bool = False, last_year: int | None = None,
               betrayal: float = 0.12) -> PlaySession:
        sid = secrets.token_hex(4)
        s = PlaySession(sid, self.map, power.upper(), seed=seed, alliances=alliances, betrayal=betrayal,
                        last_year=last_year or self.last_year, map_name=self.map_name, save_dir=self.save_dir,
                        rules=self.rules_factory())
        with self.lock:
            self.sessions[sid] = s
        return s

    def get(self, sid: str) -> PlaySession | None:
        with self.lock:
            return self.sessions.get(sid)

    def close(self, sid: str) -> bool:
        with self.lock:
            s = self.sessions.pop(sid, None)
        if s:
            s.stop()
        return s is not None

    def shutdown(self) -> None:
        for sid in list(self.sessions):
            self.close(sid)

    # ------------------------------------------------------------------ http
    def handler(self):
        app = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "brink/0.1"
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):  # quiet: one line per API call is enough
                if self.path.startswith("/api/") and getattr(app, "verbose", False):
                    super().log_message(fmt, *args)

            # -- helpers
            def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store" if ctype.startswith("application/json") else "no-cache")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _json(self, code: int, obj) -> None:
                self._send(code, json.dumps(obj).encode(), "application/json; charset=utf-8")

            def _error(self, code: int, msg: str) -> None:
                self._json(code, {"error": msg})

            def _body(self) -> dict:
                n = int(self.headers.get("Content-Length") or 0)
                if not n:
                    return {}
                try:
                    data = json.loads(self.rfile.read(n))
                except ValueError:
                    raise _BadRequest("body is not JSON")
                if not isinstance(data, dict):
                    raise _BadRequest("body must be a JSON object")
                return data

            # -- routing
            def do_GET(self):
                self._route("GET")

            def do_HEAD(self):
                self._route("GET")

            def do_POST(self):
                self._route("POST")

            def do_DELETE(self):
                self._route("DELETE")

            def _route(self, method: str):
                path = urlparse(self.path).path
                try:
                    if path.startswith("/api/"):
                        return self._api(method, [p for p in path[5:].split("/") if p])
                    if method != "GET":
                        return self._error(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")
                    return self._static(path)
                except _BadRequest as e:
                    return self._error(HTTPStatus.BAD_REQUEST, str(e))
                except (BrokenPipeError, ConnectionResetError):
                    return None
                except Exception as e:   # pragma: no cover - defensive
                    traceback.print_exc()
                    return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(e).__name__}: {e}")

            def _api(self, method: str, parts: list[str]):
                if parts == ["health"]:
                    return self._json(200, {"ok": True})
                if len(parts) == 2 and parts[0] == "tutorial" and method == "POST":
                    try:
                        return self._json(200, tutorial.handle(app.map, parts[1], self._body()))
                    except tutorial.TutorialError as e:
                        raise _BadRequest(str(e))
                if parts == ["games"]:
                    if method == "GET":
                        with app.lock:
                            items = [s.summary() for s in app.sessions.values()]
                        return self._json(200, sorted(items, key=lambda x: -x["created"]))
                    if method == "POST":
                        b = self._body()
                        power = str(b.get("power") or "").upper()
                        if power not in app.map.power_order:
                            raise _BadRequest(f"power must be one of {', '.join(app.map.power_order)}")
                        seed = b.get("seed")
                        last_year = b.get("last_year")
                        try:
                            seed = int(seed) if seed not in (None, "") else None
                            last_year = int(last_year) if last_year not in (None, "") else None
                        except (TypeError, ValueError):
                            raise _BadRequest("seed and last_year must be integers")
                        s = app.create(power, seed=seed, alliances=bool(b.get("alliances")), last_year=last_year,
                                       betrayal=float(b.get("betrayal", 0.12)))
                        s.wait(timeout=WAIT_SECONDS)
                        return self._json(201, s.state())
                    return self._error(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")
                if len(parts) >= 2 and parts[0] == "games":
                    s = app.get(parts[1])
                    if s is None:
                        return self._error(HTTPStatus.NOT_FOUND, f"no game {parts[1]}")
                    rest = parts[2:]
                    if not rest and method == "GET":
                        return self._json(200, s.state())
                    if not rest and method == "DELETE":
                        app.close(s.id)
                        return self._json(200, {"ok": True})
                    if rest == ["validate"] and method == "POST":
                        orders = _orders(self._body())
                        return self._json(200, {"results": s.validate(orders)})
                    if rest == ["orders"] and method == "POST":
                        b = self._body()
                        orders = _orders(b)
                        d = s.human.decision
                        results = s.validate(orders)
                        if not b.get("submit", True):
                            return self._json(200, {"results": results, "accepted": False, "state": s.state()})
                        did = int(b.get("decision") or (d.id if d else 0))
                        if d is None or did != d.id:
                            return self._json(409, {"error": "that decision is no longer open",
                                                    "results": results, "accepted": False, "state": s.state()})
                        accepted = s.submit(did, orders)
                        s.wait(after=did)
                        s.save()
                        return self._json(200, {"results": results, "accepted": accepted, "state": s.state()})
                return self._error(HTTPStatus.NOT_FOUND, "no such endpoint")

            def _static(self, path: str):
                root = app.web_dir
                if not root or not root.is_dir():
                    return self._send(404, b"web/dist not built: run `npm run build` in web/", "text/plain")
                rel = path.lstrip("/") or "index.html"
                f = (root / rel).resolve()
                if root.resolve() not in f.parents and f != root.resolve():
                    return self._send(403, b"forbidden", "text/plain")
                if f.is_dir():
                    f = f / "index.html"
                if not f.is_file():
                    return self._send(404, b"not found", "text/plain")
                ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                    ctype += "; charset=utf-8"
                return self._send(200, f.read_bytes(), ctype)

        return Handler

    def make_server(self, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
        httpd = ThreadingHTTPServer((host, port), self.handler())
        httpd.daemon_threads = True
        return httpd


def path_is_api(path: str) -> bool:
    return path.startswith("/api/")


class _BadRequest(Exception):
    pass


def _orders(body: dict) -> list[str]:
    orders = body.get("orders", [])
    if isinstance(orders, str):
        orders = [orders]
    if not isinstance(orders, list):
        raise _BadRequest("orders must be a list of strings")
    return [str(o).strip() for o in orders if str(o).strip()]


def serve(host: str = "127.0.0.1", port: int = 8765, power: str | None = None, seed: int | None = None,
          map_path: str | Path = DEFAULT_MAP, web_dir: str | Path = DEFAULT_WEB, save_dir: str | Path | None = None,
          last_year: int | None = None, alliances: bool = False, verbose: bool = False) -> None:
    app = PlayServer(map_path, web_dir, save_dir, last_year=last_year)
    app.verbose = verbose
    httpd = app.make_server(host, port)
    url = f"http://{host}:{httpd.server_address[1]}/"
    if not (Path(web_dir) / "index.html").is_file():
        print("note: web/dist is not built; run `cd web && npm install && npm run build` (or use `npm run dev`)")
    if power:
        s = app.create(power, seed=seed, alliances=alliances, last_year=last_year)
        print(f"Brink: you play {app.map.powers[s.power].get('name', s.power)} -> {url}?play={s.id}", flush=True)
    else:
        print(f"Brink: choose a power at {url}?play", flush=True)
    print("Ctrl-C to stop.", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        app.shutdown()
