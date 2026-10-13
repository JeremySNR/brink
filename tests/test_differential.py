"""Randomised differential test: Brink's adjudicator vs MILA `diplomacy` on the classic 1901 board.

Random legal A/F orders (with coordinated supports and convoys) are played in MILA from 1901 for a random
walk of several years; each movement phase is also adjudicated by Brink and compared: unit positions,
dislodged units (+ attacker origin), retreat options, and positions after random retreats.

Known MILA deviations are detected, proven (see mila_bridge.differential_game) and counted, not failed:
* convoy_rejected_by_mila: MILA only accepts convoy orders on a minimal fleet chain (DATC allows any chain);
  such orders are dropped on both sides.
* mila_convoy_retreat_bug: MILA skips the standoff/occupancy checks for retreats when the attacker came by
  convoy (operator precedence in Game._move_results).
* mila_adjacent_convoy_fallback: MILA lets an Army whose convoy to an adjacent province was disrupted fall
  back to the land route; the 2000 rulebook (DATC 4.A.3) makes it fail. Proven by re-adjudicating.

Scale with BRINK_DIFF_GAMES (default 150 games per weighting, 3 weightings, up to 14 phases each).
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("diplomacy")

import mila_bridge as mb  # noqa: E402

GAMES = int(os.environ.get("BRINK_DIFF_GAMES", "150"))
WEIGHTINGS = {
    "default": None,
    "convoy-heavy": {"convoy": 0.35, "support": 0.4, "move": 0.25, "hold": 0.0},
    "support-heavy": {"support": 0.6, "move": 0.4, "hold": 0.0, "convoy": 0.2},
}


@pytest.mark.parametrize("label", list(WEIGHTINGS))
def test_differential_vs_mila(label):
    mb.STATS.clear()
    phases, problems = 0, []
    base = {"default": 0, "convoy-heavy": 100000, "support-heavy": 200000}[label]
    for seed in range(base, base + GAMES):
        n, p = mb.differential_game(seed, 14, WEIGHTINGS[label])
        phases += n
        problems += p
    print(f"\n{label}: {phases} movement phases compared, stats {dict(mb.STATS)}")
    assert phases >= GAMES * 10
    assert not problems, "\n".join(problems[:10])
