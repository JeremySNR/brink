"""Orders: dataclasses, text notation (parse/format), validation and legal-order enumeration.

Orders don't carry their power: the submitting power is passed alongside (power-level orders such as
WITHDRAW/UMBRELLA name the acting power in the text and must match the submitter).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from .map import BrinkMap
from .state import (CIVIL, MILITARY, UNIT_TYPES, Effects, Resolution, TurnContext, Unit, layer_of,
                    prov_of)


class OrderError(ValueError):
    """Text that isn't an order at all (as opposed to a well-formed but illegal order)."""


# ---------------------------------------------------------------------------------------------- dataclasses
@dataclass(frozen=True)
class Order:
    kind = "?"

    def text(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def __str__(self) -> str:
        return self.text()


@dataclass(frozen=True)
class UnitOrder(Order):
    utype: str
    loc: str

    @property
    def unit_ref(self) -> str:
        return f"{self.utype} {self.loc}"


@dataclass(frozen=True)
class Hold(UnitOrder):
    kind = "hold"

    def text(self):
        return f"{self.unit_ref} H"


@dataclass(frozen=True)
class Move(UnitOrder):
    to: str = ""
    via: bool = False
    kind = "move"

    def text(self):
        return f"{self.unit_ref} - {self.to}" + (" VIA" if self.via else "")


@dataclass(frozen=True)
class Support(UnitOrder):
    ttype: str = ""
    tloc: str = ""
    to: str | None = None
    kind = "support"

    def text(self):
        t = f"{self.ttype} {self.tloc}"
        return f"{self.unit_ref} S {t} - {self.to}" if self.to else f"{self.unit_ref} S {t} H"


@dataclass(frozen=True)
class Convoy(UnitOrder):
    ttype: str = "A"
    tloc: str = ""
    to: str = ""
    kind = "convoy"

    def text(self):
        return f"{self.unit_ref} C {self.ttype} {self.tloc} - {self.to}"


@dataclass(frozen=True)
class Close(UnitOrder):
    against: tuple[str, ...] = ()   # empty: closed to everyone else
    kind = "close"

    def text(self):
        return f"{self.unit_ref} CLOSE" + "".join(f" {p}" for p in self.against)


@dataclass(frozen=True)
class Sanction(UnitOrder):
    target: str = ""
    kind = "sanction"

    def text(self):
        return f"{self.unit_ref} SANCTION {self.target}"


@dataclass(frozen=True)
class Disrupt(UnitOrder):
    target: str = ""
    kind = "disrupt"

    def text(self):
        return f"{self.unit_ref} DISRUPT {self.target}"


@dataclass(frozen=True)
class Commons(UnitOrder):
    kind = "commons"

    def text(self):
        return f"{self.unit_ref} COMMONS"


@dataclass(frozen=True)
class Forge(UnitOrder):
    target: str = ""
    kind = "forge"

    def text(self):
        return f"{self.unit_ref} FORGE {self.target}"


@dataclass(frozen=True)
class Withdraw(Order):
    power: str
    protected: str
    kind = "withdraw"

    def text(self):
        return f"{self.power} WITHDRAW {self.protected}"


@dataclass(frozen=True)
class Retreat(UnitOrder):
    to: str = ""
    kind = "retreat"

    def text(self):
        return f"{self.unit_ref} R {self.to}"


@dataclass(frozen=True)
class Disband(UnitOrder):
    """Retreat-phase disband: 'A UKR D'."""
    kind = "disband"

    def text(self):
        return f"{self.unit_ref} D"


@dataclass(frozen=True)
class Build(UnitOrder):
    kind = "build"

    def text(self):
        return f"BUILD {self.unit_ref}"


@dataclass(frozen=True)
class Remove(UnitOrder):
    """Winter disband: 'DISBAND I ROM'."""
    kind = "disband"

    def text(self):
        return f"DISBAND {self.unit_ref}"


@dataclass(frozen=True)
class Umbrella(Order):
    power: str
    protected: str
    kind = "umbrella"

    def text(self):
        return f"{self.power} UMBRELLA {self.protected}"


@dataclass(frozen=True)
class Propose(Order):
    resolution: Resolution | None
    kind = "propose"

    def text(self):
        return "PROPOSE " + (self.resolution.text() if self.resolution else "NOTHING")


@dataclass(frozen=True)
class Vote(Order):
    choice: str   # yes | no | veto
    kind = "vote"

    def text(self):
        return f"VOTE {self.choice.upper()}"


MOVEMENT_KINDS = {"hold", "move", "support", "convoy", "close", "sanction", "disrupt", "commons", "forge",
                  "withdraw"}
SPECIAL_KINDS = {"close", "sanction", "disrupt", "commons", "forge"}


# ---------------------------------------------------------------------------------------------- parsing
def _loc(tok: str) -> str:
    """Normalise 'spa/nc', 'SPA(NC)', 'SPA/NC' -> 'SPA/NC'."""
    tok = tok.upper()
    if "(" in tok and tok.endswith(")"):
        a, b = tok[:-1].split("(", 1)
        return f"{a}/{b}"
    return tok


def parse_resolution(tokens: list[str]) -> Resolution | None:
    if not tokens or tokens[0] in ("NOTHING", "NONE"):
        return None
    kind = tokens[0].lower()
    aliases = {"sanction": "sanctions", "fon": "navigation", "freedom": "navigation", "climate": "accord",
               "recognise": "recognition", "recognize": "recognition"}
    kind = aliases.get(kind, kind)
    if kind not in Resolution.KINDS:
        raise OrderError(f"unknown resolution {tokens[0]}")
    if kind == "accord":
        return Resolution("accord")
    if len(tokens) < 2:
        raise OrderError(f"resolution {kind} needs an argument")
    return Resolution(kind, tokens[-1])


def parse_order(text: str) -> Order:
    """Parse any order in the text notation (movement, retreat or winter)."""
    toks = text.replace(",", " ").upper().split()
    if not toks:
        raise OrderError("empty order")
    head = toks[0]
    if head == "BUILD" and len(toks) == 3:
        return Build(toks[1], _loc(toks[2]))
    if head == "DISBAND" and len(toks) == 3:
        return Remove(toks[1], _loc(toks[2]))
    if head == "PROPOSE":
        return Propose(parse_resolution(toks[1:]))
    if head == "VOTE" and len(toks) == 2 and toks[1] in ("YES", "NO", "VETO"):
        return Vote(toks[1].lower())
    if head not in UNIT_TYPES:
        if len(toks) == 3 and toks[1] == "WITHDRAW":
            return Withdraw(head, toks[2])
        if len(toks) == 3 and toks[1] == "UMBRELLA":
            return Umbrella(head, toks[2])
        raise OrderError(f"cannot parse order: {text!r}")
    if len(toks) < 2:
        raise OrderError(f"cannot parse order: {text!r}")
    utype, loc = head, _loc(toks[1])
    rest = toks[2:]
    if not rest or rest == ["H"] or rest == ["HOLD"]:
        return Hold(utype, loc)
    verb = rest[0]
    if verb == "-" or verb == "->":
        if len(rest) >= 2:
            via = rest[-1] == "VIA"
            dests = [t for t in rest[1:] if t not in ("-", "VIA")]
            if dests:
                return Move(utype, loc, _loc(dests[-1]), via or len(dests) > 1)
    elif verb == "S" and len(rest) >= 3:
        ttype, tloc = rest[1], _loc(rest[2])
        tail = rest[3:]
        if not tail or tail == ["H"]:
            return Support(utype, loc, ttype, tloc, None)
        if tail[0] == "-" and len(tail) >= 2:
            return Support(utype, loc, ttype, tloc, prov_of(_loc(tail[1])))
    elif verb == "C" and len(rest) >= 5 and rest[3] == "-":
        return Convoy(utype, loc, rest[1], _loc(rest[2]), prov_of(_loc(rest[4])))
    elif verb == "C" and len(rest) == 4 and rest[2] == "-":   # 'F NTH C LON - BEL'
        return Convoy(utype, loc, "A", _loc(rest[1]), prov_of(_loc(rest[3])))
    elif verb == "CLOSE":
        return Close(utype, loc, tuple(rest[1:]))
    elif verb == "SANCTION" and len(rest) == 2:
        return Sanction(utype, loc, rest[1])
    elif verb == "DISRUPT" and len(rest) == 2:
        return Disrupt(utype, loc, _loc(rest[1]))
    elif verb == "COMMONS" and len(rest) == 1:
        return Commons(utype, loc)
    elif verb == "FORGE" and len(rest) == 2:
        return Forge(utype, loc, rest[1])
    elif verb == "R" and len(rest) == 2:
        return Retreat(utype, loc, _loc(rest[1]))
    elif verb in ("D", "DISBAND") and len(rest) == 1:
        return Disband(utype, loc)
    raise OrderError(f"cannot parse order: {text!r}")


def format_order(order: Order) -> str:
    return order.text()


# ---------------------------------------------------------------------------------------------- record json
def order_json(power: str, order: Order, result: str | None = None) -> dict:
    """Structured order for the game record."""
    d: dict = {"power": power, "text": order.text(), "unit": None, "kind": order.kind, "to": None,
               "target": None}
    if isinstance(order, UnitOrder):
        d["unit"] = {"type": order.utype, "at": order.loc}
    if isinstance(order, (Move, Retreat)):
        d["to"] = order.to
    elif isinstance(order, (Support, Convoy)):
        d["target"] = {"type": order.ttype, "at": order.tloc}
        d["to"] = order.to
    elif isinstance(order, Disrupt):
        d["to"] = order.target
    elif isinstance(order, (Sanction, Forge)):
        d["about"] = order.target
    elif isinstance(order, Close):
        d["about"] = list(order.against)
    elif isinstance(order, (Withdraw, Umbrella)):
        d["about"] = order.protected
    elif isinstance(order, Propose):
        d["about"] = order.resolution.text() if order.resolution else None
    elif isinstance(order, Vote):
        d["about"] = order.choice
    if result is not None:
        d["result"] = result
    return d


# ---------------------------------------------------------------------------------------------- validation
class Position:
    """Unit positions + turn context, with the per-turn caches validation and enumeration need."""

    def __init__(self, gmap: BrinkMap, units: Iterable[Unit], ctx: TurnContext):
        self.map = gmap
        self.units = list(units)
        self.ctx = ctx
        self.by_slot: dict[tuple[str, str], Unit] = {u.slot: u for u in self.units}
        self.fleet_zones = frozenset(u.prov for u in self.units
                                     if u.type == "F" and gmap.provinces[u.prov].is_sea)
        self._moves: dict[Unit, list[Move]] = {}
        self._reach: dict[Unit, set[str]] = {}

    # ---- lookup
    def find(self, utype: str, loc: str, power: str | None = None) -> Unit | None:
        """Unit of this type in loc's province (the coast of the ordered unit is ignored, DATC 6.B.10)."""
        layer = layer_of(utype) if utype in UNIT_TYPES else None
        if layer is None:
            return None
        u = self.by_slot.get((prov_of(loc), layer))
        if u is None or u.type != utype:
            return None
        if power is not None and u.power != power:
            return None
        return u

    def entry_block(self, unit_power: str, utype: str, pid: str) -> str | None:
        ctx = self.ctx
        why = self.map.entry_block(unit_power, utype, pid, ctx.ownership, ctx.umbrellas, ctx.year)
        if why:
            return why
        if layer_of(utype) == MILITARY and pid in ctx.effects.peacekeeping:
            return f"peacekeeping in {pid}: no military moves into it"
        if layer_of(utype) == CIVIL and pid in ctx.effects.recognition:
            return f"recognition of {pid}: no civil moves into it"
        return None

    def reach(self, unit: Unit) -> set[str]:
        """Provinces this unit could legally move into (for supports): own adjacency/reach, no convoy,
        entry restrictions applied; closures not applied."""
        if unit not in self._reach:
            r = {q for q in self.map.support_reach(unit.type, unit.loc, self.ctx.year)
                 if self.entry_block(unit.power, unit.type, q) is None}
            self._reach[unit] = r
        return self._reach[unit]

    def can_support_into(self, supporter: Unit, pid: str, target_layer: str) -> bool:
        if pid == supporter.prov:
            # only across layers: an Army in Brazil may back a Capital moving into Brazil
            return target_layer != supporter.layer
        return pid in self.reach(supporter)

    # ---- validation
    def validate(self, power: str, order: Order) -> tuple[Order, str | None]:
        """Return (normalised order, void reason or None) for a movement-phase order."""
        ctx, m = self.ctx, self.map
        if isinstance(order, Withdraw):
            if order.power != power:
                return order, "can only withdraw your own umbrella"
            if (power, order.protected) not in ctx.umbrellas:
                return order, f"{power} has no umbrella over {order.protected}"
            return order, None
        if not isinstance(order, UnitOrder) or order.kind not in MOVEMENT_KINDS:
            return order, f"{order.kind} is not a movement-phase order"
        unit = self.find(order.utype, order.loc, power)
        if unit is None:
            return order, f"{power} has no {order.utype} at {order.loc}"
        if unit.loc != order.loc:
            order = replace(order, loc=unit.loc)
        if (unit.layer == MILITARY and unit.prov in ctx.effects.peacekeeping
                and order.kind not in ("hold", "support")):
            return order, f"peacekeeping in {unit.prov}: units there may only hold or support"
        year = ctx.year
        if isinstance(order, Hold):
            return order, None
        if isinstance(order, Move):
            return self._validate_move(unit, order)
        if isinstance(order, Support):
            target = self.find(order.ttype, order.tloc)
            if target is None:
                return order, f"no {order.ttype} at {order.tloc} to support"
            if target == unit:
                return order, "a unit cannot support itself"
            if target.loc != order.tloc:
                order = replace(order, tloc=target.loc)
            if order.to is None:
                if not self.can_support_into(unit, target.prov, target.layer):
                    return order, f"{unit.ref()} cannot reach {target.prov}"
                return order, None
            to = prov_of(order.to)
            if to == target.prov:
                return order, "support for a move to where the unit already is"
            if to not in m.provinces:
                return order, f"unknown province {to}"
            if not self.can_support_into(unit, to, target.layer):
                return order, f"{unit.ref()} cannot move to {to}, so cannot support into it"
            return order, None
        if isinstance(order, Convoy):
            if unit.type != "F" or not m.provinces[unit.prov].is_sea:
                return order, "only a Fleet in a sea zone can convoy"
            army = self.find("A", order.tloc)
            if army is None or order.ttype != "A":
                return order, f"no Army at {order.tloc} to convoy"
            dst = m.provinces.get(order.to)
            if dst is None or not dst.is_land:
                return order, f"cannot convoy to {order.to}"
            if not m._convoy_via(army.prov, order.to, set(self.fleet_zones), unit.prov):
                return order, "no convoy route through this fleet"
            why = self.entry_block(army.power, "A", order.to)
            if why:
                return order, f"the Army may not enter {order.to}: {why}"
            return order, None
        if isinstance(order, Close):
            if unit.type != "F" or not m.provinces[unit.prov].chokepoint:
                return order, "only a Fleet in a chokepoint can close it"
            if unit.prov in ctx.effects.navigation:
                return order, f"freedom of navigation: {unit.prov} cannot be closed"
            bad = [p for p in order.against if p not in m.powers or p == power]
            if bad:
                return order, f"cannot close against {' '.join(bad)}"
            return replace(order, against=tuple(dict.fromkeys(order.against))), None
        if isinstance(order, Sanction):
            if unit.type != "C":
                return order, "only a Capital can sanction"
            p = m.provinces[unit.prov]
            own_home = p.home_of == power
            own_swing = p.centre == "swing" and ctx.ownership.get(unit.prov) == power
            if not (own_home or own_swing):
                return order, "a Capital sanctions only from its own home or an aligned swing state"
            if order.target not in m.powers or order.target == power:
                return order, f"cannot sanction {order.target}"
            return order, None
        if isinstance(order, Disrupt):
            if unit.type != "I":
                return order, "only Influence can disrupt"
            if order.target != unit.prov and order.target not in m.within_two(unit.prov, year):
                return order, f"{order.target} is out of Disrupt range"
            return order, None
        if isinstance(order, Commons):
            return order, None
        if isinstance(order, Forge):
            if unit.type != "I":
                return order, "only Influence can forge"
            if order.target not in m.powers or order.target == power:
                return order, f"cannot forge {order.target}"
            homes = set(m.homes(order.target))
            if not (homes & (set(m.any_adj.get(unit.prov, ())) | {unit.prov})):
                return order, f"must be next to a home of {order.target}"
            return order, None
        return order, f"unknown order kind {order.kind}"

    def _validate_move(self, unit: Unit, order: Move) -> tuple[Order, str | None]:
        m, year = self.map, self.ctx.year
        dest_loc = order.to
        dest = prov_of(dest_loc)
        if dest not in m.provinces:
            return order, f"unknown province {dest}"
        if dest == unit.prov:
            return order, "move to own province"
        why = self.entry_block(unit.power, unit.type, dest)
        if why:
            return order, why
        if unit.type != "F" and dest_loc != dest:
            order = replace(order, to=dest)   # coasts mean nothing to Armies and civil units
        if unit.type == "A":
            if dest in m.army_moves(unit.prov, year):
                return order, None
            if m.provinces[dest].is_land and m.convoy_path_exists(unit.prov, dest, self.fleet_zones):
                return order, None
            return order, f"{dest} is not reachable from {unit.prov}"
        if order.via:
            return order, "only Armies move via convoy"
        if unit.type == "F":
            options = m.fleet_dest_locs(unit.loc, dest, year)
            if "/" in dest_loc:
                if dest_loc not in options:
                    return order, f"{dest_loc} is not adjacent to {unit.loc}"
                return order, None
            if dest in m.coasts:
                if len(options) == 1:
                    return replace(order, to=options[0]), None
                if not options:
                    return order, f"{dest} is not adjacent to {unit.loc}"
                return order, f"coast must be specified for {dest}"
            if dest not in options:
                return order, f"{dest} is not adjacent to {unit.loc}"
            return order, None
        if unit.type == "C":
            if unit.power in self.ctx.effects.sanctions_regime:
                return order, f"sanctions regime: {unit.power}'s Capital cannot move"
            if dest not in m.capital_moves(unit.prov, year):
                return order, f"{dest} is not reachable by a Capital from {unit.prov}"
            return order, None
        if dest not in m.influence_moves(unit.prov, year):
            return order, f"{dest} is out of Influence range"
        return order, None

    # ---- enumeration
    def legal_moves(self, unit: Unit) -> list[Move]:
        """Every legal move for the unit (Army: land and convoy; Fleet: every coast)."""
        if unit in self._moves:
            return self._moves[unit]
        m, year = self.map, self.ctx.year
        out: list[Move] = []
        if unit.layer == MILITARY and unit.prov in self.ctx.effects.peacekeeping:
            self._moves[unit] = out
            return out
        if unit.type == "C" and unit.power in self.ctx.effects.sanctions_regime:
            self._moves[unit] = out
            return out
        if unit.type == "A":
            dests = set(m.army_moves(unit.prov, year))
            if m.provinces[unit.prov].is_land:
                dests |= m.convoy_destinations(unit.prov, self.fleet_zones)
            for d in sorted(dests):
                if d != unit.prov and self.entry_block(unit.power, "A", d) is None:
                    out.append(Move("A", unit.loc, d))
        elif unit.type == "F":
            for d in sorted(m.fleet_moves(unit.loc, year)):
                if self.entry_block(unit.power, "F", prov_of(d)) is None:
                    out.append(Move("F", unit.loc, d))
        else:
            for d in sorted(m.moves_for(unit.type, unit.loc, year)):
                if self.entry_block(unit.power, unit.type, d) is None:
                    out.append(Move(unit.type, unit.loc, d))
        self._moves[unit] = out
        return out

    def legal_supports(self, unit: Unit) -> list[Support]:
        out: list[Support] = []
        reach = self.reach(unit) | {unit.prov}
        for other in self.units:
            if other == unit:
                continue
            if other.prov in reach and self.can_support_into(unit, other.prov, other.layer):
                out.append(Support(unit.type, unit.loc, other.type, other.loc, None))
            for mv in self.legal_moves(other):
                to = prov_of(mv.to)
                if to in reach and self.can_support_into(unit, to, other.layer):
                    out.append(Support(unit.type, unit.loc, other.type, other.loc, to))
        return list(dict.fromkeys(out))

    def legal_convoys(self, unit: Unit) -> list[Convoy]:
        m = self.map
        if unit.type != "F" or not m.provinces[unit.prov].is_sea:
            return []
        out = []
        for army in self.units:
            if army.type != "A" or not m.provinces[army.prov].is_land:
                continue
            for d in sorted(m.convoy_destinations(army.prov, self.fleet_zones, via_zone=unit.prov)):
                if self.entry_block(army.power, "A", d) is None:
                    out.append(Convoy("F", unit.loc, "A", army.loc, d))
        return out

    def legal_specials(self, unit: Unit) -> list[Order]:
        m, ctx = self.map, self.ctx
        power = unit.power
        if unit.layer == MILITARY and unit.prov in ctx.effects.peacekeeping:
            return []
        out: list[Order] = [Commons(unit.type, unit.loc)]
        others = [p for p in m.powers if p != power]
        if unit.type == "F" and m.provinces[unit.prov].chokepoint and unit.prov not in ctx.effects.navigation:
            out.append(Close("F", unit.loc, ()))
            out.extend(Close("F", unit.loc, (p,)) for p in others)
        if unit.type == "C":
            p = m.provinces[unit.prov]
            if p.home_of == power or (p.centre == "swing" and ctx.ownership.get(unit.prov) == power):
                out.extend(Sanction("C", unit.loc, t) for t in others)
        if unit.type == "I":
            targets = sorted(m.within_two(unit.prov, ctx.year))
            out.extend(Disrupt("I", unit.loc, t) for t in targets)
            near = set(m.any_adj.get(unit.prov, ())) | {unit.prov}
            out.extend(Forge("I", unit.loc, t) for t in others if set(m.homes(t)) & near)
        return out

    def legal_orders(self, unit: Unit) -> list[Order]:
        """Every legal movement-phase order for a unit."""
        return ([Hold(unit.type, unit.loc)] + list(self.legal_moves(unit)) + self.legal_supports(unit)
                + self.legal_convoys(unit) + self.legal_specials(unit))


def make_context(year: int, ownership: dict, umbrellas, effects: Effects | None = None) -> TurnContext:
    return TurnContext(year=year, ownership=dict(ownership), umbrellas=set(map(tuple, umbrellas)),
                       effects=effects or Effects())
