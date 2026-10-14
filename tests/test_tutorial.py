"""The tutorial: every lesson in web/public/tutorial.json, played through the real engine (brink.tutorial).

Each play chapter's decisions are answered with their `solution` orders; every `result` check the coach
relies on must then hold. A rules change that breaks a lesson fails here, not in front of a new player."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from brink import tutorial as T
from brink.map import BrinkMap

try:
    from test_hosted import client, db_url  # noqa: F401  (fixtures for the HTTP test)
except Exception:   # fastapi / pgserver not installed
    pass

ROOT = Path(__file__).resolve().parents[1]
LESSONS = json.loads((ROOT / "web" / "public" / "tutorial.json").read_text())
MAP = ROOT / "build" / "map.json"


@pytest.fixture(scope="module")
def gmap():
    if not MAP.exists():
        pytest.skip("build/map.json not built")
    return BrinkMap.load(MAP)


def holds(cond: str, phase: dict, position: dict) -> bool:
    """The coach's checks (web/src/tutorial.ts `holds`): order:<text>=<result>, owner:<pid>=<power>,
    unit:<power> <type> <pid>."""
    kind, _, arg = cond.partition(":")
    if kind == "order":
        text, _, res = arg.rpartition("=")
        return any(o["text"] == text and o["result"] == res for o in phase["orders"])
    if kind == "owner":
        pid, _, power = arg.partition("=")
        return (position["ownership"].get(pid) or "") == power
    if kind == "unit":
        power, typ, at = arg.split()
        return any(u["power"] == power and u["type"] == typ and u["at"] == at for u in position["units"])
    raise AssertionError(f"unknown check {cond!r}")


PLAY = [c for c in LESSONS["chapters"] if c["kind"] == "play"]


@pytest.mark.parametrize("chapter", PLAY, ids=[c["id"] for c in PLAY])
def test_lesson_solutions_meet_every_check(gmap, chapter):
    v = T.start(gmap, chapter["setup"])
    seen = set()
    for _ in range(12):
        d = v["decision"]
        if d is None or d["phase"] == chapter["end"]:
            break
        step = chapter["steps"].get(d["phase"])
        assert step is not None, f"{chapter['id']}: no coach step for {d['phase']}"
        before = len(v["phases"])
        v = T.step(gmap, v["state"], LESSONS["power"], step.get("solution", []), chapter.get("script"))
        seen.add(d["phase"])
        position = v["decision"]["position"] if v["decision"] else v["phases"][-1]
        for ph in v["phases"][before:]:
            r = chapter.get("results", {}).get(ph["name"])
            if r:
                assert holds(r["check"], ph, position), f"{chapter['id']} {ph['name']}: {r['check']}"
    assert v["decision"] and v["decision"]["phase"] == chapter["end"], f"{chapter['id']} never reached {chapter['end']}"
    assert seen == set(chapter["steps"]), f"{chapter['id']}: steps never reached: {set(chapter['steps']) - seen}"


def test_every_setup_starts(gmap):
    for c in LESSONS["chapters"]:
        v = T.start(gmap, c.get("setup") or {})
        assert v["decision"] and v["decision"]["kind"] == "orders"


def test_sandbox_rejects_nonsense(gmap):
    with pytest.raises(T.TutorialError):
        T.handle(gmap, "start", {"setup": {"units": [{"power": "XXX", "type": "A", "at": "TEX"}]}})
    with pytest.raises(T.TutorialError):
        T.handle(gmap, "step", {"state": {"v": 99}})
    with pytest.raises(T.TutorialError):
        T.handle(gmap, "nope", {})


def test_hosted_endpoint_needs_no_sign_in(gmap, request):
    pytest.importorskip("fastapi")
    pytest.importorskip("pgserver")
    client = request.getfixturevalue("client")
    r = client.post("/api/tutorial/start", json={"setup": LESSONS["chapters"][1]["setup"]})
    assert r.status_code == 200 and r.json()["decision"]["kind"] == "orders"
    state = r.json()["state"]
    r = client.post("/api/tutorial/validate", json={"state": state, "orders": ["C CAL - MEX", "A TEX - MEX"]})
    assert [x["ok"] for x in r.json()["results"]] == [True, False]   # no Army into an unaligned state
    assert client.post("/api/tutorial/start", json={"setup": {"year": 1999}}).status_code == 400
