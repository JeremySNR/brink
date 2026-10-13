"""Hand-written Brink rules tests on the fixture map (tests/fixtures/mini_map.json).

Geography: Americas WAS TEX (US homes) CAN (filler) ARC (swing, opens 2034) MEX BRA ARG (swing; ARG inland)
CAM (filler); Asia BEI GUA (China homes) KOR VNM IDN (swing) MAL (filler) TOK OSA (Japan, non-nuclear).
Seas NAO CAR PAN* PAC ECS SCS MLC* IOC (* chokepoint). Routes: SAT TEX-BRA [CAR], TPA TEX-TOK [CAR PAN PAC],
MLR GUA-MAL [SCS MLC], NSR WAS-BEI [NAO PAC] (opens 2034).
"""
from __future__ import annotations

from collections import Counter

import pytest

from brink.game import Rules
from brink.orders import parse_order
from brink.state import Effects, Resolution
from helpers import Scripted, ctx, game_with, mini, resolve, retreat_options, units, where


# ---------------------------------------------------------------------------------------------- map reach
def test_reach_helpers():
    m = mini()
    assert m.army_moves("CAN", 2031) == {"WAS", "TEX"}            # ARC not open yet
    assert m.army_moves("CAN", 2034) == {"WAS", "TEX", "ARC"}
    assert "BRA" in m.capital_moves("TEX", 2031)                   # SAT trade route
    assert "BRA" in m.capital_moves("MEX", 2031)                   # short hop over CAR
    assert "IDN" in m.capital_moves("VNM", 2031)                   # hop over MLC
    assert "IDN" not in m.capital_moves("VNM", 2031, closed={"MLC"})
    assert "IDN" in m.capital_moves("MAL", 2031, closed={"MLC"})   # MAL also shares IOC with IDN
    assert "BEI" not in m.capital_moves("WAS", 2033) and "BEI" in m.capital_moves("WAS", 2034)  # NSR opens
    assert m.influence_moves("ARG", 2031) == {"BRA", "CAM"}        # jumps 2 steps
    assert "TWN" not in m.within_two("GUA", 2031)                  # impassable
    assert m.distance("TEX", "BRA") == 2
    assert m.entry_block("USA", "A", "MEX", {"MEX": None}, set()) is not None
    assert m.entry_block("USA", "C", "MEX", {"MEX": None}, set()) is None
    assert m.entry_block("CHN", "A", "TEX", {}, set()) is not None           # nuclear home
    assert m.entry_block("CHN", "C", "TOK", {}, set()) is not None           # foreign home, civil
    assert m.entry_block("CHN", "A", "TOK", {}, {("USA", "JPN")}) is not None
    assert m.entry_block("USA", "A", "TOK", {}, {("USA", "JPN")}) is None    # the protector itself
    assert m.entry_block("CHN", "A", "TOK", {}, {("CHN", "JPN")}) is None    # own umbrella doesn't protect vs self


# ---------------------------------------------------------------------------------------------- parse/format
@pytest.mark.parametrize("text", [
    "A TEX H", "A TEX - MEX", "A PAR - BRA VIA", "C ALA - TOK", "C MEX S A TEX - MEX", "F NAO S A WAS H",
    "F NAO C A WAS - PAR", "F HOR CLOSE", "F HOR CLOSE CHN IND", "C BRA SANCTION CHN", "I MOS DISRUPT TUR",
    "A DEL COMMONS", "I OSA FORGE CHN", "USA WITHDRAW JPN", "A UKR R BLR", "A UKR D", "BUILD A WAS",
    "BUILD C MEX", "DISBAND I ROM", "USA UMBRELLA JPN", "PROPOSE SANCTIONS CHN", "PROPOSE ACCORD",
    "PROPOSE NOTHING", "VOTE YES", "VOTE VETO", "F MAO - SPA/NC",
])
def test_notation_round_trip(text):
    assert parse_order(text).text() == text
    assert parse_order(text.lower()).text() == text


# ---------------------------------------------------------------------------------------------- Brazil example
def _brazil(extra_china=(), china_units=()):
    usa = Scripted(moves={"S2031": ["A CAM - BRA"], "F2031": ["C CAM - BRA", "A BRA S C CAM - BRA"]},
                   retreats={})
    chn = Scripted(moves={"F2031": list(extra_china)}, retreats={"F2031": ["C BRA R ARG"]})
    g = game_with(["USA A CAM", "USA C CAM", "CHN C BRA", *china_units], owners={"BRA": "CHN"},
                  players={"USA": usa, "CHN": chn}, rules=Rules(last_year=2031))
    g.run()
    return g


