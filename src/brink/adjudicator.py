"""Two-layer simultaneous adjudication of a movement phase.

The resolver follows Lucas Kruijswijk's "The Math of Adjudication": every order with an outcome that
other orders depend on gets a boolean decision (move succeeds / support given / convoying fleet holds /
special order takes effect). `resolve` evaluates a decision recursively, guessing when it meets a cycle
and backtracking; a cycle whose outcome doesn't depend on the guess is settled, and a real paradox goes
to the backup rule:

* convoy paradox: Szykman (2000 rulebook) - the convoyed armies in the cycle don't move and don't cut;
* a CLOSE in the cycle: the closure applies;
* a DISRUPT in the cycle: the disrupt applies;
* a cross-layer support paradox: the supports in the cycle are cut;
* otherwise circular movement: every move in the cycle succeeds.

Layers: only units in the same layer stand each other off or dislodge each other; supports may cross
layers; supports are cut by same-layer attacks (not from the province the support is aimed at), by a
Disrupt aimed at the supporter's province, or by the supporter being dislodged.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Iterable

from .map import BrinkMap
from .orders import Hold, Move, Order, Position, Support, UnitOrder, Withdraw
from .state import MILITARY, TurnContext, Unit, layer_of, prov_of

UNRESOLVED, GUESSING, RESOLVED = 0, 1, 2

sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))


@dataclass
class OrderResult:
    power: str
    order: Order
    result: str                      # ok | bounce | cut | void | dislodged | failed
    reason: str | None = None        # void reason / detail
    flags: set[str] = field(default_factory=set)
    unit: Unit | None = None         # the unit at the start of the phase


@dataclass
class Dislodgement:
    unit: Unit
    attacker_from: str               # province the dislodging unit came from
    by_sea: bool                     # attacker came by convoy / Capital sea move


@dataclass
class MovementResult:
    results: list[OrderResult]
    units: list[Unit]                         # surviving units in their new positions
    dislodged: list[Dislodgement]
    standoffs: set[tuple[str, str]]           # (province, layer) left empty by a standoff
    closed: dict[str, list[str]]              # chokepoint -> powers it was closed to
    sanctions: dict[str, int]                 # target -> points this phase
    commons: dict[str, int]                   # power -> points this phase
    commons_issued: set[str]                  # powers that issued a valid COMMONS (even if dislodged)
    nuclear_clash: bool
    umbrellas: set[tuple[str, str]]           # after WITHDRAW
    withdrawn: list[tuple[str, str]]
    disrupted: dict[str, list[str]]           # target province -> disrupting powers (effective)

    def result_for(self, unit: Unit) -> OrderResult | None:
        for r in self.results:
            if r.unit == unit:
                return r
        return None


@dataclass
class _O:
    idx: int
    power: str
    unit: Unit
    order: Order
    kind: str
    void: str | None = None
    dest: str | None = None          # move: destination province
    dest_loc: str | None = None
    mode: str = "direct"             # move: direct | convoy | sea
    means: list = field(default_factory=list)     # capital sea move: list of zone sets
    convoyers: list[int] = field(default_factory=list)
    target: int | None = None        # support/convoy: matched order index
    supp_to: str | None = None
    mismatch: bool = False


class _Resolver:
    def __init__(self, gmap: BrinkMap, orders: list[_O]):
        self.map = gmap
        self.o = orders
        self.state = [UNRESOLVED] * len(orders)
        self.res = [False] * len(orders)
        self.deps: list[int] = []
        self.guess_reads = 0
        self.szykman: set[int] = set()        # convoyed moves that fail by the paradox rule
        self.forced: set[int] = set()         # close/disrupt orders forced to take effect
        self.cut_forced: set[int] = set()     # supports cut by the paradox rule
        self.occupant: dict[tuple[str, str], int] = {}
        self.moves_into: dict[tuple[str, str], list[int]] = {}
        self.supports_for: dict[int, list[int]] = {}
        self.disrupts_at: dict[str, list[int]] = {}
        self.closes_at: dict[str, int] = {}
        for x in orders:
            self.occupant[x.unit.slot] = x.idx
        for x in orders:
            if x.kind == "move":
                self.moves_into.setdefault((x.dest, x.unit.layer), []).append(x.idx)
            elif x.kind == "support" and not x.mismatch:
                self.supports_for.setdefault(x.target, []).append(x.idx)
            elif x.kind == "disrupt":
                self.disrupts_at.setdefault(x.order.target, []).append(x.idx)
            elif x.kind == "close":
                self.closes_at[x.unit.prov] = x.idx

    # ------------------------------------------------------------------ Kruijswijk core
    def resolve(self, nr: int) -> bool:
        if self.state[nr] == RESOLVED:
            return self.res[nr]
        if self.state[nr] == GUESSING:
            self.guess_reads += 1
            if nr not in self.deps:
                self.deps.append(nr)
            return self.res[nr]
        old = len(self.deps)
        old_reads = self.guess_reads
        self.res[nr] = False
        self.state[nr] = GUESSING
        first = self.adjudicate(nr)
        if self.guess_reads == old_reads:
            # no guess was read: the outcome is final
            if self.state[nr] != RESOLVED:
                self.res[nr] = first
                self.state[nr] = RESOLVED
            return self.res[nr]
        if self.state[nr] == RESOLVED:
            return self.res[nr]
        if len(self.deps) == old or self.deps[old] != nr:
            # depends on someone else's guess (perhaps one already listed): stay provisional
            if nr not in self.deps:
                self.deps.append(nr)
            self.res[nr] = first
            return first
        # nr depends on its own guess: try the other guess
        for d in self.deps[old:]:
            self.state[d] = UNRESOLVED
        del self.deps[old:]
        self.res[nr] = True
        self.state[nr] = GUESSING
        second = self.adjudicate(nr)
        if first == second:
            for d in self.deps[old:]:
                self.state[d] = UNRESOLVED
            del self.deps[old:]
            self.res[nr] = first
            self.state[nr] = RESOLVED
            return first
        self.backup_rule(old)
        return self.resolve(nr)

    def backup_rule(self, old: int) -> None:
        cycle = list(dict.fromkeys(self.deps[old:]))
        for d in cycle:
            self.state[d] = UNRESOLVED
        del self.deps[old:]
        kinds = {d: self.o[d].kind for d in cycle}
        convoy_fleets = [d for d in cycle if kinds[d] == "convoy"]
        changed = False
        if convoy_fleets:
            # Szykman: every convoyed army whose convoy is in the paradox holds instead
            for d in convoy_fleets:
                t = self.o[d].target
                if t is not None and t not in self.szykman:
                    self.szykman.add(t)
                    changed = True
            for d in cycle:
                if kinds[d] == "move" and self.o[d].mode == "convoy" and d not in self.szykman:
                    self.szykman.add(d)
                    changed = True
        if not changed:
            for d in cycle:
                if kinds[d] in ("close", "disrupt") and d not in self.forced:
                    self.forced.add(d)
                    changed = True
        if not changed:
            for d in cycle:
                if kinds[d] == "support" and d not in self.cut_forced:
                    self.cut_forced.add(d)
                    changed = True
        if not changed:
            # circular movement: all moves in the cycle succeed
            for d in cycle:
                if kinds[d] == "move":
                    self.res[d] = True
                    self.state[d] = RESOLVED
                    changed = True
        if not changed:  # pragma: no cover - defensive: settle everything as failing
            for d in cycle:
                self.res[d] = False
                self.state[d] = RESOLVED

    def adjudicate(self, nr: int) -> bool:
        x = self.o[nr]
        if x.kind == "move":
            return self.move_succeeds(x)
        if x.kind == "support":
            return self.support_given(x)
        if x.kind in ("close", "disrupt") and nr in self.forced:
            return True
        # convoy / close / disrupt / other holds: the unit is not dislodged
        return not self.dislodged_holder(x)

    # ------------------------------------------------------------------ helpers
    def dislodged_holder(self, x: _O) -> bool:
        """For a unit that isn't moving: does any move into its slot succeed?"""
        for m in self.moves_into.get(x.unit.slot, ()):
            if self.resolve(m):
                return True
        return False

    def head_to_head(self, x: _O) -> _O | None:
        if x.mode != "direct":
            return None
        occ = self.occupant.get((x.dest, x.unit.layer))
        if occ is None:
            return None
        y = self.o[occ]
        if y.kind == "move" and y.mode == "direct" and y.dest == x.unit.prov:
            return y
        return None

    def closed_to(self, zone: str, power: str) -> bool:
        c = self.closes_at.get(zone)
        if c is None:
            return False
        x = self.o[c]
        if x.power == power:
            return False
        if x.order.against and power not in x.order.against:
            return False
        return self.resolve(c)

    def path(self, x: _O) -> bool:
        if x.mode == "direct":
            return True
        if x.mode == "sea":
            for zones in x.means:
                if not any(self.closed_to(z, x.power) for z in zones):
                    return True
            return False
        # convoy
        if x.idx in self.szykman:
            return False
        ok_zones = set()
        for c in x.convoyers:
            cz = self.o[c].unit.prov
            if self.closed_to(cz, x.power):
                continue
            if self.resolve(c):
                ok_zones.add(cz)
        return self.map.convoy_path_exists(x.unit.prov, x.dest, ok_zones)

    def supports_given(self, x: _O, exclude_power: str | None = None) -> int:
        n = 0
        for s in self.supports_for.get(x.idx, ()):
            if exclude_power is not None and self.o[s].power == exclude_power:
                continue
            if self.resolve(s):
                n += 1
        return n

    def attack_strength(self, x: _O) -> int:
        if not self.path(x):
            return 0
        occ = self.occupant.get((x.dest, x.unit.layer))
        if occ is not None:
            y = self.o[occ]
            moving_away = (y.kind == "move" and self.head_to_head(x) is None and self.resolve(occ))
            if not moving_away:
                if y.power == x.power:
                    return 0
                return 1 + self.supports_given(x, exclude_power=y.power)
        return 1 + self.supports_given(x)

    def hold_strength(self, slot: tuple[str, str]) -> int:
        occ = self.occupant.get(slot)
        if occ is None:
            return 0
        y = self.o[occ]
        if y.kind == "move":
            return 0 if self.resolve(occ) else 1
        return 1 + self.supports_given(y)

    def defend_strength(self, x: _O) -> int:
        return 1 + self.supports_given(x)

    def prevent_strength(self, x: _O) -> int:
        if not self.path(x):
            return 0
        opp = self.head_to_head(x)
        if opp is not None and self.resolve(opp.idx):
            return 0
        return 1 + self.supports_given(x)

    def move_succeeds(self, x: _O) -> bool:
        attack = self.attack_strength(x)
        if attack == 0:
            return False
        opp = self.head_to_head(x)
        if opp is not None:
            if attack <= self.defend_strength(opp):
                return False
        elif attack <= self.hold_strength((x.dest, x.unit.layer)):
            return False
        for m in self.moves_into.get((x.dest, x.unit.layer), ()):
            if m != x.idx and attack <= self.prevent_strength(self.o[m]):
                return False
        return True

    def support_given(self, x: _O) -> bool:
        if x.idx in self.cut_forced:
            return False
        sup_slot = x.unit.slot
        aimed = x.supp_to  # province the support is aimed at (move supports only)
        # Disrupt aimed at the supporter's province (not by the supporter's own power, and not cutting
        # a support for an attack on the disrupting unit itself)
        for d in self.disrupts_at.get(x.unit.prov, ()):
            dz = self.o[d]
            if dz.power == x.power:
                continue
            if aimed is not None and aimed == dz.unit.prov and self.o[x.target].unit.layer == dz.unit.layer:
                continue
            if self.resolve(d):
                return False
        from_target = []
        for m in self.moves_into.get(sup_slot, ()):
            y = self.o[m]
            if y.power == x.power:
                continue
            if aimed is not None and y.unit.prov == aimed:
                from_target.append(m)
                continue
            if self.path(y):
                return False
        for m in from_target:
            if self.resolve(m):
                return False
        return True


