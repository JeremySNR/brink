"""Bridge between MILA `diplomacy` (the oracle) and the Brink engine.

* `classic_map_data()` converts MILA's standard map into Brink's map.json format (with `coasts`), so the
  Brink engine can play the 1901 board with Armies and Fleets only.
* `ClassicGame` is a tiny MILA-like facade over the Brink adjudicator, enough for MILA's own DATC test
  suite (`diplomacy/tests/test_datc.py`) to run against Brink unchanged.
"""
from __future__ import annotations

from collections import Counter
from functools import lru_cache

from brink.adjudicator import adjudicate, resolve_retreats, retreat_options
from brink.map import BrinkMap
from brink.orders import OrderError, parse_order
from brink.state import Effects, TurnContext, Unit, prov_of

CLASSIC_POWERS = ["AUSTRIA", "ENGLAND", "FRANCE", "GERMANY", "ITALY", "RUSSIA", "TURKEY"]


@lru_cache(maxsize=1)
def classic_map_data() -> dict:
    from diplomacy import Game
    m = Game().map
    bases = sorted({loc.upper()[:3] for loc in m.locs})
    coasts: dict[str, list[str]] = {}
    for loc in m.locs:
        if "/" in loc:
            coasts.setdefault(loc[:3], []).append(loc)
    home_of = {h: p for p, hs in m.homes.items() for h in hs}
    provinces = {}
    for b in bases:
        t = m.area_type(b)
        provinces[b] = {
            "name": b, "kind": "sea" if t == "WATER" else "land",
            "centre": "home" if b in home_of else "none", "home_of": home_of.get(b),
            "coastal": t == "COAST", "chokepoint": False, "impassable": t == "SHUT", "opens": None,
        }
    land = [b for b in bases if provinces[b]["kind"] == "land" and not provinces[b]["impassable"]]
    army: dict[str, set] = {b: set() for b in bases}
    for a in land:
        for b in land:
            if a != b and m.abuts("A", a, "-", b):
                army[a].add(b)
    fleet_locs = []
    for b in bases:
        if provinces[b]["impassable"]:
            continue
        if b in coasts:
            fleet_locs += coasts[b]
        elif m.area_type(b) in ("WATER", "COAST"):
            fleet_locs.append(b)
    fleet: dict[str, set] = {loc: set() for loc in fleet_locs}
    for a in fleet_locs:
        for b in fleet_locs:
            if a[:3] != b[:3] and m.abuts("F", a, "-", b):
                fleet[a].add(b)
                fleet[b].add(a)
    for a in list(army):
        for b in army[a]:
            army[b].add(a)
    anyadj: dict[str, set] = {b: set() for b in bases}
    for a, ns in army.items():
        for b in ns:
            anyadj[a].add(b)
            anyadj[b].add(a)
    for a, ns in fleet.items():
        for b in ns:
            anyadj[a[:3]].add(b[:3])
            anyadj[b[:3]].add(a[:3])
    powers = {p: {"name": p.title(), "nuclear": False, "homes": list(m.homes[p])} for p in CLASSIC_POWERS}
    units = []
    from diplomacy import Game as G
    for p, us in G().get_units().items():
        for u in us:
            units.append({"power": p, "type": u[0], "at": u[2:]})
    return {
        "version": 1, "powers": powers, "provinces": provinces,
        "adj": {"army": {k: sorted(v) for k, v in army.items() if v},
                "fleet": {k: sorted(v) for k, v in fleet.items() if v},
                "any": {k: sorted(v) for k, v in anyadj.items() if v}},
        "coasts": coasts, "trade_routes": [],
        "start": {"year": 1901, "crisis": 0, "umbrellas": [], "units": units},
    }


@lru_cache(maxsize=1)
def classic_map() -> BrinkMap:
    return BrinkMap(classic_map_data())


def classic_context(gmap: BrinkMap, year: int = 1901) -> TurnContext:
    # all centres count as 'none' for entry purposes: no swing states, no nuclear powers, no umbrellas
    return TurnContext(year=year, ownership={}, umbrellas=set(), effects=Effects())


