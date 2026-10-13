"""A bot that plays random LEGAL orders of every kind, weighted so games aren't all holds."""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

from ..orders import Convoy, Hold, Move, Order, Position, Withdraw
from ..state import Unit, prov_of
from .base import Player

if TYPE_CHECKING:  # pragma: no cover
    from ..game import Game

DEFAULT_WEIGHTS = {"hold": 0.08, "move": 0.47, "support": 0.27, "convoy": 0.06, "special": 0.12}


def random_movement_orders(pos: Position, power: str, rng: random.Random,
                           weights: dict[str, float] | None = None,
                           withdraw_chance: float = 0.03) -> list[Order]:
    """Random legal orders for every unit of `power`, with some coordination: supports prefer to back
    an order this power already chose, and a chosen convoy usually sets the convoyed Army moving."""
    w = dict(DEFAULT_WEIGHTS, **(weights or {}))
    mine = sorted((u for u in pos.units if u.power == power), key=lambda u: (u.prov, u.type))
    rng.shuffle(mine)
    chosen: dict[Unit, Order] = {}
    supporters: list[Unit] = []
    for u in mine:
        if u in chosen:
            continue
        options = {"hold": [Hold(u.type, u.loc)], "move": pos.legal_moves(u)}
        if u.type == "F":
            options["convoy"] = pos.legal_convoys(u)
        options["special"] = pos.legal_specials(u)
        kinds = [k for k in ("hold", "move", "support", "convoy", "special") if k == "support" or options.get(k)]
        kind = rng.choices(kinds, [w[k] for k in kinds])[0]
        if kind == "support":
            supporters.append(u)
            continue
        od = rng.choice(options[kind])
        chosen[u] = od
        if isinstance(od, Convoy):
            army = pos.find("A", od.tloc)
            if army is not None and army.power == power and rng.random() < 0.85:
                chosen[army] = Move("A", army.loc, od.to)
    for u in supporters:
        sups = pos.legal_supports(u)
        if not sups:
            chosen[u] = Hold(u.type, u.loc)
            continue
        matching = []
        for s in sups:
            t = pos.find(s.ttype, s.tloc)
            if t is None or t.power != power or t not in chosen:
                continue
            o = chosen[t]
            if s.to is None and not isinstance(o, Move):
                matching.append(s)
            elif s.to is not None and isinstance(o, Move) and prov_of(o.to) == s.to:
                matching.append(s)
        pool = matching if matching and rng.random() < 0.8 else sups
        chosen[u] = rng.choice(pool)
    out: list[Order] = [chosen[u] for u in sorted(chosen, key=lambda u: (u.prov, u.type))]
    for protector, protected in sorted(pos.ctx.umbrellas):
        if protector == power and rng.random() < withdraw_chance:
            out.append(Withdraw(power, protected))
    return out


class RandomBot(Player):
    """Random legal play in every phase."""

    name = "random"

    def __init__(self, seed: int | None = None, weights: dict[str, float] | None = None):
        self.rng = random.Random(seed)
        self.weights = weights

    def orders(self, game: "Game", power: str) -> list[str]:
        return [o.text() for o in random_movement_orders(game.position(), power, self.rng, self.weights)]

    def retreats(self, game: "Game", power: str, options: dict[Unit, list[str]]) -> list[str]:
        out = []
        for u, opts in sorted(options.items(), key=lambda kv: kv[0].loc):
            if u.power != power:
                continue
            if opts and self.rng.random() < 0.85:
                out.append(f"{u.type} {u.loc} R {self.rng.choice(opts)}")
            else:
                out.append(f"{u.type} {u.loc} D")
        return out

    def propose(self, game: "Game", power: str) -> str | None:
        options = game.resolution_options(power)
        if not options or self.rng.random() < 0.2:
            return None
        return "PROPOSE " + self.rng.choice(options).text()

    def vote(self, game: "Game", power: str, proposal: str) -> str:
        r = self.rng.random()
        if r < 0.04 and game.can_veto(power):
            return "VOTE VETO"
        return "VOTE YES" if r < 0.55 else "VOTE NO"

    def adjust(self, game: "Game", power: str, change: int) -> list[str]:
        if change > 0:
            builds = game.build_options(power)
            self.rng.shuffle(builds)
            out, used, forward = [], set(), False
            for b in builds:
                if len(out) >= change:
                    break
                key = (b.loc, "M" if b.utype in "AF" else "C")
                is_forward = game.is_forward_base(power, b)
                if key in used or (is_forward and forward):
                    continue
                if self.rng.random() < 0.85:
                    used.add(key)
                    forward = forward or is_forward
                    out.append(b.text())
            return out
        if change < 0:
            units = sorted(game.units_of(power), key=lambda u: (u.prov, u.type))
            self.rng.shuffle(units)
            return [f"DISBAND {u.type} {u.loc}" for u in units[: -change]]
        return []

    def umbrellas(self, game: "Game", power: str) -> list[str] | None:
        if not game.map.nuclear(power):
            return None
        out = []
        for p in game.map.power_order:
            if p != power and not game.map.nuclear(p) and p in game.active_powers():
                current = (power, p) in game.umbrellas
                if (current and self.rng.random() < 0.8) or (not current and self.rng.random() < 0.15):
                    out.append(f"{power} UMBRELLA {p}")
        return out