# ---------------------------------------------------------------------------------------------- driver
def adjudicate(gmap: BrinkMap, units: Iterable[Unit], orders: Iterable[tuple[str, Order]],
               ctx: TurnContext) -> MovementResult:
    """Resolve one movement phase. `orders` are (power, Order) pairs; units without an order hold;
    a later order for the same unit replaces an earlier one."""
    units = list(units)
    orders = list(orders)
    results_extra: list[OrderResult] = []

    # 1. WITHDRAW applies before anything else
    umbrellas = set(ctx.umbrellas)
    withdrawn = []
    pre = Position(gmap, units, ctx)
    for power, od in orders:
        if isinstance(od, Withdraw):
            _, why = pre.validate(power, od)
            if why is None and (od.power, od.protected) in umbrellas:
                umbrellas.discard((od.power, od.protected))
                withdrawn.append((od.power, od.protected))
                results_extra.append(OrderResult(power, od, "ok"))
            else:
                results_extra.append(OrderResult(power, od, "void", why or "already withdrawn", {"void"}))
    ctx2 = TurnContext(year=ctx.year, ownership=ctx.ownership, umbrellas=umbrellas, effects=ctx.effects)
    pos = Position(gmap, units, ctx2)

    # 2. one order per unit, validated
    chosen: dict[Unit, tuple[str, Order]] = {}
    for power, od in orders:
        if isinstance(od, Withdraw):
            continue
        if not isinstance(od, UnitOrder):
            results_extra.append(OrderResult(power, od, "void", "not a movement order", {"void"}))
            continue
        u = pos.find(od.utype, od.loc, power)
        if u is None:
            results_extra.append(OrderResult(power, od, "void", f"{power} has no {od.utype} at {od.loc}",
                                             {"void"}))
            continue
        chosen[u] = (power, od)

    ox: list[_O] = []
    for i, u in enumerate(units):
        power, od = chosen.get(u, (u.power, Hold(u.type, u.loc)))
        norm, why = pos.validate(power, od)
        if why is not None:
            ox.append(_O(i, power, u, norm, "hold", void=why))
            continue
        x = _O(i, power, u, norm, norm.kind)
        if isinstance(norm, Move):
            x.dest_loc = norm.to
            x.dest = prov_of(norm.to)
        elif isinstance(norm, Support):
            x.supp_to = norm.to
        ox.append(x)
    by_slot = {x.unit.slot: x for x in ox}

    # 3. match supports and convoys to the orders they name; work out how each move travels
    def unit_order(utype: str, loc: str) -> _O | None:
        x = by_slot.get((prov_of(loc), layer_of(utype)))
        return x if x is not None and x.unit.type == utype else None

    for x in ox:
        if x.kind == "convoy":
            t = unit_order("A", x.order.tloc)
            x.target = t.idx if t else None
    for x in ox:
        if x.kind != "move":
            continue
        u = x.unit
        if u.type == "A":
            convoyers = [c.idx for c in ox if c.kind == "convoy" and c.target == x.idx and c.order.to == x.dest]
            land = x.dest in gmap.army_moves(u.prov, ctx.year)
            zones = {ox[c].unit.prov for c in convoyers}
            if not land or (x.order.via and convoyers and gmap.convoy_path_exists(u.prov, x.dest, zones)):
                # VIA states intent; with no convoy ordered, an adjacent move goes overland (DATC 6.G.8)
                x.mode = "convoy"
            elif x.order.via:
                x.mode = "direct"
            else:
                # 2000 rulebook intent: an own fleet ordered to convoy this move, which could be part
                # of a convoy route given every fleet on the board
                own = [c for c in convoyers if ox[c].power == x.power
                       and gmap._convoy_via(u.prov, x.dest, pos.fleet_zones, ox[c].unit.prov)]
                routed = gmap.convoy_path_exists(u.prov, x.dest, zones)
                x.mode = "convoy" if own and routed else "direct"
            if x.mode == "convoy":
                x.convoyers = [c for c in convoyers if gmap._convoy_via(u.prov, x.dest, zones, ox[c].unit.prov)]
        elif u.type == "C":
            means = gmap.capital_means(u.prov, ctx.year).get(x.dest, [])
            if any(mn.kind == "land" for mn in means):
                x.mode = "direct"
            else:
                x.mode = "sea"
                x.means = [mn.zones for mn in means]
        else:
            x.mode = "direct"
    for x in ox:
        if x.kind == "convoy":
            t = ox[x.target] if x.target is not None else None
            if t is None or t.kind != "move" or t.dest != x.order.to or t.mode != "convoy" or x.idx not in t.convoyers:
                x.mismatch = True
        elif x.kind == "support":
            t = unit_order(x.order.ttype, x.order.tloc)
            x.target = t.idx if t else None
            if t is None:
                x.mismatch = True
            elif x.supp_to is None:
                x.mismatch = t.kind == "move"
            else:
                x.mismatch = not (t.kind == "move" and t.dest == x.supp_to)
                if not x.mismatch and t.mode == "convoy" and not gmap.convoy_path_exists(
                        t.unit.prov, t.dest, {ox[c].unit.prov for c in t.convoyers}):
                    x.mismatch = True   # the army can't be convoyed at all: nothing to support

    # 4. resolve
    r = _Resolver(gmap, ox)
    for x in ox:
        if x.kind in ("move", "support", "convoy", "close", "disrupt"):
            if not (x.kind == "support" and x.mismatch):
                r.resolve(x.idx)

    succeeded = {x.idx for x in ox if x.kind == "move" and r.resolve(x.idx)}
    dislodged: list[Dislodgement] = []
    dislodged_idx: set[int] = set()
    for x in ox:
        if x.idx in succeeded:
            continue
        for m in r.moves_into.get(x.unit.slot, ()):
            if m in succeeded:
                y = ox[m]
                dislodged.append(Dislodgement(x.unit, y.unit.prov, y.mode != "direct"))
                dislodged_idx.add(x.idx)
                break

    # 5. outcomes
    new_units: list[Unit] = []
    for x in ox:
        if x.idx in dislodged_idx:
            continue
        if x.idx in succeeded:
            new_units.append(Unit(x.unit.power, x.unit.type, x.dest_loc))
        else:
            new_units.append(x.unit)
    occupied = {u.slot for u in new_units}
    standoffs = set()
    for slot, ms in r.moves_into.items():
        if slot in occupied:
            continue
        for m in ms:
            if m in succeeded or not r.path(ox[m]):
                continue
            beaten_from_there = any(
                d.unit == ox[m].unit and d.attacker_from == slot[0] for d in dislodged)
            if not beaten_from_there:
                standoffs.add(slot)
                break

    closed: dict[str, list[str]] = {}
    sanctions: dict[str, int] = {}
    commons: dict[str, int] = {}
    commons_issued: set[str] = set()
    disrupted: dict[str, list[str]] = {}
    effective: set[int] = set()
    for x in ox:
        if x.void is not None:
            continue
        alive = x.idx not in dislodged_idx
        if x.kind == "close":
            if r.resolve(x.idx) or x.idx in r.forced:
                effective.add(x.idx)
                closed[x.unit.prov] = sorted(x.order.against or [p for p in gmap.powers if p != x.power])
        elif x.kind == "sanction" and alive:
            effective.add(x.idx)
            sanctions[x.order.target] = sanctions.get(x.order.target, 0) + 1
        elif x.kind == "commons":
            commons_issued.add(x.power)
            if alive:
                effective.add(x.idx)
                commons[x.power] = commons.get(x.power, 0) + 1
        elif x.kind == "disrupt":
            if r.resolve(x.idx):
                effective.add(x.idx)
                disrupted.setdefault(x.order.target, []).append(x.power)
        elif x.kind == "forge" and alive:
            effective.add(x.idx)

    nuclear_clash = False
    for d in dislodged:
        if d.unit.layer != MILITARY or not gmap.nuclear(d.unit.power):
            continue
        attacker = next(ox[m] for m in r.moves_into[d.unit.slot] if m in succeeded)
        if gmap.nuclear(attacker.power) and attacker.power != d.unit.power:
            nuclear_clash = True

    results: list[OrderResult] = []
    for x in ox:
        flags: set[str] = set()
        if x.void is not None:
            res, flags = "void", {"void"}
        elif x.kind == "move":
            if x.idx in succeeded:
                res = "ok"
            elif not r.path(x):
                res = "failed"
                flags.add("no_path")
            else:
                res = "bounce"
                flags.add("bounce")
        elif x.kind == "support":
            if x.mismatch:
                res = "void"
                flags.add("void")
            elif r.resolve(x.idx):
                res = "ok"
                t = ox[x.target]
                if t.kind == "move" and not r.path(t):
                    flags.add("no_path")   # supported a convoy that didn't run
                if t.kind == "move":
                    occ = r.occupant.get((t.dest, t.unit.layer))
                    y = ox[occ] if occ is not None else None
                    if y is not None and y.power == x.power and occ not in succeeded:
                        flags.add("own_target")
                        res = "void"   # a power's support never helps dislodge its own unit
                        flags.add("void")
                        x.void = "support against own unit"
            else:
                res = "cut"
                flags.add("cut")
        elif x.kind == "convoy":
            if x.mismatch:
                res = "void"
                flags.add("void")
            else:
                t = ox[x.target]
                if t.idx in r.szykman or not r.path(t):
                    res = "failed"
                    flags.add("no_path")
                    if t.idx in r.szykman:
                        flags.add("paradox")
                else:
                    res = "ok"
        elif x.kind in ("close", "sanction", "disrupt", "commons", "forge"):
            res = "ok" if x.idx in effective else "failed"
        else:
            res = "ok"
        if x.idx in dislodged_idx:
            flags.add("dislodged")
            if res in ("ok", "bounce", "failed") or (res == "cut" and x.kind == "support"):
                res = "dislodged"
        results.append(OrderResult(x.power, x.order, res, x.void, flags, x.unit))
    results.extend(results_extra)

    return MovementResult(results=results, units=new_units, dislodged=dislodged, standoffs=standoffs,
                          closed=closed, sanctions=sanctions, commons=commons, commons_issued=commons_issued,
                          nuclear_clash=nuclear_clash, umbrellas=umbrellas, withdrawn=withdrawn,
                          disrupted=disrupted)


