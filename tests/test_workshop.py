"""Real rules, copied positions, guest persistence and hosted isolation."""
import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from brink import session as S, tutorial, workshop as W
from brink.bots.human import _power_info
from brink.game import Game
from brink.hosted.app import app
from brink.map import BrinkMap
from test_hosted import client, db_url, players, started  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def gmap():
    return BrinkMap.load(ROOT / "build/map.json")


def test_guest_http_without_auth_or_database(monkeypatch):
    monkeypatch.delenv("BRINK_DATABASE_URL", raising=False)
    c = TestClient(app)
    r = c.post("/api/workshop/start", json={"power": "USA"})
    assert r.status_code == 200
    v = r.json()
    assert v["state"]["rules"]["last_year"] == 2034
    assert v["decision"]["options"]["USA"]["status"]["briefing"]["primacy"] == 9
    check = c.post("/api/workshop/validate", json={"state": v["state"], "orders": ["A TEX - MEX"]}).json()
    assert "unaligned" in check["results"][0]["reason"]
    r = c.post("/api/workshop/step", json={"state": v["state"], "orders": ["C CAL - MEX"]})
    assert r.status_code == 200
    nxt = r.json()
    assert nxt["state"]["step"] > v["state"]["step"]
    # Reload uses only the returned JSON, no session or user account.
    resumed = c.post("/api/workshop/view", json={"state": nxt["state"]}).json()
    assert resumed["decision"] == nxt["decision"]


def test_sandbox_resolves_both_sides_without_mutating_source(gmap):
    lesson = next(c for c in json.loads((ROOT / "web/public/tutorial.json").read_text())["chapters"] if c["id"] == "support")
    source = tutorial.start(gmap, lesson["setup"])["state"]
    before = copy.deepcopy(source)
    body = {"mode": "sandbox", "power": "USA", "state": source,
            "decisions": {"USA": lesson["steps"]["S2032M"]["solution"], **lesson["script"].get("S2032M", {})}}
    result = W.handle(gmap, "step", body)
    assert source == before
    expected = S.advance(gmap, source, body["decisions"], bots={})
    assert result["phases"] == expected["phases"]
    assert result["state"]["units"] == expected["units"]
    assert any(o["result"] == "bounce" and "strength" in o["reason"] for o in result["phases"][0]["orders"])


def test_guest_can_finish_and_reopen_the_result(gmap):
    v = W.handle(gmap, "start", {"power": "USA"})
    v["state"]["seed"] = 7
    for _ in range(40):
        if v["result"]:
            break
        v = W.handle(gmap, "step", {"power": "USA", "state": v["state"], "orders": []})
    assert v["result"] and v["state"]["stage"] == "over"
    reopened = W.handle(gmap, "view", {"power": "USA", "state": v["state"]})
    assert reopened["result"] == v["result"] and reopened["decision"] is None


def test_sandbox_repeated_resolution_and_reset_are_identical(gmap):
    source = S.new_game(gmap, humans=gmap.power_order, bot_kind="hold")
    body = {"mode": "sandbox", "state": source, "decisions": {"USA": ["C CAL - MEX"], "EU": ["C PAR - BRA"]}}
    one = W.handle(gmap, "step", body)
    assert W.handle(gmap, "step", body) == one
    assert {u["power"] for u in one["state"]["units"] if u["at"] in ("MEX", "BRA")} == {"USA", "EU"}
    assert W.handle(gmap, "view", {"mode": "sandbox", "state": source})["state"]["units"] == source["units"]


def test_sandbox_preserves_winter_context(gmap):
    source = S.new_game(gmap, humans=gmap.power_order, bot_kind="hold")
    source = S.advance(gmap, source, {}, bots={})
    source = S.advance(gmap, source, {}, bots={})
    assert source["stage"] == "propose"
    copy_state = W.snapshot(source, list(gmap.power_order))
    v = W.handle(gmap, "view", {"mode": "sandbox", "state": copy_state})
    assert v["decision"]["kind"] == "propose"
    crisis = next(iter(v["decision"]["options"].values()))["status"]["briefing"]["crisis"]
    assert crisis["applied"] and crisis["projected"] == crisis["current"]
    assert v["state"]["year_acc"] == source["year_acc"]
    assert v["state"]["winter"] == source["winter"]
    v = W.handle(gmap, "step", {"mode": "sandbox", "state": copy_state})
    assert v["decision"]["kind"] == "winter"


