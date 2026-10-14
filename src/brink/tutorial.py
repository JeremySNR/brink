"""The tutorial's sandbox: hand-placed positions played through the real engine.

The tutorial (web/src/tutorial.ts) sets up small positions ("your Capital in California, a European
Capital eyeing Brazil") and has the learner play them with the real order entry. Every resolution is the
engine's: this module only builds a state from a lesson's setup and advances it, with the other powers
playing the lesson's scripted orders (anything unscripted holds, keeps its umbrellas and builds nothing).

Stateless, like brink.session: the client holds the state and sends it back with each step.

    start(gmap, setup)                       -> view    a position from {year, season, units, ownership, ...}
    validate(gmap, state, power, orders)     -> [{text, ok, order, reason}]
    step(gmap, state, power, orders, script) -> view    resolves the learner's decision, then any decision
                                                        point the learner isn't part of, until theirs again

view = {"state", "decision" (brink.session.pending for the learner, or null), "phases", "result"}.
script = {"S2031M": {"EU": ["C PAR - BRA"]}, ...}: orders by phase name, then power.
"""
from __future__ import annotations

from .bots.base import Player
from .game import Game
from .map import BrinkMap
from . import session as S

MAX_UNITS = 40
AUTO_STEPS = 6          # decision points resolved without the learner per step (a propose step, retreats)
YEARS = (2031, 2040)


class TutorialError(ValueError):
    """A setup or request the sandbox won't play."""


def _check_setup(gmap: BrinkMap, setup: dict) -> None:
    units = setup.get("units") or []
    if not isinstance(units, list) or len(units) > MAX_UNITS:
        raise TutorialError(f"units: a list of at most {MAX_UNITS}")
    for u in units:
        if not isinstance(u, dict) or u.get("power") not in gmap.power_order or u.get("type") not in "AFCI" \
                or not isinstance(u.get("at"), str) or u["at"].split("/")[0] not in gmap.provinces:
            raise TutorialError(f"bad unit {u!r}")
    for pid, owner in (setup.get("ownership") or {}).items():
        if pid not in gmap.provinces or (owner is not None and owner not in gmap.power_order):
            raise TutorialError(f"bad ownership {pid}: {owner!r}")
    year = setup.get("year", YEARS[0])
    if not isinstance(year, int) or not YEARS[0] <= year <= YEARS[1]:
        raise TutorialError(f"year must be {YEARS[0]}..{YEARS[1]}")
    if setup.get("season", "spring") not in ("spring", "fall"):
        raise TutorialError("season must be spring or fall")


def start(gmap: BrinkMap, setup: dict) -> dict:
    """A movement phase at the setup's position. Unlisted fields keep the real start's values."""
    _check_setup(gmap, setup)
    power = setup.get("power", "USA")
    state = S.new_game(gmap, seed=int(setup.get("seed", 1)), humans=[power], bot_kind="hold")
    state["year"] = setup.get("year", state["year"])
    state["season"] = setup.get("season", "spring")
    if "units" in setup:
        state["units"] = [{"power": u["power"], "type": u["type"], "at": u["at"]} for u in setup["units"]]
    if "ownership" in setup:
        own = {pid: None for pid in state["ownership"]}
        own.update(setup["ownership"])
        state["ownership"] = own
    if "crisis" in setup:
        state["crisis"] = max(0, min(9, int(setup["crisis"])))
    if "umbrellas" in setup:
        state["umbrellas"] = sorted([list(x) for x in setup["umbrellas"]])
    return view(gmap, state)


def view(gmap: BrinkMap, state: dict) -> dict:
    power = _learner(state)
    p = S.pending(gmap, state, [power])
    decision = p if p and power in p["powers"] else None
    return {"state": state, "decision": decision, "phases": state["phases"], "result": state["result"]}


def validate(gmap: BrinkMap, state: dict, power: str, orders: list[str]) -> list[dict]:
    return S.validate(gmap, state, power, orders)


def step(gmap: BrinkMap, state: dict, power: str, orders: list[str], script: dict | None = None) -> dict:
    """Resolve the learner's open decision with `orders`, then play on until they decide again."""
    if power != _learner(state):
        raise TutorialError("that's not the learner's power")
    script = script or {}
    state = S.advance(gmap, state, {**_scripted(gmap, state, script), power: list(orders or [])}, bots=_holders(gmap))
    for _ in range(AUTO_STEPS):
        if state.get("result") is not None:
            break
        p = S.pending(gmap, state, [])
        if p is None or power in p["powers"]:
            break
        state = S.advance(gmap, state, _scripted(gmap, state, script), bots=_holders(gmap))
    return view(gmap, state)


def _learner(state: dict) -> str:
    humans = (state.get("meta") or {}).get("humans") or []
    if len(humans) != 1:
        raise TutorialError("a tutorial state has exactly one learner")
    return humans[0]


def _scripted(gmap: BrinkMap, state: dict, script: dict) -> dict:
    """The script's orders for the open decision point, by power."""
    at = script.get(S._phase_name(Game.from_state(gmap, state))) or {}
    if state["stage"] == "propose":   # the chair proposes from the script's PROPOSE line, if any
        at = {p: [o for o in v if o.upper().startswith("PROPOSE")] for p, v in at.items()}
    return {p.upper(): list(v) for p, v in at.items() if isinstance(v, list)}


def _holders(gmap: BrinkMap) -> dict[str, Player]:
    return {p: Player() for p in gmap.power_order}


def handle(gmap: BrinkMap, action: str, body: dict) -> dict:
    """The sandbox API, shared by the hosted API and `brink serve`: POST /api/tutorial/<action>."""
    try:
        if action == "start":
            return start(gmap, body.get("setup") or {})
        state = body.get("state")
        if not isinstance(state, dict):
            raise TutorialError("state is required")
        orders = [str(o) for o in (body.get("orders") or []) if str(o).strip()]
        if action == "validate":
            return {"results": validate(gmap, state, _learner(state), orders)}
        if action == "step":
            return step(gmap, state, _learner(state), orders, body.get("script") or {})
    except TutorialError:
        raise
    except (KeyError, TypeError, ValueError, S.GameOver) as e:
        raise TutorialError(f"can't play that position: {e}") from e
    raise TutorialError(f"no tutorial action {action!r}")