def test_brazil_example_us_takes_brazil():
    g = _brazil()
    spring, fall = g.phases[0], g.phases[1]
    assert {o["text"]: o["result"] for o in spring["orders"]}["A CAM - BRA"] == "ok"
    res = {o["text"]: o["result"] for o in fall["orders"]}
    assert res["C CAM - BRA"] == "ok" and res["A BRA S C CAM - BRA"] == "ok"
    assert fall["dislodged"] == [{"power": "CHN", "type": "C", "at": "BRA", "from": "CAM"}]
    assert g.ownership["BRA"] == "USA"
    assert any("Brazil aligns to United States" in e for e in g.phases[-1]["events"])
    assert any(u.power == "CHN" and u.loc == "ARG" for u in g.units)   # retreated (ARG unaligned: civil ok)


def test_brazil_example_defended_by_influence_support():
    g = _brazil(extra_china=["I ARG S C BRA H"], china_units=["CHN I ARG"])
    fall = {o["text"]: o["result"] for o in g.phases[1]["orders"]}
    assert fall["C CAM - BRA"] == "bounce"
    assert g.ownership["BRA"] == "CHN"     # contested (US army + Chinese capital): no change


def test_brazil_example_defended_by_disrupt():
    g = _brazil(extra_china=["I ARG DISRUPT BRA"], china_units=["CHN I ARG"])
    fall = {o["text"]: o["result"] for o in g.phases[1]["orders"]}
    assert fall["A BRA S C CAM - BRA"] == "cut"
    assert fall["C CAM - BRA"] == "bounce"
    assert fall["I ARG DISRUPT BRA"] == "ok"


# ---------------------------------------------------------------------------------------------- support cutting
BASE = ["USA C TEX", "USA C MEX", "CHN C BRA"]


def test_military_attack_does_not_cut_civil_support():
    res, t = resolve(BASE + ["CHN A CAM"],
                     ["USA: C TEX - BRA", "USA: C MEX S C TEX - BRA", "CHN: A CAM - MEX"],
                     owners={"MEX": "USA", "BRA": "CHN"})
    assert t["C MEX"] == "ok" and t["C TEX"] == "ok"
    assert [d.unit.loc for d in res.dislodged] == ["BRA"]
    assert t["A CAM"] == "ok"           # different layer: the army walks into MEX's military slot


def test_civil_attack_cuts_civil_support():
    res, t = resolve(BASE + ["CHN I CAM"], ["USA: C TEX - BRA", "USA: C MEX S C TEX - BRA", "CHN: I CAM - MEX"],
                     owners={"MEX": "USA", "BRA": "CHN"})
    assert t["C MEX"] == "cut" and t["C TEX"] == "bounce"


def test_attack_from_target_province_does_not_cut():
    res, t = resolve(BASE, ["USA: C TEX - BRA", "USA: C MEX S C TEX - BRA", "CHN: C BRA - MEX"],
                     owners={"MEX": "USA", "BRA": "CHN"})
    assert t["C MEX"] == "ok" and t["C TEX"] == "ok" and t["C BRA"] == "dislodged"


def test_military_support_of_civil_move_cut_by_military_attack():
    res, t = resolve(["USA C TEX", "USA A CAM", "CHN C BRA", "CHN A MEX"],
                     ["USA: C TEX - BRA", "USA: A CAM S C TEX - BRA", "CHN: A MEX - CAM"],
                     owners={"MEX": "CHN", "BRA": "CHN"})
    assert t["A CAM"] == "cut" and t["C TEX"] == "bounce"


