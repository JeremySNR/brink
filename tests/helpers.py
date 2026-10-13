"""Shared helpers for the fixture-map tests."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from brink.adjudicator import adjudicate, retreat_options
from brink.bots.base import Player
from brink.game import Game, Rules
from brink.map import BrinkMap
from brink.orders import parse_order
from brink.state import Effects, TurnContext, Unit

FIXTURE = Path(__file__).parent / "fixtures" / "mini_map.json"


@lru_cache(maxsize=1)
def mini() -> BrinkMap:
    return BrinkMap.load(FIXTURE)


def units(*specs: str) -> list[Unit]:
    """units('USA A TEX', 'CHN C BRA')"""
    out = []
    for s in specs:
        p, t, loc = s.split()
        out.append(Unit(p, t, loc))
    return out


def ctx(year=2031, owners: dict | None = None, umbrellas=(("USA", "JPN"),), effects: Effects | None = None,
        gmap: BrinkMap | None = None) -> TurnContext:
    gmap = gmap or mini()
    own = gmap.start_ownership()
    own.update(owners or {})
    return TurnContext(year=year, ownership=own, umbrellas=set(umbrellas), effects=effects or Effects())


def resolve(unit_specs, order_lines, **kw):
    """order_lines: ['USA: A TEX - MEX', ...]. Returns (MovementResult, {order text: result})."""
    gmap = kw.pop("gmap", None) or mini()
    c = ctx(gmap=gmap, **kw)
    orders = []
    for line in order_lines:
        p, _, t = line.partition(":")
        orders.append((p.strip(), parse_order(t.strip())))
    res = adjudicate(gmap, units(*unit_specs), orders, c)
    table = {}
    for r in res.results:
        key = f"{r.power}: {r.order.text()}" if r.unit is None else f"{r.unit.type} {r.unit.loc}"
        table[key] = r.result
    return res, table


def where(res) -> set[str]:
    return {f"{u.power} {u.type} {u.loc}" for u in res.units}


class Scripted(Player):
    """Plays scripted orders keyed by phase ('S2031', 'F2031'); holds otherwise. Winter hooks are dicts."""

    name = "scripted"

    def __init__(self, moves=None, retreats=None, propose=None, votes=None, builds=None, umbrellas=None):
        self.moves = moves or {}
        self.retreat_orders = retreats or {}
        self.proposals = propose or {}
        self.votes = votes or {}
        self.builds = builds or {}
        self.umbrella_decl = umbrellas or {}

    def _key(self, game):
        return {"spring": "S", "fall": "F", "winter": "W"}[game.season] + str(game.year)

    def orders(self, game, power):
        return self.moves.get(self._key(game), [])

    def retreats(self, game, power, options):
        return self.retreat_orders.get(self._key(game), [])

    def propose(self, game, power):
        return self.proposals.get(game.year)

    def vote(self, game, power, proposal):
        return self.votes.get(game.year, "VOTE NO")

    def adjust(self, game, power, change):
        return self.builds.get(game.year, [])

    def umbrellas(self, game, power):
        return self.umbrella_decl.get(game.year)


def game_with(unit_specs, owners=None, players=None, rules=None, umbrellas=(("USA", "JPN"),), crisis=3) -> Game:
    g = Game(mini(), players or {}, seed=0, rules=rules or Rules())
    g.units = units(*unit_specs)
    g.ownership.update(owners or {})
    g.umbrellas = set(umbrellas)
    g.crisis = crisis
    return g


__all__ = ["mini", "units", "ctx", "resolve", "where", "Scripted", "game_with", "retreat_options"]
