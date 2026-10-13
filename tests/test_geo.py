"""Checks on the geometry pipeline's outputs (build/map.json, web/public/board.json) and a few helpers.

The output checks need only the committed JSON; the helper tests need the `geo` extra.
"""

import json
from collections import deque
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "build" / "map.json"
BOARD = ROOT / "web" / "public" / "board.json"


@pytest.fixture(scope="module")
def m():
    if not MAP.exists():
        pytest.skip("build/map.json missing: run python -m brink.geo.build")
    return json.loads(MAP.read_text())


@pytest.fixture(scope="module")
def board():
    if not BOARD.exists():
        pytest.skip("web/public/board.json missing: run python -m brink.geo.build")
    return json.loads(BOARD.read_text())


def test_counts(m):
    groups = {}
    for p in m["provinces"].values():
        groups[p["group"]] = groups.get(p["group"], 0) + 1
    assert groups == {"home": 21, "swing": 20, "filler": 19, "impassable": 4, "sea": 28, "chokepoint": 7}


def test_adjacency_symmetric_and_no_impassable(m):
    imp = {p for p, v in m["provinces"].items() if v["impassable"]}
    for kind, d in m["adj"].items():
        for a, bs in d.items():
            assert a not in imp
            for b in bs:
                assert b not in imp, (kind, a, b)
                assert a in d[b], (kind, a, b)


def test_any_is_union(m):
    A = m["adj"]
    for p in A["any"]:
        assert set(A["any"][p]) == set(A["army"].get(p, [])) | set(A["fleet"].get(p, []))


def test_army_only_land_and_fleet_land_is_coastal(m):
    P = m["provinces"]
    for a, bs in m["adj"]["army"].items():
        if bs:
            assert all(P[x]["kind"] == "land" for x in [a, *bs])
    for a, bs in m["adj"]["fleet"].items():
        if P[a]["kind"] == "land" and bs:
            assert P[a]["coastal"], a


def test_connected(m):
    P = m["provinces"]
    nodes = {p for p, v in P.items() if not v["impassable"] and not v["opens"]}
    start = next(iter(nodes))
    seen, q = {start}, deque([start])
    while q:
        for b in m["adj"]["any"][q.popleft()]:
            if b in nodes and b not in seen:
                seen.add(b)
                q.append(b)
    assert seen == nodes


def test_trade_routes_are_chains(m):
    F = m["adj"]["fleet"]
    for r in m["trade_routes"]:
        chain = [r["ends"][0], *r["path"], r["ends"][1]]
        for a, b in zip(chain, chain[1:]):
            assert b in F[a], (r["id"], a, b)


def test_ocean_flag(m):
    oceans = {p for p, v in m["provinces"].items() if v.get("ocean")}
    assert oceans == {"NAO", "MAO", "SAO", "IOC", "NWP", "CPA", "NEP", "EPA", "SPA", "ARO"}
    for p, v in m["provinces"].items():
        if v["kind"] == "sea":
            assert isinstance(v["ocean"], bool), p


def test_start_units(m):
    P = m["provinces"]
    for u in m["start"]["units"]:
        assert P[u["at"]]["kind"] == "land"
        if u["type"] == "F":
            assert P[u["at"]]["coastal"]


def test_board_matches_map(m, board):
    assert set(board["provinces"]) == set(m["provinces"])
    for pid, p in board["provinces"].items():
        assert p["d"].startswith("M"), pid
        assert "military" in p["slots"]
        if m["provinces"][pid]["kind"] == "land":
            assert "civil" in p["slots"]
    assert set(board["routes"]) == {r["id"] for r in m["trade_routes"]}
    assert BOARD.stat().st_size < 1.5e6


def test_name_normaliser():
    pytest.importorskip("shapely")
    from brink.geo.land import norm

    assert norm("Hokkaidō") == norm("Hokkaido")
    assert norm("`Asir") == norm("'Asir") == "asir"
    assert norm("Al-Qassim") == norm("Al Qassim")
    assert norm("Ōsaka") == "osaka"


def test_spherical_voronoi_cell_is_nearest_seed():
    np = pytest.importorskip("numpy")
    shapely = pytest.importorskip("shapely")
    from brink.geo.seas import _spherical_cell, _unit

    seeds = [(0.0, 0.0), (20.0, 0.0), (0.0, 30.0), (170.0, 10.0), (-170.0, 10.0)]
    S = np.array([_unit(lon, lat) for lon, lat in seeds])
    cell = _spherical_cell(3, S, *seeds[3])
    # points just across the antimeridian from seed 3 belong to seed 4, not 3
    assert cell.contains(shapely.Point(175, 10))
    assert not cell.contains(shapely.Point(185, 10))  # = -175, nearer to seed 4