def test_disrupt_cuts_both_layers_in_range_and_is_void_out_of_range():
    specs = ["USA C TEX", "USA A CAM", "USA C CAM", "CHN C BRA", "CHN I ARG"]
    orders = ["USA: C TEX - BRA", "USA: A CAM S C TEX - BRA", "USA: C CAM S C TEX - BRA"]
    res, t = resolve(specs, orders + ["CHN: I ARG DISRUPT CAM"], owners={"BRA": "CHN"})
    assert t["A CAM"] == "cut" and t["C CAM"] == "cut" and t["C TEX"] == "bounce" and t["I ARG"] == "ok"
    res, t = resolve(specs, orders + ["CHN: I ARG DISRUPT MEX"], owners={"BRA": "CHN"})   # 3 steps away
    assert t["I ARG"] == "void" and t["C TEX"] == "ok"


def test_disrupt_is_not_cut_by_attack_but_stops_when_dislodged():
    specs = ["USA C TEX", "USA A CAM", "CHN C BRA", "CHN I ARG", "USA I MEX"]
    # attacked only (bounces): the disrupt still works
    _, t = resolve(specs + ["USA C CAM"], ["USA: C TEX - BRA", "USA: A CAM S C TEX - BRA", "USA: C CAM - ARG",
                                           "CHN: I ARG DISRUPT CAM"], owners={"BRA": "CHN"})
    assert t["I ARG"] == "ok" and t["A CAM"] == "cut"
    # dislodged by a supported attack: the disrupt fails
    _, t = resolve(["USA C BRA", "USA A BRA", "USA C CAM", "USA A CAM", "CHN I ARG", "CHN C MEX"],
                   ["USA: C BRA - ARG", "USA: A BRA S C BRA - ARG", "USA: A CAM S C MEX - BRA",
                    "CHN: I ARG DISRUPT BRA"],
                   owners={"BRA": "USA", "ARG": "USA"})
    assert t["I ARG"] == "dislodged" and t["A BRA"] == "ok"


def test_disrupt_does_not_cut_support_for_attack_on_the_disrupter():
    _, t = resolve(["USA C BRA", "USA A BRA", "CHN I ARG"],
                   ["USA: C BRA - ARG", "USA: A BRA S C BRA - ARG", "CHN: I ARG DISRUPT BRA"],
                   owners={"BRA": "USA", "ARG": "USA"})
    assert t["A BRA"] == "ok" and t["C BRA"] == "ok" and t["I ARG"] == "dislodged"


def test_supports_cross_layer_into_own_province():
    _, t = resolve(["USA A BRA", "USA C CAM", "CHN C BRA"], ["USA: C CAM - BRA", "USA: A BRA S C CAM - BRA"],
                   owners={"BRA": "CHN"})
    assert t["A BRA"] == "ok" and t["C CAM"] == "ok"
    _, t = resolve(["USA A BRA", "USA A CAM"], ["USA: A CAM - BRA", "USA: A BRA S A CAM - BRA"],
                   owners={"BRA": "USA"})
    assert t["A BRA"] == "void"    # same layer: can't support into the province you stand in


def test_no_self_dislodgement_including_via_support():
    # China's army backs the US capital against China's own capital: doesn't count
    _, t = resolve(["USA C CAM", "CHN A BRA", "CHN C BRA"], ["USA: C CAM - BRA", "CHN: A BRA S C CAM - BRA"],
                   owners={"BRA": "CHN"})
    assert t["C CAM"] == "bounce" and t["A BRA"] == "void"
    # own attack on own unit: never dislodges
    _, t = resolve(["USA C CAM", "USA C BRA", "USA A CAM"], ["USA: C CAM - BRA", "USA: A CAM S C CAM - BRA"],
                   owners={"BRA": "USA"})
    assert t["C CAM"] == "bounce" and t["C BRA"] == "ok"


def test_layers_do_not_stand_off_or_dislodge_each_other():
    res, t = resolve(["USA A CAM", "CHN C CAM", "CHN A BRA", "USA C BRA"],
                     ["USA: A CAM - BRA", "CHN: C CAM - BRA"], owners={"BRA": "USA"})
    assert t["A CAM"] == "bounce" and t["C CAM"] == "bounce"   # each meets a same-layer defender
    res, t = resolve(["USA A CAM", "CHN C BRA"], ["USA: A CAM - BRA"], owners={"BRA": "CHN"})
    assert t["A CAM"] == "ok" and not res.dislodged and "CHN C BRA" in where(res)