# ---------------------------------------------------------------------------------------------- retreats
def retreat_options(gmap: BrinkMap, mres: MovementResult, ctx: TurnContext) -> dict[Unit, list[str]]:
    """Legal retreat destinations (locations) for each dislodged unit."""
    occupied = {u.slot for u in mres.units}
    pos = Position(gmap, mres.units, TurnContext(ctx.year, ctx.ownership, mres.umbrellas, ctx.effects))
    out: dict[Unit, list[str]] = {}
    for d in mres.dislodged:
        u = d.unit
        opts = []
        for loc in sorted(gmap.retreat_moves(u.type, u.loc, ctx.year)):
            p = prov_of(loc)
            if (p, u.layer) in occupied or (p, u.layer) in mres.standoffs:
                continue
            if p == d.attacker_from and not d.by_sea:
                continue
            if pos.entry_block(u.power, u.type, p) is not None:
                continue
            opts.append(loc)
        out[u] = opts
    return out


@dataclass
class RetreatResult:
    results: list[OrderResult]
    units: list[Unit]            # all units after the retreat phase
    disbanded: list[Unit]


def resolve_retreats(gmap: BrinkMap, units_after: list[Unit], options: dict[Unit, list[str]],
                     orders: Iterable[tuple[str, Order]]) -> RetreatResult:
    from .orders import Disband, Retreat
    chosen: dict[Unit, tuple[str, Order]] = {}
    results: list[OrderResult] = []
    for power, od in orders:
        unit = None
        if isinstance(od, (Retreat, Disband)):
            for u in options:
                if u.power == power and u.type == od.utype and (u.loc == od.loc or (
                        "/" not in od.loc and u.prov == od.loc)):
                    unit = u
        if unit is None:
            results.append(OrderResult(power, od, "void", "no dislodged unit for this order", {"void"}))
            continue
        chosen[unit] = (power, od)
    targets: dict[tuple[str, str], list[Unit]] = {}
    decided: dict[Unit, str | None] = {}
    for u, opts in options.items():
        power, od = chosen.get(u, (u.power, None))
        if isinstance(od, Retreat):
            dest = od.to
            if "/" not in dest and gmap.coasts.get(dest) and u.type == "F":
                cands = [o for o in opts if prov_of(o) == dest]
                dest = cands[0] if len(cands) == 1 else dest
            if dest in opts:
                decided[u] = dest
                targets.setdefault((prov_of(dest), u.layer), []).append(u)
                continue
            results.append(OrderResult(power, od, "void", f"cannot retreat to {od.to}", {"void", "disband"}, u))
            decided[u] = None
            continue
        decided[u] = None
        if od is None:
            od = Disband(u.type, u.loc)
        results.append(OrderResult(power, od, "ok", None, {"disband"}, u))
    new_units = list(units_after)
    disbanded = [u for u, d in decided.items() if d is None]
    for slot, us in targets.items():
        if len(us) > 1:
            for u in us:
                power, od = chosen[u]
                results.append(OrderResult(power, od, "bounce", "retreat standoff", {"bounce", "disband"}, u))
                disbanded.append(u)
        else:
            u = us[0]
            power, od = chosen[u]
            results.append(OrderResult(power, od, "ok", None, set(), u))
            new_units.append(Unit(u.power, u.type, decided[u]))
    return RetreatResult(results, new_units, disbanded)