class ClassicGame:
    """MILA-like facade (just what test_datc.py uses) over the Brink adjudicator."""

    def __init__(self):
        self.map = classic_map()
        self.units: list[Unit] = []
        self.pending: dict[str, list] = {}
        self.phase = "S1901M"
        self.last = None           # MovementResult
        self.status: dict[str, set] = {}
        self.dislodged: dict[str, str] = {}
        self.popped: list[str] = []
        self.options = {}
        self.unparsed: set[str] = set()

    @property
    def phase_type(self) -> str:
        return self.phase[-1]

    # -- setup
    def clear_units(self):
        self.units = []

    def clear_centers(self):
        pass

    def set_centers(self, power, centers):
        pass

    def set_units(self, power, units, reset=False):
        if isinstance(units, str):
            units = [units]
        if reset:
            self.units = [u for u in self.units if u.power != power]
        for s in units:
            s = s.strip().lstrip("*")
            t, loc = s.split()
            loc = loc.upper()
            self.units = [u for u in self.units if u.prov != prov_of(loc)]
            self.units.append(Unit(power, t.upper(), loc))

    def set_orders(self, power, orders):
        if isinstance(orders, str):
            orders = [orders]
        for o in orders:
            try:
                self.pending.setdefault(power, []).append(parse_order(o))
            except OrderError:
                toks = o.upper().split()
                self.unparsed.add(f"{toks[0]} {toks[1]}")

    def set_current_phase(self, phase):
        self.phase = phase

    # -- processing
    def process(self):
        year = int(self.phase[1:5])
        ctx = classic_context(self.map, year)
        flat = [(p, o) for p, os in self.pending.items() for o in os]
        self.pending = {}
        self.status = {}
        if self.phase_type == "M":
            res = adjudicate(self.map, self.units, flat, ctx)
            self.last = res
            for r in res.results:
                if r.unit is not None:
                    self.status[r.unit.ref()] = set(r.flags) | {r.result}
            for ref in self.unparsed:
                self.status.setdefault(ref, set()).add("void")
            self.unparsed = set()
            self.dislodged = {d.unit.ref(): d.attacker_from for d in res.dislodged}
            self.units = list(res.units)
            if res.dislodged:
                self.options = retreat_options(self.map, res, ctx)
                self._dislodged_units = [d.unit for d in res.dislodged]
                self.phase = self.phase[:-1] + "R"
            else:
                self._next_movement()
        elif self.phase_type == "R":
            rr = resolve_retreats(self.map, self.units, self.options, flat)
            self.units = rr.units
            self.popped = [u.ref() for u in rr.disbanded]
            for r in rr.results:
                if r.unit is not None:
                    self.status.setdefault(r.unit.ref(), set()).update(set(r.flags) | {r.result})
                else:
                    try:
                        self.status.setdefault(r.order.unit_ref, set()).update(set(r.flags) | {r.result})
                    except AttributeError:
                        pass
            self.dislodged = {}
            self._next_movement()
        else:
            raise NotImplementedError("adjustment phases are not part of the Brink engine")

    def _next_movement(self):
        season, year = self.phase[0], int(self.phase[1:5])
        self.phase = ("F%dM" % year) if season == "S" else ("S%dM" % (year + 1))

    # -- queries
    def unit_owner(self, unit: str) -> str | None:
        t, loc = unit.split()
        for u in self.units:
            if u.type == t and (u.loc == loc if "/" in loc else u.prov == loc):
                return u.power
        return None


# ---------------------------------------------------------------------------------------------- differential
def _mila_text(order) -> str:
    """Brink order -> MILA notation (support-hold without the trailing H)."""
    from brink.orders import Support
    if isinstance(order, Support) and order.to is None:
        return f"{order.utype} {order.loc} S {order.ttype} {order.tloc}"
    return order.text()


def _mila_units(mg) -> tuple[list[Unit], list[Unit]]:
    """(units in place, dislodged units) from a MILA game."""
    here, gone = [], []
    for p, us in mg.get_units().items():
        for s in us:
            t, loc = s.lstrip("*").split()
            (gone if s.startswith("*") else here).append(Unit(p, t, loc))
    return here, gone


STATS: "Counter[str]" = Counter()