# ---------------------------------------------------------------------------------------------- entry restrictions
def test_unaligned_swing_state_entry():
    _, t = resolve(["USA A TEX", "USA C TEX"], ["USA: A TEX - MEX"])
    assert t["A TEX"] == "void"
    _, t = resolve(["USA A TEX", "USA C TEX"], ["USA: C TEX - MEX", "USA: A TEX S C TEX - MEX"])
    assert t["C TEX"] == "ok" and t["A TEX"] == "void"       # military can't support into it
    _, t = resolve(["USA I CAN", "USA C TEX"], ["USA: C TEX - MEX", "USA: I CAN S C TEX - MEX"])
    assert t["I CAN"] == "ok"
    _, t = resolve(["USA A TEX"], ["USA: A TEX - CAN"])
    assert t["A TEX"] == "ok"                                 # filler: open to all


def test_homes_entry():
    _, t = resolve(["CHN A MEX", "CHN C MEX"], ["CHN: A MEX - TEX", "CHN: C MEX - CAM"], owners={"MEX": "CHN"})
    assert t["A MEX"] == "void"                               # nuclear home
    _, t = resolve(["CHN C CAM"], ["CHN: C CAM - MEX"], owners={"MEX": "USA"})
    assert t["C CAM"] == "ok"
    _, t = resolve(["CHN C MEX"], ["CHN: C MEX - TEX"])
    assert t["C MEX"] == "void"                               # civil never enters a foreign home


def test_umbrella_protection_and_secret_withdraw():
    specs = ["CHN F ECS", "JPN C TOK", "USA F PAC"]
    _, t = resolve(specs, ["CHN: F ECS - TOK"])
    assert t["F ECS"] == "void"
    res, t = resolve(specs, ["CHN: F ECS - TOK", "USA: USA WITHDRAW JPN"])
    assert t["F ECS"] == "ok" and res.umbrellas == set() and res.withdrawn == [("USA", "JPN")]
    _, t = resolve(specs, ["USA: F PAC - TOK"])
    assert t["F PAC"] == "ok"                                 # the protector itself may enter
    # two umbrellas: China's own umbrella doesn't protect Japan against China
    _, t = resolve(specs, ["CHN: F ECS - TOK", "USA: USA WITHDRAW JPN"], umbrellas=[("USA", "JPN"), ("CHN", "JPN")])
    assert t["F ECS"] == "ok"
    _, t = resolve(specs, ["CHN: CHN WITHDRAW JPN"])          # not China's umbrella to withdraw
    assert t["CHN: CHN WITHDRAW JPN"] == "void"


# ---------------------------------------------------------------------------------------------- chokepoints
def test_close_blocks_same_turn_route_and_hop():
    specs = ["CHN C GUA", "CHN C VNM", "USA F MLC"]
    res, t = resolve(specs, ["CHN: C GUA - MAL", "CHN: C VNM - IDN", "USA: F MLC CLOSE"])
    assert t["C GUA"] == "failed" and t["C VNM"] == "failed" and t["F MLC"] == "ok"
    assert res.closed == {"MLC": ["CHN", "JPN"]}
    res, t = resolve(specs, ["CHN: C GUA - MAL", "CHN: C VNM - IDN", "USA: F MLC CLOSE JPN"])
    assert t["C GUA"] == "ok" and t["C VNM"] == "ok"          # closed only to Japan
    res, t = resolve(specs + ["USA C MAL"], ["USA: C MAL - IDN", "USA: F MLC CLOSE"])
    assert t["C MAL"] == "ok"                                 # own power passes; MAL-IDN also share IOC


def test_close_fails_if_fleet_dislodged_same_turn():
    specs = ["CHN C GUA", "USA F MLC", "CHN F SCS", "CHN F VNM"]
    res, t = resolve(specs, ["CHN: C GUA - MAL", "USA: F MLC CLOSE", "CHN: F SCS - MLC", "CHN: F VNM S F SCS - MLC"])
    assert t["F MLC"] == "dislodged" and t["C GUA"] == "ok" and res.closed == {}


