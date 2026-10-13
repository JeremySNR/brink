"""brink.session: persisted state and one-decision-point-at-a-time play (hosted, serverless)."""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from brink import session as S
from brink.bots.search import SearchBot
from brink.bots.strategist import StrategistBot
from brink.game import Game, Rules
from brink.map import BrinkMap
from brink.record import validate_record
from helpers import game_with, mini

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def gmap() -> BrinkMap:
    return BrinkMap.load(ROOT / "build" / "map.json")


def norm(x):
    return json.loads(json.dumps(x))


def _same_bots(gmap, seed, cls=StrategistBot):
    rng = random.Random(seed)
    board: dict = {}
    return {p: cls(seed=rng.randrange(1 << 30), coalition=board) for p in gmap.power_order}


# ---------------------------------------------------------------------------------------------- (a)
def test_stepwise_search_bots_complete_with_valid_record(gmap):
    st = S.new_game(gmap, seed=3, bot_kind="search")
    kinds = set()
    while st["result"] is None:
        p = S.pending(gmap, st)
        kinds.add(p["kind"])
        assert p["powers"] and set(p["options"]) == set(p["powers"])
        st = S.advance(gmap, st)            # bots rebuilt from the state by make_bots
    assert {"orders", "propose", "winter"} <= kinds
    rec = S.record(st)
    assert not validate_record(rec)
    assert S.pending(gmap, st) is None
    with pytest.raises(S.GameOver):
        S.advance(gmap, st)


def test_same_state_same_bot_decisions(gmap):
    st = S.new_game(gmap, seed=9, bot_kind="search")
    a, b = S.advance(gmap, st), S.advance(gmap, st)
    assert a == b
    assert st["step"] == 0 and not st["phases"]          # input untouched


# ---------------------------------------------------------------------------------------------- (b)
@pytest.mark.parametrize("seed", [0, 1])
def test_stepwise_equals_run_with_same_bots(gmap, seed):
    """The stepwise path asks the players in exactly the order Game.run() does, so identically seeded
    bots (RNG and coalition board included) produce the identical record, not just the same scores."""
    ref = Game(gmap, _same_bots(gmap, seed), seed=seed).run()
    st = S.new_game(gmap, seed=seed)
    bots = _same_bots(gmap, seed)
    while st["result"] is None:
        st = S.advance(gmap, st, {}, bots)
    rec = S.record(st)
    assert rec["phases"] == norm(ref["phases"]) and rec["result"] == norm(ref["result"])


def test_stepwise_equals_run_with_search_bots(gmap):
    rules = Rules(last_year=2032)
    ref = Game(gmap, _same_bots(gmap, 4, SearchBot), seed=4, rules=rules).run()
    st = S.new_game(gmap, seed=4, rules=Rules(last_year=2032))
    bots = _same_bots(gmap, 4, SearchBot)
    while st["result"] is None:
        st = S.advance(gmap, st, {}, bots)
    assert S.record(st)["phases"] == norm(ref["phases"])


# ---------------------------------------------------------------------------------------------- (c)
def test_round_trip_at_every_decision_point(gmap):
    g = Game(gmap, None, seed=21)
    g.player_names = {q: "strategist" for q in gmap.power_order}
    stages = set()
    while g.result is None:
        st = norm(g.to_state())
        g2 = Game.from_state(gmap, st)
        assert norm(g2.to_state()) == st
        stages.add(g.stage)
        g.players = S.make_bots(st, gmap.power_order, kind="strategist")
        g2.players = S.make_bots(st, gmap.power_order, kind="strategist")
        g.step()
        g2.step()
        assert norm(g2.to_state()) == norm(g.to_state()), (st["year"], st["stage"])
    assert stages == {"movement", "retreats", "propose", "winter"}
    assert norm(Game.from_state(gmap, norm(g.to_state())).to_state()) == norm(g.to_state())


