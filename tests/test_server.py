"""`brink serve`: the play API and the HumanPlayer threading."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from brink.bots.human import HumanPlayer
from brink.bots.strategist import StrategistBot
from brink.game import Game, Rules
from brink.map import BrinkMap
from brink.server import PlayServer

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "build" / "map.json"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    app = PlayServer(MAP, web_dir=None, save_dir=tmp_path_factory.mktemp("play"), last_year=2032)
    httpd = app.make_server(port=0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield app, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()
    app.shutdown()


def call(base: str, method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(base + path, method=method, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=30) as f:
            return f.status, json.loads(f.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def answer_defaults(base: str, st: dict) -> dict:
    """Submit nothing for the open decision (holds / defaults) and return the next state."""
    p = st["pending"]
    code, r = call(base, "POST", f"/api/games/{st['id']}/orders", {"orders": [], "decision": p["id"]})
    assert code == 200 and r["accepted"]
    return r["state"]


def test_start_game_and_pending_orders(server):
    app, base = server
    code, st = call(base, "POST", "/api/games", {"power": "usa", "seed": 3})
    assert code == 201
    assert st["power"] == "USA" and st["status"] == "waiting"
    p = st["pending"]
    assert p["kind"] == "orders" and p["power"] == "USA"
    assert p["phase"]["name"] == "S2031M" and p["phase"]["kind"] == "movement"
    units = {(u["unit"]["type"], u["unit"]["at"]): u for u in p["units"]}
    assert set(units) == {("F", "WAS"), ("F", "CAL"), ("A", "TEX"), ("C", "CAL")}
    texts = {o["text"] for o in units[("A", "TEX")]["options"]}
    assert "A TEX H" in texts and "A TEX - CAN" in texts
    kinds = {o["kind"] for u in p["units"] for o in u["options"]}
    assert {"hold", "move", "support", "commons", "sanction"} <= kinds
    assert p["withdraw"] == ["JPN"]                      # USA starts with an umbrella over Japan
    assert p["status"]["nuclear"] and p["status"]["centres"] == 3
    assert st["record"]["phases"] == [] and st["record"]["human"] == "USA"
    code, games = call(base, "GET", "/api/games")
    assert any(g["id"] == st["id"] for g in games)
    code, again = call(base, "GET", f"/api/games/{st['id']}")
    assert again["pending"]["id"] == p["id"]


def test_validate_reports_void_reasons(server):
    app, base = server
    _, st = call(base, "POST", "/api/games", {"power": "USA", "seed": 4})
    code, r = call(base, "POST", f"/api/games/{st['id']}/validate",
                   {"orders": ["A TEX - CAN", "A TEX - MEX", "F WAS - XXX", "nonsense", "USA WITHDRAW JPN"]})
    assert code == 200
    ok = [x["ok"] for x in r["results"]]
    assert ok == [True, False, False, False, True]
    assert "swing" in r["results"][1]["reason"]
    assert r["results"][3]["order"] is None
    # validating doesn't advance the game
    _, again = call(base, "GET", f"/api/games/{st['id']}")
    assert again["pending"]["id"] == st["pending"]["id"]


def test_submit_orders_resolves_phase(server):
    app, base = server
    _, st = call(base, "POST", "/api/games", {"power": "USA", "seed": 5})
    p = st["pending"]
    code, r = call(base, "POST", f"/api/games/{st['id']}/orders",
                   {"orders": ["A TEX - CAN", "F WAS H", "C CAL COMMONS", "garbage"], "decision": p["id"]})
    assert code == 200 and r["accepted"]
    st2 = r["state"]
    ph = st2["record"]["phases"][0]
    assert ph["name"] == "S2031M"
    usa = {o["text"]: o for o in ph["orders"] if o["power"] == "USA"}
    assert usa["A TEX - CAN"]["result"] in ("ok", "bounce")
    assert usa["C CAL COMMONS"]["result"] == "ok"
    assert usa["garbage"]["result"] == "void"
    assert "F CAL H" in usa                       # unordered units hold
    assert st2["pending"]["id"] > p["id"]
    # the old decision can't be answered twice
    code, r = call(base, "POST", f"/api/games/{st['id']}/orders", {"orders": [], "decision": p["id"]})
    assert code == 409 and not r["accepted"]


def test_full_year_with_winter_decisions(server):
    app, base = server
    _, st = call(base, "POST", "/api/games", {"power": "USA", "seed": 6})
    seen = []
    # play through the end of Winter 2031
    while st["status"] == "waiting":
        p = st["pending"]
        seen.append(p["kind"])
        if p["kind"] == "orders":
            orders = ["A TEX - CAN"] if p["phase"]["season"] == "spring" else []
        elif p["kind"] == "adjust":
            assert p["change"] != 0
            orders = [p["builds"][0]["text"]] if p["change"] > 0 else [p["disband"][0]["text"]]
        elif p["kind"] == "propose":
            assert "ACCORD" in p["resolutions"]
            orders = ["PROPOSE ACCORD"]
        elif p["kind"] == "vote":
            assert p["proposal"]
            orders = ["VOTE YES"]
        elif p["kind"] == "umbrellas":
            assert p["candidates"] and set(p["current"]) <= set(p["candidates"])
            orders = [f"USA UMBRELLA {c}" for c in p["current"]]
        else:
            orders = []
        code, r = call(base, "POST", f"/api/games/{st['id']}/orders", {"orders": orders, "decision": p["id"]})
        assert code == 200
        st = r["state"]
        if any(ph["kind"] == "winter" for ph in st["record"]["phases"]):
            break
    assert seen[:2] == ["orders", "orders"]
    assert "umbrellas" in seen
    names = [ph["name"] for ph in st["record"]["phases"]]
    assert names[0] == "S2031M" and "W2031A" in names
    winter = st["record"]["phases"][-1]
    assert winter["assembly"] is not None
    if "propose" in seen:
        assert winter["assembly"]["proposal"] == "ACCORD"
    if "vote" in seen:
        assert winter["assembly"]["votes"].get("USA") == "yes"
    assert st["pending"]["kind"] == "orders" and st["pending"]["phase"]["name"] == "S2032M"


@pytest.mark.parametrize("power", ["JPN", "RUS"])
def test_play_to_the_end_with_holds(server, power):
    app, base = server
    _, st = call(base, "POST", "/api/games", {"power": power, "seed": 7})
    steps = 0
    while st["status"] == "waiting":
        st = answer_defaults(base, st)
        steps += 1
        assert steps < 200
    assert st["status"] == "over"
    rec = st["record"]
    assert rec["result"]["kind"] in ("time", "primacy", "collapse")
    assert rec["players"][power] == "human"
    assert all(pl == "search" for p, pl in rec["players"].items() if p != power)
    assert st["pending"] is None
    s = app.get(st["id"])
    s.thread.join(timeout=5)
    assert not s.thread.is_alive()
    assert (app.save_dir / f"{st['id']}.json").exists()


def test_bad_requests(server):
    app, base = server
    assert call(base, "POST", "/api/games", {"power": "XXX"})[0] == 400
    assert call(base, "GET", "/api/games/nope")[0] == 404
    assert call(base, "GET", "/api/nothing")[0] == 404
    _, st = call(base, "POST", "/api/games", {"power": "EU", "seed": 1, "alliances": True})
    assert st["status"] == "waiting"
    code, r = call(base, "DELETE", f"/api/games/{st['id']}")
    assert code == 200 and r["ok"]
    assert call(base, "GET", f"/api/games/{st['id']}")[0] == 404


# ---------------------------------------------------------------------- HumanPlayer threading
def _game(human: HumanPlayer, power="USA", last_year=2031) -> Game:
    gmap = BrinkMap.load(MAP)
    players = {p: (human if p == power else StrategistBot(seed=i)) for i, p in enumerate(gmap.power_order)}
    return Game(gmap, players, seed=1, rules=Rules(last_year=last_year))


def test_human_player_thread_finishes_cleanly():
    human = HumanPlayer()
    game = _game(human)
    done = threading.Event()

    def run():
        try:
            game.run()
        finally:
            human.finish()
            done.set()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    last = 0
    while True:
        d = human.wait_decision(timeout=10, after=last)
        if d is None or human.finished:
            break
        assert d.id > last
        assert human.answer(d.id, [])
        assert not human.answer(d.id, [])          # answered once only
        last = d.id
    assert done.wait(10)
    t.join(5)
    assert not t.is_alive() and game.result is not None
    assert human.wait_decision(timeout=0.1, after=last) is None   # no deadlock after the end


def test_human_player_abort_releases_thread():
    human = HumanPlayer()
    game = _game(human, last_year=2040)
    errors = []

    def run():
        try:
            game.run()
        except Exception as e:  # GameAborted
            errors.append(type(e).__name__)
        finally:
            human.finish()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    d = human.wait_decision(timeout=10)
    assert d is not None and d.kind == "orders"
    human.abort()
    t.join(5)
    assert not t.is_alive()
    assert errors == ["GameAborted"]
    assert game.result is None


def test_human_player_timeout_defaults():
    human = HumanPlayer(timeout=0.01)
    game = _game(human)
    rec = game.run()                       # nobody answers: every decision times out to the default
    assert rec["result"] is not None
    assert any(o["power"] == "USA" and o["text"] == "F WAS H" for o in rec["phases"][0]["orders"])