def test_close_blocks_convoy_and_freedom_of_navigation():
    # a convoy through a chokepoint needs the fleet there to convoy, so a closing fleet never carries one
    res, t = resolve(["CHN A VNM", "CHN F MLC"], ["CHN: A VNM - IDN", "CHN: F MLC C A VNM - IDN"],
                     owners={"VNM": "CHN", "IDN": "CHN"})
    assert t["A VNM"] == "ok"
    res, t = resolve(["CHN A VNM", "USA F MLC"], ["CHN: A VNM - IDN", "USA: F MLC CLOSE"],
                     owners={"VNM": "CHN", "IDN": "CHN"})
    assert t["A VNM"] == "failed"
    _, t = resolve(["USA F MLC"], ["USA: F MLC CLOSE"], effects=Effects(navigation={"MLC"}))
    assert t["F MLC"] == "void"
    _, t = resolve(["USA F SCS"], ["USA: F SCS CLOSE"])
    assert t["F SCS"] == "void"                               # not a chokepoint


# ---------------------------------------------------------------------------------------------- retreats
def test_retreats_per_layer():
    res, t = resolve(["USA C TEX", "USA C CAM", "CHN C MEX", "CHN A MEX", "USA A CAN"],
                     ["USA: C TEX - MEX", "USA: C CAM S C TEX - MEX"], owners={"MEX": "CHN"})
    assert t["C MEX"] == "dislodged" and t["A MEX"] == "ok"   # the Chinese army isn't touched
    opts = retreat_options(mini(), res, ctx(owners={"MEX": "CHN"}))
    (u,) = opts
    # TEX: foreign home (and attacker origin); CAM: civil slot now empty (C CAM supported, stayed) -> occupied
    assert opts[u] == []
    res, _ = resolve(["USA C TEX", "USA I CAN", "CHN C MEX", "USA A CAM"],
                     ["USA: C TEX - MEX", "USA: I CAN S C TEX - MEX"], owners={"MEX": "CHN"})
    opts = retreat_options(mini(), res, ctx(owners={"MEX": "CHN"}))
    assert list(opts.values()) == [["CAM"]]                   # the US army in CAM doesn't block a civil retreat


def test_retreat_not_to_standoff_and_double_retreat_disbands():
    from brink.adjudicator import resolve_retreats
    base = ["USA C TEX", "USA I CAN", "CHN C MEX"]
    attack = ["USA: C TEX - MEX", "USA: I CAN S C TEX - MEX"]
    res, _ = resolve(base, attack, owners={"MEX": "CHN"})
    assert list(retreat_options(mini(), res, ctx(owners={"MEX": "CHN"})).values()) == [["CAM"]]
    # a standoff in CAM's civil slot closes it to retreats
    res, t = resolve(base + ["USA C BRA", "USA I ARG"], attack + ["USA: C BRA - CAM", "USA: I ARG - CAM"],
                     owners={"MEX": "CHN"})
    assert t["C BRA"] == "bounce" and ("CAM", "C") in res.standoffs
    assert list(retreat_options(mini(), res, ctx(owners={"MEX": "CHN"})).values()) == [[]]
    # two units retreating to the same place are both disbanded
    own = {"MEX": "CHN", "BRA": "CHN"}
    res, t = resolve(base + ["USA C ARG", "USA A CAM", "CHN C BRA"],
                     attack + ["USA: C ARG - BRA", "USA: A CAM S C ARG - BRA"], owners=own)
    opts = retreat_options(mini(), res, ctx(owners=own))
    assert {u.loc: o for u, o in opts.items()} == {"MEX": ["CAM"], "BRA": ["CAM"]}   # ARG: attacker origin
    rr = resolve_retreats(mini(), res.units, opts, [("CHN", parse_order("C MEX R CAM")),
                                                     ("CHN", parse_order("C BRA R CAM"))])
    assert len(rr.disbanded) == 2 and not any(u.power == "CHN" for u in rr.units)
    assert {r.result for r in rr.results} == {"bounce"}


# ---------------------------------------------------------------------------------------------- special orders
def test_sanction_and_commons():
    res, t = resolve(["USA C TEX", "USA C MEX", "CHN C BRA", "USA A CAN", "CHN I CAM"],
                     ["USA: C TEX SANCTION CHN", "USA: C MEX SANCTION JPN", "CHN: C BRA SANCTION USA",
                      "USA: A CAN COMMONS", "CHN: I CAM - MEX"], owners={"MEX": "USA"})
    assert t["C TEX"] == "ok" and t["C MEX"] == "ok"     # attacked but not dislodged: still works
    assert t["C BRA"] == "void"                           # BRA unaligned: not a place to sanction from
    assert res.sanctions == {"CHN": 1, "JPN": 1} and res.commons == {"USA": 1}
    res, t = resolve(["USA C MEX", "CHN C BRA", "CHN I CAM"],
                     ["USA: C MEX SANCTION CHN", "CHN: C BRA - MEX", "CHN: I CAM S C BRA - MEX"],
                     owners={"MEX": "USA"})
    assert t["C MEX"] == "dislodged" and res.sanctions == {}


