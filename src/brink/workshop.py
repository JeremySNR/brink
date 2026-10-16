"""Account-free practice and isolated, all-power sandboxes over the real engine.

Requests contain a copy of state. This module never reads or writes hosted games,
submissions, messages or accounts. Bot decisions are only generated in practice.
"""
from __future__ import annotations

import copy
import json
from dataclasses import asdict

from . import session as S
from .game import Game, Rules
from .map import BrinkMap


class WorkshopError(ValueError):
    pass


def snapshot(state: dict, powers: list[str]) -> dict:
    """Public game mechanics, without identities, bot seeds or bot alliances."""
    state = copy.deepcopy(state)
    state["seed"] = 1
    state["players"] = {p: "sandbox" for p in powers}
    state["meta"] = {"humans": powers, "bot_kind": "hold", "alliances": []}
    return state


def _state(gmap: BrinkMap, value) -> dict:
    if not isinstance(value, dict) or len(json.dumps(value)) > 2_000_000:
        raise WorkshopError("A saved position of at most 2 MB is required.")
    state = copy.deepcopy(value)
    finished = state["stage"] == "over" and isinstance(state.get("result"), dict)
    if not 2031 <= state["year"] <= 2041 or (state["stage"] not in S.KINDS and not finished):
        raise WorkshopError("That year or decision is not supported.")
    if len(state["units"]) > 160 or len(state["phases"]) > 200:
        raise WorkshopError("That position is too large.")
    for u in state["units"]:
        if u["power"] not in gmap.powers or u["type"] not in ("A", "F", "C", "I") or u["at"].split("/")[0] not in gmap.provinces:
            raise WorkshopError("That position contains an unknown unit.")
    if any(p not in gmap.provinces or (owner is not None and owner not in gmap.powers)
           for p, owner in state["ownership"].items()):
        raise WorkshopError("That position contains unknown ownership.")
    defaults = asdict(Rules())
    for key, value in state["rules"].items():
        if key not in defaults or not isinstance(value, (int, bool)) or value < 0 or value > 2041:
            raise WorkshopError("That position has invalid rules.")
    rules = Rules(**state["rules"])
    if rules.commons_per_point < 1 or rules.drift_winters < 1 or not 2031 <= rules.last_year <= 2040:
        raise WorkshopError("That position has invalid rules.")
    # Deserialize now, before returning any position to the board.
    Game.from_state(gmap, state)
    return state


def _orders(value) -> list[str]:
    if not isinstance(value, list) or len(value) > 160 or any(not isinstance(t, str) or len(t) > 200 for t in value):
        raise WorkshopError("Orders must be a list of at most 160 short order texts.")
    return value


def view(gmap: BrinkMap, state: dict, power: str, mode: str) -> dict:
    decision = S.pending(gmap, state, None if mode == "sandbox" else [power])
    return {"state": state, "decision": decision, "phases": state["phases"], "result": state["result"]}


def handle(gmap: BrinkMap, action: str, body: dict) -> dict:
    try:
        mode = body.get("mode", "practice")
        power = body.get("power", "USA")
        if mode not in ("practice", "sandbox") or power not in gmap.powers:
            raise WorkshopError("Choose a valid mode and power.")
        if action == "start":
            if mode != "practice":
                raise WorkshopError("A sandbox starts from a copied position.")
            state = S.new_game(gmap, humans=[power], bot_kind="strategist", rules=Rules(last_year=2034))
        else:
            state = _state(gmap, body.get("state"))
            # Never trust client-specified bot kinds or alliances on the public endpoint.
            state["meta"] = {"humans": [power] if mode == "practice" else list(gmap.power_order),
                             "bot_kind": "strategist" if mode == "practice" else "hold", "alliances": []}
            if mode == "practice":
                state["rules"]["last_year"] = 2034
            if action == "validate":
                return {"results": S.validate(gmap, state, power, _orders(body.get("orders", [])))}
            if action == "step":
                if mode == "practice":
                    decisions = {power: _orders(body.get("orders", []))}
                else:
                    decisions = body.get("decisions", {})
                    if not isinstance(decisions, dict) or any(p not in gmap.powers for p in decisions):
                        raise WorkshopError("Choose orders for valid powers.")
                    decisions = {p: _orders(o) for p, o in decisions.items()}
                state = S.advance(gmap, state, decisions, bots={} if mode == "sandbox" else None)
                if mode == "practice":
                    state = S.run_bots_until_human(gmap, state, max_steps=64)
            elif action != "view":
                raise WorkshopError("Unknown workshop action.")
        return view(gmap, state, power, mode)
    except WorkshopError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, S.GameOver) as e:
        raise WorkshopError(f"Cannot use that position: {e}") from e