def differential_game(seed: int, max_phases: int = 12, weights=None) -> tuple[int, list[str]]:
    """Play one random classic game in MILA, adjudicating every movement and retreat phase with Brink as
    well. Returns (movement phases compared, disagreements)."""
    import random

    from diplomacy import Game

    from brink.bots.random_bot import random_movement_orders
    from brink.orders import Convoy, Disband, Move, Position, Retreat

    rng = random.Random(seed)
    weights = dict(weights or {}, special=0.0)   # Brink-only orders have no MILA equivalent
    gmap = classic_map()
    mg = Game()
    problems: list[str] = []
    compared = 0
    while compared < max_phases and not mg.is_game_done:
        phase = mg.get_current_phase()
        year = int(phase[1:5])
        ctx = classic_context(gmap, year)
        if phase.endswith("M"):
            units, _ = _mila_units(mg)
            pos = Position(gmap, units, ctx)
            flat = []
            for p in CLASSIC_POWERS:
                ods = random_movement_orders(pos, p, rng, weights, withdraw_chance=0)
                for o in ods:
                    before = list(mg.get_orders(p))
                    mg.set_orders(p, [_mila_text(o)], replace=False)
                    if len(mg.get_orders(p)) == len(before):
                        if isinstance(o, Convoy):
                            # MILA only accepts fleets on a *minimal* convoy chain (DATC allows any chain).
                            # Excluded: the order is dropped on both sides (the fleet holds).
                            STATS["convoy_rejected_by_mila"] += 1
                            continue
                        problems.append(f"{seed}/{phase}: MILA rejected {p} {o.text()}")
                        continue
                    flat.append((p, o))
            ours = adjudicate(gmap, units, flat, ctx)
            orders_txt = "; ".join(f"{p}: {o.text()}" for p, o in flat)
            mg.process()
            compared += 1
            here, gone = _mila_units(mg)
            if set(here) != set(ours.units):
                # Known MILA deviation: an Army whose convoy to an ADJACENT province is disrupted falls back
                # to the land route in MILA; under the 2000 rulebook (DATC 4.A.3) it just fails. Prove it:
                # re-adjudicate with those convoy orders removed (= MILA's land fallback) and compare.
                fallback = [r for r in ours.results if isinstance(r.order, Move) and r.unit.type == "A"
                            and not r.order.via and r.result in ("failed", "dislodged") and "no_path" in r.flags
                            and prov_of(r.order.to) in gmap.army_moves(r.unit.prov, year)]
                if fallback:
                    armies = {r.unit.loc for r in fallback}
                    alt = [(p, o) for p, o in flat if not (isinstance(o, Convoy) and o.tloc in armies)]
                    alt_res = adjudicate(gmap, units, alt, ctx)
                    if set(alt_res.units) == set(here):
                        STATS["mila_adjacent_convoy_fallback"] += 1
                        continue
                problems.append(f"{seed}/{phase}: positions differ\n  orders: {orders_txt}\n"
                                f"  MILA only: {sorted(map(str, set(here) - set(ours.units)))}\n"
                                f"  Brink only: {sorted(map(str, set(ours.units) - set(here)))}")
                continue
            opts = retreat_options(gmap, ours, ctx)
            # MILA removes dislodged units that have nowhere to go without a retreat phase
            ours_d = {d.unit: d.attacker_from for d in ours.dislodged if opts[d.unit]}
            mila_d = {}
            for u in gone:
                key = next((k for k in mg.dislodged if k.split()[1] == u.loc and k[0] == u.type), None)
                mila_d[u] = mg.dislodged.get(key, "?")[:3] if key else "?"
            # Known MILA deviation: when the dislodging unit came by convoy, MILA skips the standoff and
            # occupancy checks for retreats (operator precedence in Game._move_results), so it keeps units
            # that really have no retreat, and offers contested/occupied provinces.
            by_sea = {d.unit for d in ours.dislodged if d.by_sea}
            for u in list(mila_d):
                if u in by_sea and u not in ours_d and any(d.unit == u for d in ours.dislodged):
                    STATS["mila_convoy_retreat_bug"] += 1
                    ours_d[u] = mila_d[u]
            if ours_d != mila_d:
                problems.append(f"{seed}/{phase}: dislodged differ: MILA {mila_d} Brink {ours_d}\n  orders: {orders_txt}")
                continue
            STATS["dislodged"] += len(ours.dislodged)
            if gone:
                possible = mg.get_all_possible_orders()
                for u in gone:
                    mila_opts = sorted(o.split()[-1] for o in possible.get(u.prov, []) + possible.get(u.loc, [])
                                       if " R " in o)
                    if u in by_sea and set(opts[u]) <= set(mila_opts):
                        if set(opts[u]) != set(mila_opts):
                            STATS["mila_convoy_retreat_bug"] += 1
                        continue
                    if sorted(set(mila_opts)) != sorted(opts[u]):
                        problems.append(f"{seed}/{phase}: retreat options for {u} differ: MILA {sorted(set(mila_opts))}"
                                        f" Brink {opts[u]}\n  orders: {orders_txt}")
                # random retreats, resolved by both
                rflat = []
                for u in gone:
                    STATS["retreat_phases_units"] += 1
                    if opts[u] and rng.random() < 0.85:
                        od = Retreat(u.type, u.loc, rng.choice(opts[u]))
                    else:
                        od = Disband(u.type, u.loc)
                    rflat.append((u.power, od))
                for p in CLASSIC_POWERS:
                    texts = [o.text() for pp, o in rflat if pp == p]
                    if texts:
                        mg.set_orders(p, texts)
                rr = resolve_retreats(gmap, ours.units, opts, rflat)
                mg.process()
                here, _ = _mila_units(mg)
                if set(here) != set(rr.units) and not by_sea:
                    problems.append(f"{seed}/{phase}R: positions after retreats differ: MILA only "
                                    f"{sorted(map(str, set(here) - set(rr.units)))} Brink only "
                                    f"{sorted(map(str, set(rr.units) - set(here)))}")
        elif phase.endswith("R"):
            mg.process()
        else:  # adjustments: random builds/disbands from MILA's own list
            possible = mg.get_all_possible_orders()
            for p in CLASSIC_POWERS:
                locs = mg.get_orderable_locations(p)
                cands = [o for loc in locs for o in possible.get(loc, []) if o != "WAIVE"]
                rng.shuffle(cands)
                picked, used = [], set()
                for o in cands:
                    loc = o.split()[1][:3]
                    if loc not in used:
                        used.add(loc)
                        picked.append(o)
                if picked:
                    mg.set_orders(p, picked)
            mg.process()
    return compared, problems