def test_nuclear_clash_flag():
    res, _ = resolve(["USA A CAM", "USA A ARG", "CHN A BRA"], ["USA: A CAM - BRA", "USA: A ARG S A CAM - BRA"],
                     owners={"BRA": "CHN", "ARG": "USA"})
    assert res.nuclear_clash
    res, _ = resolve(["CHN A BEI", "CHN F GUA", "JPN A KOR"], ["CHN: A BEI - KOR", "CHN: F GUA S A BEI - KOR"],
                     owners={"KOR": "JPN"})
    assert not res.nuclear_clash


def test_assembly_effects_on_orders():
    eff = Effects(peacekeeping={"BRA"}, recognition={"MEX"}, sanctions_regime={"CHN"})
    _, t = resolve(["USA A CAM", "USA A BRA", "USA C TEX", "CHN C GUA"],
                   ["USA: A CAM - BRA", "USA: A BRA COMMONS", "USA: C TEX - MEX", "CHN: C GUA - VNM"],
                   owners={"BRA": "USA"}, effects=eff)
    assert t["A CAM"] == "void" and t["A BRA"] == "void" and t["C TEX"] == "void" and t["C GUA"] == "void"


def test_legal_orders_all_validate():
    from brink.bots.random_bot import RandomBot
    from brink.game import Game
    g = Game(mini(), {p: RandomBot(seed=i) for i, p in enumerate(mini().power_order)}, seed=1)
    checked = 0
    for _ in range(6):
        pos = g.position()
        for u in g.units:
            for od in pos.legal_orders(u):
                norm, why = pos.validate(u.power, od)
                assert why is None, (od.text(), why)
                checked += 1
        g.play_movement()
        g.season = "fall" if g.season == "spring" else "spring"
    assert checked > 50


# ---------------------------------------------------------------------------------------------- winter
def test_crisis_arithmetic_and_collapse():
    g = game_with(["USA F WAS", "USA C TEX"], crisis=3)
    g.year_closed = {"MLC", "PAN"}
    g.year_clash = True
    g.year_commons = Counter({"USA": 5, "CHN": 4})
    g.season = "winter"
    g.play_winter()
    assert g.crisis == 3 + 1 + 2 + 1 - 2
    g = game_with(["USA F WAS"], crisis=0)
    g.year = 2036
    g.year_commons = Counter({"USA": 12})
    g.play_winter()
    assert g.crisis == 0                      # +2 late baseline -3 commons, clamped at 0
    g = game_with(["USA F WAS"], crisis=9)
    g.play_winter()
    assert g.result["kind"] == "collapse" and set(g.result["scores"].values()) == {0.0}


def test_sanctions_reduce_builds_but_never_force_disbands():
    usa = Scripted(builds={2031: ["BUILD C WAS"]})
    g = game_with(["USA F WAS", "USA C TEX", "USA C MEX"], owners={"MEX": "USA", "BRA": "USA"}, players={"USA": usa})
    g.year_sanctions = Counter({"USA": 1})
    g.play_winter()                           # 4 centres, 3 units, 1 sanction -> 0 builds
    assert len(g.units_of("USA")) == 3
    g = game_with(["USA F WAS", "USA C TEX"], players={"USA": usa})
    g.year_sanctions = Counter({"USA": 3})
    g.play_winter()                           # 2 centres, 2 units, 3 sanctions -> still 2 units
    assert len(g.units_of("USA")) == 2
    g = game_with(["USA F WAS", "USA C TEX", "USA C MEX"], owners={"MEX": "USA", "BRA": "USA"}, players={"USA": usa})
    g.play_winter()
    assert len(g.units_of("USA")) == 4        # control: the build goes through without sanctions


