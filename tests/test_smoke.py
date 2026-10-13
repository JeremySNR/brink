"""Smoke test: many random-bot games on the fixture map run without exceptions and produce valid records."""
from __future__ import annotations

import json
import os
import time

from brink.bots.random_bot import RandomBot
from brink.game import Game, Rules
from brink.record import validate_record
from helpers import mini

N = int(os.environ.get("BRINK_SMOKE_GAMES", "200"))


def _play(seed: int) -> dict:
    gmap = mini()
    # half the games use a cheap Commons rate so they reach 2040 instead of collapsing
    rules = Rules(commons_per_point=1 if seed % 2 else 4, primacy=4 if seed % 5 == 0 else 11)
    players = {p: RandomBot(seed=seed * 100 + i) for i, p in enumerate(gmap.power_order)}
    return Game(gmap, players, seed=seed, rules=rules, map_name="mini").run()


def test_random_games_complete_and_validate():
    t0 = time.perf_counter()
    kinds, phases = {}, 0
    for seed in range(N):
        rec = _play(seed)
        errs = validate_record(rec)
        assert not errs, (seed, errs[:5])
        json.dumps(rec)   # serialisable
        kinds[rec["result"]["kind"]] = kinds.get(rec["result"]["kind"], 0) + 1
        phases += len(rec["phases"])
        # units never share a slot; the record's first phase is the start position
        for ph in rec["phases"]:
            slots = [(u["at"].split("/")[0], u["type"] in "AF") for u in ph["units"]]
            assert len(slots) == len(set(slots)), (seed, ph["name"])
    dt = time.perf_counter() - t0
    print(f"\n{N} games, {phases} phases in {dt:.1f}s: {N / dt:.1f} games/s, {phases / dt:.0f} phases/s; "
          f"endings {kinds}")
    assert set(kinds) >= {"time", "collapse"}


def test_games_are_deterministic():
    assert json.dumps(_play(7)) == json.dumps(_play(7))