def test_round_trip_mid_winter_keeps_prelude_and_proposal():
    from brink.state import Resolution
    m = mini()
    g = game_with(["USA F WAS", "CHN C BRA"], umbrellas=())
    g.season = "winter"
    assert g._winter_begin() and any("aligns" in e for e in g._winter["events"])
    g._winter["chair"] = "USA"
    g._winter["proposal"] = Resolution("recognition", "BRA")
    g.stage = "winter"
    st = norm(g.to_state())
    g2 = Game.from_state(m, st)
    assert g2._winter["proposal"] == Resolution("recognition", "BRA")
    assert g2._winter["events"] == g._winter["events"] and g2._winter["rec"] == norm(g._winter["rec"])
    assert norm(g2.to_state()) == st


# ---------------------------------------------------------------------------------------------- (d)
def test_civil_disorder_human_still_reaches_the_end(gmap):
    st = S.new_game(gmap, seed=5, humans={"USA"}, bot_kind="strategist", rules=Rules(last_year=2034))
    waits = 0
    while True:
        st = S.run_bots_until_human(gmap, st)
        if st["result"] is not None:
            break
        p = S.pending(gmap, st, powers=["USA"])
        assert "USA" in p["powers"] and set(p["options"]) == {"USA"}
        waits += 1
        st = S.advance(gmap, st, {}, expect_step=p["step"])   # the human never answers
    assert waits >= 8 and st["result"]["kind"] in ("time", "collapse", "primacy")
    assert not validate_record(S.record(st))
    usa_moves = [o for ph in st["phases"] if ph["kind"] == "movement" for o in ph["orders"]
                 if o["power"] == "USA" and o.get("kind") not in ("hold", None)]
    assert not usa_moves                                     # every US unit held all game
    assert st["players"]["USA"] == "human"


def test_stale_step_is_rejected(gmap):
    st = S.new_game(gmap, seed=1, humans={"USA"}, bot_kind="hold")
    nxt = S.advance(gmap, st, {"USA": []}, expect_step=0)
    with pytest.raises(S.StaleState):
        S.advance(gmap, nxt, {"USA": []}, expect_step=0)


# ---------------------------------------------------------------------------------------------- decisions
def test_orders_pending_validate_and_advance(gmap):
    st = S.new_game(gmap, seed=2, humans={"USA"}, bot_kind="hold")
    p = S.pending(gmap, st)
    assert p["kind"] == "orders" and p["phase"] == "S2031M" and p["step"] == 0
    assert set(p["powers"]) == set(gmap.power_order)
    opts = p["options"]["USA"]
    assert opts["units"] and "status" in opts and "withdraw" in opts
    unit = opts["units"][0]
    move = next(o for o in unit["options"] if o["kind"] == "move")
    good = f"{unit['unit']['type']} {unit['unit']['at']} - {move['to']}"
    res = S.validate(gmap, st, "USA", [good, "A XXX - YYY", "garbage"])
    assert res[0]["ok"] and not res[1]["ok"] and not res[2]["ok"] and res[2]["reason"]
    st2 = S.advance(gmap, st, {"USA": [good]})
    mv = st2["phases"][-1]
    assert mv["name"] == "S2031M" and any(o["power"] == "USA" and o["kind"] == "move" for o in mv["orders"])
    assert st2["step"] == 1 and st2["season"] == "fall" and st2["stage"] == "movement"


def _winter_state():
    """Mini map, Winter 2031 after the prelude: USA chairs (2 centres, ties with JPN, map order),
    CHN holds BRA and MEX with four units, USA has one build."""
    g = game_with(["USA F WAS", "CHN A BEI", "CHN C GUA", "CHN C BRA", "CHN C MEX"],
                  owners={"BRA": "CHN", "MEX": "CHN"}, umbrellas=())
    g.season = "winter"
    assert g._winter_begin()
    g.stage = "propose"
    g.player_names = {"USA": "human", "CHN": "human", "JPN": "human"}
    st = norm(g.to_state())
    st["meta"] = {"humans": ["CHN", "JPN", "USA"], "bot_kind": "hold", "alliances": []}
    return st