def test_recession_and_climate_accord():
    usa = Scripted(builds={2031: ["BUILD C WAS", "BUILD A WAS"]})
    g = game_with(["USA C TEX"], players={"USA": usa}, crisis=5)   # -> 6 after baseline: recession
    g.play_winter()
    assert len(g.units_of("USA")) == 1 + (2 - 1 - 1)
    g = game_with(["USA C TEX"], players={"USA": usa}, crisis=0)
    g.effects = Effects(climate_accord=True)
    g.play_winter()
    assert len(g.units_of("USA")) == 1 + 0    # 1 possible build, -1 for no Commons
    g = game_with(["USA C TEX"], players={"USA": usa}, crisis=0)
    g.effects = Effects(climate_accord=True)
    g.year_commons_issued = {"USA"}
    g.play_winter()
    assert len(g.units_of("USA")) == 2


def test_build_sites_and_forward_base():
    usa = Scripted(builds={2031: ["BUILD A MEX", "BUILD A BRA", "BUILD F TEX", "BUILD C KOR"]})
    g = game_with(["USA C MEX", "USA C BRA"], owners={"MEX": "USA", "BRA": "USA", "ARG": "USA"},
                  players={"USA": usa})
    g.play_winter()
    w = {o["text"]: o["result"] for o in g.phases[-1]["orders"]}
    assert w["BUILD A MEX"] == "ok"           # forward base: aligned, own civil unit, military slot empty
    assert w["BUILD A BRA"] == "void"         # only one forward base a year
    assert w["BUILD F TEX"] == "ok" and w["BUILD C KOR"] == "void"


def test_default_disbands_farthest_first():
    g = game_with(["USA F WAS", "USA C TEX", "USA C CAM", "USA I CAN"])
    g.play_winter()                           # 2 centres, 4 units: CAM (2 from TEX) then CAN (1)
    assert sorted(u.loc for u in g.units_of("USA")) == ["TEX", "WAS"]


def test_alignment_drift_and_home_capture():
    g = game_with(["USA C BRA", "CHN A OSA"], owners={"MEX": "USA"}, umbrellas=())
    g.play_winter()
    assert g.ownership["BRA"] == "USA" and g.ownership["OSA"] == "CHN"
    assert g.ownership["MEX"] == "USA"        # one empty winter: not yet
    g.year_commons = Counter()
    g.play_winter()
    assert g.ownership["MEX"] is None         # two consecutive winters without a US unit: drift
    g = game_with(["CHN A OSA"], umbrellas=(("USA", "JPN"),))
    g.play_winter()
    assert g.ownership["OSA"] == "JPN"        # umbrella-protected: no capture
    g = game_with(["USA A BRA", "CHN C BRA"], owners={"BRA": "USA"})
    g.play_winter()
    assert g.ownership["BRA"] == "USA"        # split: no change


def test_assembly_chair_votes_and_veto():
    # chair: fewest centres (USA 3, CHN 3, JPN 2 -> JPN); ties go by map order
    voters = {"USA": Scripted(votes={2031: "VOTE YES", 2032: "VOTE VETO"}),
              "CHN": Scripted(votes={2031: "VOTE VETO", 2032: "VOTE VETO"}),
              "JPN": Scripted(propose={2031: "PROPOSE ACCORD", 2032: "PROPOSE NAVIGATION MLC"},
                              votes={2031: "VOTE YES", 2032: "VOTE YES"})}
    g = game_with(["USA C TEX", "USA C MEX", "CHN C KOR"], owners={"MEX": "USA", "KOR": "CHN"}, players=voters)
    g.play_winter()
    a = g.phases[-1]["assembly"]
    assert a["chair"] == "JPN" and a["proposal"] == "ACCORD" and not a["passed"] and a["votes"]["CHN"] == "veto"
    g.play_winter()                           # CHN's second veto is void (counts as no); USA vetoes
    a = g.phases[-1]["assembly"]
    assert a["votes"]["CHN"] == "no" and a["votes"]["USA"] == "veto" and not a["passed"]
    voters["USA"].votes[2033] = "VOTE VETO"
    voters["CHN"].votes[2033] = "VOTE NO"
    voters["JPN"].proposals[2033] = "PROPOSE RECOGNITION MEX"
    g.play_winter()                           # USA's veto is spent: counts as NO; 1 yes vs 2 no
    a = g.phases[-1]["assembly"]
    assert a["votes"]["USA"] == "no" and not a["passed"]