def test_briefing_crisis_uses_real_year_totals(gmap):
    g = Game(gmap)
    g.crisis = 5
    g.year = 2036
    g.year_closed = {"HOR"}
    g.year_clash = True
    g.year_commons["USA"] = 3
    g.year_commons["CHN"] = 2
    c = _power_info(g, "USA")["status"]["briefing"]["crisis"]
    assert c == {"current": 5, "baseline": 2, "closed": 1, "clash": 1, "commons": 5,
                 "per_point": 4, "projected": 8, "applied": False}


@pytest.mark.parametrize("patch", [{"rules": {"commons_per_point": 0}}, {"units": [{"power": "X", "type": "A", "at": "TEX"}]}, {"stage": "x"}])
def test_reject_invalid_public_states(gmap, patch):
    state = S.new_game(gmap, humans=["USA"])
    state.update(patch)
    with pytest.raises(W.WorkshopError):
        W.handle(gmap, "step", {"state": state})


def test_hosted_copy_does_not_leak_or_modify_submissions(client, players):
    a, b, outsider = players
    gid, initial = started(players)
    b.ok("POST", f"/api/games/{gid}/submit", {"step": initial["pending"]["step"], "orders": ["C BEI H"], "ready": False})
    assert client.get(f"/api/games/{gid}/sandbox").status_code == 401
    assert outsider.get(f"/api/games/{gid}/sandbox").status_code == 403
    state = a.ok("GET", f"/api/games/{gid}/sandbox")["state"]
    assert "C BEI H" not in json.dumps(state)
    assert state["seed"] == 1 and set(state["players"].values()) == {"sandbox"}
    result = client.post("/api/workshop/step", json={"mode": "sandbox", "state": state, "decisions": {"CHN": ["C BEI H"]}})
    assert result.status_code == 200
    live = b.ok("GET", f"/api/games/{gid}")
    assert live["pending"]["step"] == initial["pending"]["step"]
    assert live["submission"]["orders"] == ["C BEI H"]


@pytest.mark.parametrize("mode", ["practice", "sandbox"])
def test_invalid_orders_are_accepted_and_explained_only_in_results(gmap, mode):
    state = S.new_game(gmap, humans=list(gmap.power_order), bot_kind="hold")
    orders = ["A TEX - MEX", "C CAL S A TEX - MEX", "F WAS C A TEX - BEI"]
    body = {"mode": mode, "power": "USA", "state": state, "orders": orders,
            "decisions": {"USA": orders}}
    result = W.handle(gmap, "step", body)
    resolved = {o["text"]: o for o in result["phases"][0]["orders"]}
    assert all(t in resolved for t in orders)
    assert resolved[orders[0]]["result"] == "void"
    assert "unaligned" in resolved[orders[0]]["reason"]
    assert any(u["type"] == "A" and u["at"] == "TEX" for u in result["state"]["units"])


def test_hosted_invalid_draft_survives_save_ready_and_reload(players):
    a, b, _ = players
    gid, g = started(players)
    step = g["game"]["step"]
    orders = ["A TEX - MEX"]
    for ready in (False, True):
        saved = a.ok("POST", f"/api/games/{gid}/submit", {"step": step, "orders": orders, "ready": ready})
        assert saved["game"]["step"] == step
        assert saved["submission"]["orders"] == orders
        assert saved["submission"]["ready"] == ready
        assert saved["phases"] == []
        assert a.ok("GET", f"/api/games/{gid}")["submission"]["orders"] == orders
    result = b.ok("POST", f"/api/games/{gid}/submit", {"step": step, "orders": [], "ready": True})
    resolved = next(o for o in result["phases"][0]["orders"] if o["text"] == orders[0])
    assert resolved["result"] == "void" and "unaligned" in resolved["reason"]
