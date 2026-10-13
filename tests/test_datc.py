"""MILA's DATC suite (diplomacy/tests/test_datc.py), run unchanged against the Brink engine.

Only the hooks are replaced: games are `ClassicGame`s (Brink adjudicator on the converted standard map)
and result codes are mapped onto Brink's results. Unit positions and dislodgements are checked by the
original assertions. Adjustment-phase cases (6.I builds, 6.J civil disorder and the W1901A case in 6.A)
are skipped: Brink's Winter is a different system (see test_game.py).
"""
from __future__ import annotations

import pytest

pytest.importorskip("diplomacy")

from diplomacy.tests import test_datc as mila_datc  # noqa: E402
from diplomacy.utils import order_results as R  # noqa: E402

from mila_bridge import ClassicGame  # noqa: E402

ADJUSTMENT_CASES = {"test_6_a_12", "test_6_b_14", "test_6_k_1"} | {f"test_6_i_{i}" for i in range(1, 8)} | {
    f"test_6_j_{i}" for i in range(1, 12)}

# Result-code mapping. MILA can give a unit several codes; Brink gives one result plus flags.
CODE_FLAGS = {
    R.BOUNCE: {"bounce"},
    R.CUT: {"cut"},
    R.DISLODGED: {"dislodged"},
    R.VOID: {"void"},
    # MILA marks both a convoyed army without a working convoy and the unused/broken convoy orders
    R.NO_CONVOY: {"no_path", "failed", "void"},
    R.DISRUPTED: {"no_path", "failed", "dislodged"},
}


def _status(game: ClassicGame, unit: str) -> set | None:
    if unit in game.status:
        return game.status[unit]
    t, loc = unit.split()
    for k, v in game.status.items():
        kt, kloc = k.split()
        if kt == t and kloc.split("/")[0] == loc.split("/")[0]:
            return v
    return None


class TestBrinkDATC(mila_datc.TestDATC):
    @staticmethod
    def create_game():
        return ClassicGame()

    @staticmethod
    def process(game):
        game.process()

    @staticmethod
    def owner_name(game, unit):
        return game.unit_owner(unit)

    @staticmethod
    def move_to_phase(game, new_phase):
        game.set_current_phase(new_phase)

    @staticmethod
    def check_results(game, unit, value, phase="M"):
        st = _status(game, unit)
        if phase == "R":
            popped = any(p.split()[1].split("/")[0] == unit.split()[1].split("/")[0] and p[0] == unit[0]
                         for p in game.popped)
            if value == R.DISBAND:
                return popped
            if value == R.OK:
                return not popped and (st is None or not (st & {"void", "bounce"}))
            if value == R.VOID:
                return st is not None and "void" in st
            if value == R.BOUNCE:
                return st is not None and "bounce" in st
            return False
        # A support for an attack on the supporter's own (staying) unit: Brink says 'void'; MILA says VOID
        # or OK depending on its internal order of evaluation. Label-only difference: accept either.
        if st is not None and "own_target" in st and value in (R.VOID, R.OK):
            return True
        if value == R.VOID:
            return st is None or "void" in st
        if st is None:
            return False
        if value == R.OK:
            return st <= {"ok"}
        return bool(st & CODE_FLAGS[value])


def _wrap_skips():
    for name in dir(TestBrinkDATC):
        if name in ADJUSTMENT_CASES:
            setattr(TestBrinkDATC, name, pytest.mark.skip(reason="adjustment phase: Brink Winter differs")(
                getattr(mila_datc.TestDATC, name)))


_wrap_skips()