def test_assembly_passes_and_applies_next_year():
    players = {"USA": Scripted(votes={2031: "VOTE YES"}, propose={2031: "PROPOSE SANCTIONS CHN"}),
               "CHN": Scripted(votes={2031: "VOTE NO"}), "JPN": Scripted(votes={2031: "VOTE YES"})}
    g = game_with(["CHN C GUA", "USA C TEX"], players=players)
    g.ownership.update({"MEX": "CHN", "KOR": "CHN", "VNM": "JPN", "IDN": "JPN"})   # USA has fewest -> chair
    g.play_winter()
    assert g.phases[-1]["assembly"]["passed"] and g.resolution == Resolution("sanctions", "CHN")
    assert "CHN" in g.effects.sanctions_regime
    _, why = g.position().validate("CHN", parse_order("C GUA - VNM"))
    assert why and "sanctions regime" in why


def test_recognition_unaligns_now():
    players = {p: Scripted(votes={2031: "VOTE YES"}) for p in ("USA", "CHN", "JPN")}
    players["USA"].proposals[2031] = "PROPOSE RECOGNITION BRA"
    g = game_with([], owners={"BRA": "CHN", "MEX": "CHN"}, players=players)
    g.play_winter()
    assert g.ownership["BRA"] is None and "BRA" in g.effects.recognition


def test_primacy_and_scoring():
    g = game_with(["USA C MEX", "USA C BRA"], rules=Rules(primacy=2))
    g.play_winter()
    assert g.result["kind"] == "primacy" and g.result["winner"] == "USA" and g.result["scores"]["USA"] == 100
    g = game_with(["USA C MEX"], rules=Rules(last_year=2031))
    g.play_winter()                           # USA 3, CHN 2, JPN 2 -> 9/17, 4/17, 4/17
    assert g.result["kind"] == "time"
    assert g.result["scores"] == {"USA": round(900 / 17, 2), "CHN": round(400 / 17, 2), "JPN": round(400 / 17, 2)}


def test_umbrella_declarations():
    players = {"USA": Scripted(umbrellas={2031: []}), "CHN": Scripted(umbrellas={2031: ["CHN UMBRELLA JPN",
                                                                                         "CHN UMBRELLA USA"]})}
    g = game_with([], players=players)
    g.play_winter()
    assert g.umbrellas == {("CHN", "JPN")}


# ---------------------------------------------------------------------------------------------- paradoxes
def test_cross_layer_support_paradox_cuts_supports():
    # A BRA - CAM backed by C BRA; C CAM - BRA backed by A CAM. Each support can only be cut by
    # dislodgement from its target, giving two consistent outcomes: the backup rule cuts both supports.
    res, t = resolve(["CHN A BRA", "CHN C BRA", "USA C CAM", "USA A CAM"],
                     ["CHN: A BRA - CAM", "CHN: C BRA S A BRA - CAM", "USA: C CAM - BRA",
                      "USA: A CAM S C CAM - BRA"], owners={"BRA": "CHN"})
    assert t == {"A BRA": "bounce", "C BRA": "cut", "C CAM": "bounce", "A CAM": "cut"} and not res.dislodged


def test_circular_movement_in_civil_layer():
    res, t = resolve(["USA C CAM", "CHN C BRA", "JPN I ARG"],
                     ["USA: C CAM - BRA", "CHN: C BRA - ARG", "JPN: I ARG - CAM"])
    assert set(t.values()) == {"ok"}
    assert where(res) == {"USA C BRA", "CHN C ARG", "JPN I CAM"}
    res, t = resolve(["USA C CAM", "CHN C BRA"], ["USA: C CAM - BRA", "CHN: C BRA - CAM"])
    assert set(t.values()) == {"bounce"}      # a land swap bounces in either layer


def test_capital_sea_moves_pass_each_other():
    res, t = resolve(["USA C TEX", "CHN C BRA"], ["USA: C TEX - BRA", "CHN: C BRA - MEX"])
    assert t["C TEX"] == "ok" and t["C BRA"] == "ok"   # trade route TEX-BRA; hop BRA-MEX over CAR
    res, t = resolve(["USA C MEX", "CHN C BRA"], ["USA: C MEX - BRA", "CHN: C BRA - MEX"])
    assert set(t.values()) == {"ok"}          # hops by sea swap like a convoy, no head-to-head
