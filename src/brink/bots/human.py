"""HumanPlayer: a Player whose decisions come from outside the game thread (the web UI via brink.server).

The engine calls the Player methods synchronously from the thread running `Game.run()`. Each call here
publishes a *decision* (what must be decided now, with every legal option, built while the engine is
paused so the game state is consistent) and blocks until `answer()` is called from another thread.

    human = HumanPlayer()
    threading.Thread(target=game.run).start()
    d = human.wait_decision()            # -> Decision | None (None: the game ended)
    human.answer(d.id, ["A TEX - MEX"])  # the game thread resumes

`abort()` releases a blocked game thread: the pending Player call raises `GameAborted`, which unwinds
`Game.run()`; the thread owner catches it.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..orders import Build, Hold, Order, OrderError, Remove, Umbrella, Vote, order_json, parse_order
from ..state import Unit, layer_of, prov_of
from .base import Player

if TYPE_CHECKING:  # pragma: no cover
    from ..game import Game


class GameAborted(Exception):
    """Raised inside the game thread when the session is closed while waiting for the human."""


@dataclass
class Decision:
    id: int
    kind: str                 # orders | retreats | propose | vote | adjust | umbrellas
    power: str
    phase: dict               # the live position, in game-record phase format (orders empty)
    options: dict             # kind-specific, JSON-ready
    # server-side only: what validation needs (the engine is paused while this decision is open)
    ctx: dict = field(default_factory=dict, repr=False)

    def to_json(self) -> dict:
        return {"id": self.id, "kind": self.kind, "power": self.power, "phase": self.phase, **self.options}


def _unit_json(u: Unit) -> dict:
    return {"type": u.type, "at": u.loc}


def _opt(power: str, o: Order) -> dict:
    d = order_json(power, o)
    d.pop("power", None)
    return d


class HumanPlayer(Player):
    name = "human"

    def __init__(self, timeout: float | None = None):
        self._cv = threading.Condition()
        self._decision: Decision | None = None
        self._answer: tuple[int, list[str] | str | None] | None = None
        self._seq = 0
        self._aborted = False
        self._finished = False
        self.timeout = timeout   # seconds to wait for an answer before defaulting (None = forever)

    # ------------------------------------------------------------------ control side (other threads)
    @property
    def decision(self) -> Decision | None:
        return self._decision

    def wait_decision(self, timeout: float | None = None, after: int = 0) -> Decision | None:
        """Block until a decision with id > `after` is open, or the game is finished/aborted.
        Returns the open decision (or None)."""
        with self._cv:
            self._cv.wait_for(lambda: self._finished or self._aborted
                              or (self._decision is not None and self._decision.id > after), timeout)
            return self._decision

    def answer(self, decision_id: int, response: list[str] | str | None) -> bool:
        """Hand the response to the waiting engine. False if `decision_id` is not the open decision."""
        with self._cv:
            if self._decision is None or self._decision.id != decision_id:
                return False
            self._answer = (decision_id, response)
            self._decision = None
            self._cv.notify_all()
            return True

    def finish(self) -> None:
        """Called by the thread owner when Game.run() returns (or fails): wakes any waiter."""
        with self._cv:
            self._finished = True
            self._decision = None
            self._cv.notify_all()

    def abort(self) -> None:
        with self._cv:
            self._aborted = True
            self._cv.notify_all()

    @property
    def finished(self) -> bool:
        return self._finished

    # ------------------------------------------------------------------ engine side (game thread)
    def _ask(self, game: "Game", kind: str, power: str, phase: dict, options: dict, ctx: dict | None = None):
        with self._cv:
            if self._aborted:
                raise GameAborted()
            self._seq += 1
            d = Decision(self._seq, kind, power, phase, options, ctx or {})
            self._decision = d
            self._answer = None
            self._cv.notify_all()
            ok = self._cv.wait_for(lambda: self._aborted or (self._answer is not None and self._answer[0] == d.id),
                                   self.timeout)
            if self._aborted:
                self._decision = None
                raise GameAborted()
            if not ok:   # timed out: the engine applies its defaults
                self._decision = None
                return None
            return self._answer[1]

    @staticmethod
    def _live_phase(game: "Game", kind: str) -> dict:
        return game._phase(kind, game.units)

    # movement --------------------------------------------------------
    def orders(self, game: "Game", power: str) -> list[str]:
        options, ctx = movement_payload(game, power)
        res = self._ask(game, "orders", power, self._live_phase(game, "movement"), options, ctx)
        return _as_list(res)

    def retreats(self, game: "Game", power: str, options: dict[Unit, list[str]]) -> list[str]:
        payload, ctx = retreat_payload(game, power, options)
        ph = self._live_phase(game, "retreat")
        last = game.phases[-1] if game.phases else None
        if last and last.get("kind") == "movement":
            ph["dislodged"] = last.get("dislodged", [])
        return _as_list(self._ask(game, "retreats", power, ph, payload, ctx))

    # winter ----------------------------------------------------------
    def propose(self, game: "Game", power: str) -> str | None:
        payload, ctx = propose_payload(game, power)
        res = self._ask(game, "propose", power, self._live_phase(game, "winter"), payload, ctx)
        return normalise_proposal(res)

    def vote(self, game: "Game", power: str, proposal: str) -> str:
        payload, ctx = vote_payload(game, power, proposal)
        res = self._ask(game, "vote", power, self._live_phase(game, "winter"), payload, ctx)
        return normalise_vote(res) or "VOTE NO"

    def adjust(self, game: "Game", power: str, change: int) -> list[str]:
        payload, ctx = adjust_payload(game, power, change)
        res = self._ask(game, "adjust", power, self._live_phase(game, "winter"), payload, ctx)
        return _as_list(res)

    def umbrellas(self, game: "Game", power: str) -> list[str] | None:
        if not game.map.nuclear(power):
            return None
        payload, ctx = umbrella_payload(game, power)
        res = self._ask(game, "umbrellas", power, self._live_phase(game, "winter"), payload, ctx)
        if res is None:
            return None
        cands = payload["candidates"]
        return [Umbrella(power, p).text() if p in cands else p for p in _as_list(res)]

    # ------------------------------------------------------------------ validation (control side)
    def validate(self, game: "Game", texts: list[str]) -> list[dict]:
        """Check order texts against the open decision. Each result: {text, ok, order, reason}.
        Only meaningful while a decision is open (the engine is paused then)."""
        d = self._decision
        if d is None:
            return [{"text": t, "ok": False, "order": None, "reason": "no decision is open"} for t in texts]
        return check_orders(game, d.kind, d.power, texts, d.ctx)


# ---------------------------------------------------------------------- decision payloads
# Each returns (options, ctx): `options` is the JSON the client sees (merged into the decision payload),
# `ctx` is what `check_orders` needs to validate drafts. Shared by HumanPlayer and brink.session.

def movement_payload(game: "Game", power: str) -> tuple[dict, dict]:
    pos = game.position()
    units = []
    for u in sorted(game.units_of(power), key=lambda x: (x.layer, x.loc)):
        opts = [Hold(u.type, u.loc)] + list(pos.legal_moves(u)) + pos.legal_supports(u) \
            + pos.legal_convoys(u) + pos.legal_specials(u)
        units.append({
            "unit": _unit_json(u), "name": _unit_name(game, u),
            "options": [_opt(power, o) for o in opts],
        })
    withdraw = sorted(b for a, b in game.umbrellas if a == power)
    return {"units": units, "withdraw": withdraw, **_power_info(game, power)}, {"position": pos}


def retreat_payload(game: "Game", power: str, options: dict[Unit, list[str]]) -> tuple[dict, dict]:
    mine = {u: d for u, d in options.items() if u.power == power}
    units = [{"unit": _unit_json(u), "name": _unit_name(game, u), "to": list(dests)}
             for u, dests in sorted(mine.items(), key=lambda kv: kv[0].loc)]
    return ({"units": units, **_power_info(game, power)},
            {"options": {(u.type, prov_of(u.loc)): (u, list(d)) for u, d in mine.items()}})


def propose_payload(game: "Game", power: str) -> tuple[dict, dict]:
    opts = [r.text() for r in game.resolution_options(power)]
    return {"resolutions": opts, "chair": power, **_power_info(game, power)}, {"resolutions": set(opts)}


def vote_payload(game: "Game", power: str, proposal: str) -> tuple[dict, dict]:
    return {"proposal": proposal, "chair": _chair(game), "can_veto": game.can_veto(power),
            "weight": game.votes_weight(power), **_power_info(game, power)}, {}


def adjust_payload(game: "Game", power: str, change: int) -> tuple[dict, dict]:
    if change > 0:
        builds = [{"type": b.utype, "at": b.loc, "text": b.text(),
                   "forward_base": game.is_forward_base(power, b)} for b in game.build_options(power)]
        options = {"change": change, "builds": builds, "disband": []}
    else:
        options = {"change": change, "builds": [],
                   "disband": [{"unit": _unit_json(u), "name": _unit_name(game, u),
                                "text": Remove(u.type, u.loc).text()} for u in game.units_of(power)]}
    return {**options, **_power_info(game, power)}, {"change": change}


def umbrella_payload(game: "Game", power: str) -> tuple[dict, dict]:
    cands = [p for p in game.active_powers() if p != power and not game.map.nuclear(p)]
    current = sorted(b for a, b in game.umbrellas if a == power)
    return {"candidates": cands, "current": current, **_power_info(game, power)}, {"candidates": cands}


def normalise_proposal(res) -> str | None:
    if isinstance(res, list):
        res = res[0] if res else None
    if not res or not str(res).strip():
        return None
    res = str(res).strip()
    return res if res.upper().startswith("PROPOSE") else f"PROPOSE {res}"


def normalise_vote(res) -> str | None:
    if isinstance(res, list):
        res = res[0] if res else None
    if not res or not str(res).strip():
        return None
    res = str(res).strip()
    return res if res.upper().startswith("VOTE") else f"VOTE {res}"


def check_orders(game: "Game", kind: str, power: str, texts: list[str], ctx: dict) -> list[dict]:
    """Validate draft order texts for one decision (kind: orders | retreats | propose | vote | adjust |
    umbrellas; ctx from the matching *_payload). Each result: {text, ok, order, reason}."""
    out = []
    built: list[Build] = []
    forward = 0
    slots = {u.slot for u in game.units}
    for t in texts:
        try:
            od = parse_order(t)
        except OrderError as e:
            out.append({"text": t, "ok": False, "order": None, "reason": str(e)})
            continue
        why: str | None = None
        if kind == "orders":
            od, why = ctx["position"].validate(power, od)
        elif kind == "retreats":
            key = (od.utype, prov_of(od.loc)) if hasattr(od, "utype") else None
            hit = ctx["options"].get(key) if key else None
            if od.kind not in ("retreat", "disband"):
                why = "not a retreat order"
            elif hit is None:
                why = f"no dislodged {od.utype} at {od.loc}"
            elif od.kind == "retreat" and prov_of(od.to) not in {prov_of(x) for x in hit[1]}:
                why = f"{od.to} is not a legal retreat"
        elif kind == "adjust":
            change = ctx["change"]
            if change > 0:
                if not isinstance(od, Build):
                    why = "not a build"
                elif len(built) >= change:
                    why = "no builds left"
                else:
                    why = game._build_ok(power, od, slots, forward)
                    if why is None:
                        built.append(od)
                        slots.add((prov_of(od.loc), layer_of(od.utype)))
                        if game.is_forward_base(power, od):
                            forward += 1
            elif change < 0:
                if not isinstance(od, Remove):
                    why = "not a disband"
                elif not any(u.type == od.utype and u.prov == prov_of(od.loc) for u in game.units_of(power)):
                    why = f"no {od.utype} at {od.loc}"
            else:
                why = "no adjustment this Winter"
        elif kind == "propose":
            if od.kind != "propose":
                why = "not a proposal"
            elif od.resolution is not None and od.resolution.text() not in ctx["resolutions"]:
                why = "not an allowed resolution"
        elif kind == "vote":
            if not isinstance(od, Vote):
                why = "not a vote"
            elif od.choice == "veto" and not game.can_veto(power):
                why = "veto already used"
        elif kind == "umbrellas":
            if not isinstance(od, Umbrella) or od.power != power:
                why = "not your umbrella"
            elif od.protected not in ctx.get("candidates", []):
                why = f"cannot extend an umbrella over {od.protected}"
        out.append({"text": t, "ok": why is None, "order": order_json(power, od), "reason": why})
    return out


def _as_list(res) -> list[str]:
    if res is None:
        return []
    if isinstance(res, str):
        return [res]
    return [str(x) for x in res if str(x).strip()]


def _chair(game: "Game") -> str:
    """The Assembly chair, as the engine picks it (fewest centres, ties by power order)."""
    idx = {p: i for i, p in enumerate(game.map.power_order)}
    return min(game.active_powers(), key=lambda p: (game.centres(p), idx[p]))


def _unit_name(game: "Game", u: Unit) -> str:
    return game._unit_name(u)


def _power_info(game: "Game", power: str) -> dict:
    """Status the UI shows with every decision."""
    protectors = sorted(a for a, b in game.umbrellas if b == power)
    return {"status": {
        "centres": game.centres(power), "units": len(game.units_of(power)),
        "swing": game.swing_count(power), "protected_by": protectors,
        "protecting": sorted(b for a, b in game.umbrellas if a == power),
        "vetoes_left": 1 if game.can_veto(power) else 0, "vote_weight": game.votes_weight(power),
        "nuclear": game.map.nuclear(power),
        "effects": _effects(game),
    }}


def _effects(game: "Game") -> list[str]:
    e = game.effects
    out = []
    out += [f"Sanctions regime on {game.name(p)}: its Capitals cannot move" for p in sorted(e.sanctions_regime)]
    out += [f"Peacekeeping in {game.name(p)}: no Military moves in; Military there only hold or support"
            for p in sorted(e.peacekeeping)]
    out += [f"Freedom of navigation: {game.name(p)} cannot be closed" for p in sorted(e.navigation)]
    out += [f"Recognition of {game.name(p)}: no civil moves in" for p in sorted(e.recognition)]
    if e.climate_accord:
        out.append("Climate accord: a power that scores no COMMONS this year loses a build")
    return out