def test_propose_then_one_simultaneous_winter_step():
    m = mini()
    st = _winter_state()
    p = S.pending(m, st)
    assert p["kind"] == "propose" and p["powers"] == ["USA"] and p["phase"] == "W2031A"
    assert "RECOGNITION BRA" in p["options"]["USA"]["resolutions"]
    assert S.validate(m, st, "CHN", ["PROPOSE ACCORD"])[0]["reason"] == "only the chair proposes"
    assert S.validate(m, st, "USA", ["RECOGNITION BRA"])[0]["ok"]
    st = S.advance(m, st, {"USA": ["RECOGNITION BRA"]})
    assert not st["phases"]                             # the Winter record is written when Winter ends

    p = S.pending(m, st)
    assert p["kind"] == "winter" and set(p["powers"]) == {"USA", "CHN", "JPN"}
    assert p["proposal"] == "RECOGNITION BRA" and p["chair"] == "USA"
    usa, chn = p["options"]["USA"], p["options"]["CHN"]
    assert usa["vote"]["proposal"] == "RECOGNITION BRA" and usa["adjust"]["change"] == 1
    assert usa["umbrellas"]["candidates"] == ["JPN"]
    assert chn["adjust"]["change"] == 0 and chn["adjust"]["change_if_passed"] == -1
    assert p["options"]["JPN"]["umbrellas"] is None

    orders = ["VOTE YES", "BUILD C TEX", "BUILD A WAS", "USA UMBRELLA JPN", "USA UMBRELLA CHN"]
    res = S.validate(m, st, "USA", orders)
    assert [r["ok"] for r in res] == [True, True, False, True, False]
    assert res[2]["reason"] == "no builds left"
    assert not S.validate(m, st, "JPN", ["JPN UMBRELLA KOR"])[0]["ok"]

    st = S.advance(m, st, {"USA": ["VOTE YES", "BUILD C TEX", "USA UMBRELLA JPN"], "JPN": ["YES"]})
    w = st["phases"][-1]
    assert w["kind"] == "winter" and w["assembly"]["passed"]
    assert w["assembly"]["votes"] == {"USA": "yes", "JPN": "yes"}        # CHN abstained (civil disorder)
    assert st["ownership"]["BRA"] is None
    units = {(u["power"], u["type"], u["at"]) for u in st["units"]}
    assert ("USA", "C", "TEX") in units
    assert sum(1 for u in units if u[0] == "CHN") == 3                  # the default disband after recognition
    assert ["USA", "JPN"] in st["umbrellas"]
    assert st["stage"] == "movement" and st["season"] == "spring" and st["year"] == 2032


def test_umbrella_none_drops_umbrellas_and_silence_keeps_them():
    m = mini()
    st = _winter_state()
    st = S.advance(m, st, {"USA": []})                        # proposes nothing
    g = Game.from_state(m, st)
    g.umbrellas = {("USA", "JPN")}
    st = norm(g.to_state()) | {"meta": st["meta"]}
    p = S.pending(m, st)
    assert p["proposal"] is None and p["options"]["USA"]["vote"] is None
    assert S.validate(m, st, "USA", ["VOTE YES"])[0]["reason"] == "there is no proposal to vote on"
    kept = S.advance(m, st, {})
    assert ["USA", "JPN"] in kept["umbrellas"]
    dropped = S.advance(m, st, {"USA": ["UMBRELLA NONE"]})
    assert ["USA", "JPN"] not in dropped["umbrellas"]


def test_run_bots_until_human_stops_at_human_decisions(gmap):
    st = S.new_game(gmap, seed=8, humans={"JPN"}, bot_kind="strategist")
    st = S.run_bots_until_human(gmap, st)
    assert "JPN" in S.pending(gmap, st)["powers"]
    st = S.advance(gmap, st, {"JPN": []})
    st = S.run_bots_until_human(gmap, st)
    p = S.pending(gmap, st)
    assert "JPN" in p["powers"]
    # the propose step is skipped by run_bots_until_human unless JPN chairs it
    if p["kind"] == "propose":
        assert p["powers"] == ["JPN"]
