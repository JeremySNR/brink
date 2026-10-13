"""Game state and the phase loop: Spring movement -> retreats -> Fall movement -> retreats -> Winter.

Players plug in through the `Player` interface (brink.bots.base): every decision comes back as order text.
`Game.run()` plays to the end and returns the game record (docs/ARCHITECTURE.md, "Game record").
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from .adjudicator import MovementResult, adjudicate, resolve_retreats, retreat_options
from .bots.base import Player
from .map import BrinkMap
from .orders import (Build, Order, OrderError, Position, Propose, Remove, Umbrella, Vote, order_json,
                     parse_order)
from .state import CIVIL, MILITARY, TYPE_NAMES, Effects, Resolution, TurnContext, Unit, layer_of, prov_of


@dataclass
class Rules:
    """Tuning values (rulebook section 15)."""

    primacy: int = 9
    collapse: int = 10
    last_year: int = 2040
    baseline: int = 1
    baseline_late: int = 2
    late_from: int = 2036
    commons_per_point: int = 4
    recession_at: int = 6
    state_failure_at: int = 8
    drift: bool = True
    drift_winters: int = 2
    forward_bases_per_year: int = 1
    contested_supply: bool = True    # a swing state with a foreign unit in it gives its owner no supply


def _and(items) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


SEASON_CODE = {"spring": "S", "fall": "F", "winter": "W"}


class Game:
    def __init__(self, gmap: BrinkMap, players: dict[str, Player] | None = None, seed: int | None = None,
                 rules: Rules | None = None, map_name: str = "brink-v0"):
        self.map = gmap
        self.rules = rules or Rules()
        self.seed = seed
        self.map_name = map_name
        self.players: dict[str, Player] = {p: (players or {}).get(p) or Player() for p in gmap.power_order}
        start = gmap.start
        self.year: int = int(start.get("year", 2031))
        self.season = "spring"
        self.units: list[Unit] = gmap.start_units()
        self.ownership: dict[str, str | None] = gmap.start_ownership()
        self.crisis: int = int(start.get("crisis", 0))
        self.umbrellas: set[tuple[str, str]] = {tuple(x) for x in start.get("umbrellas", [])}
        self.vetoes_used: set[str] = set()
        self.drift_count: dict[str, int] = {s: 0 for s in gmap.swing_states()}
        self.eliminated: set[str] = set()
        self.effects = Effects()
        self.resolution: Resolution | None = None     # in force this year
        self.pending: Resolution | None = None        # passed this Winter, in force next year
        self._reset_year()
        self.phases: list[dict] = []
        self.result: dict | None = None
        self._pos_cache: Position | None = None

    # ------------------------------------------------------------------ queries for players
    def context(self) -> TurnContext:
        return TurnContext(year=self.year, ownership=dict(self.ownership), umbrellas=set(self.umbrellas),
                           effects=self.effects)

    def position(self) -> Position:
        """Positions + rules context for the current movement phase (validation, legal orders)."""
        if self._pos_cache is None:
            self._pos_cache = Position(self.map, self.units, self.context())
        return self._pos_cache

    def legal_orders(self, unit: Unit) -> list[Order]:
        return self.position().legal_orders(unit)

    def active_powers(self) -> list[str]:
        return [p for p in self.map.power_order if p not in self.eliminated]

    def units_of(self, power: str) -> list[Unit]:
        return [u for u in self.units if u.power == power]

    def centres(self, power: str) -> int:
        return sum(1 for owner in self.ownership.values() if owner == power)

    def contested(self, power: str) -> list[str]:
        """Swing states aligned to `power` with another power's unit in either slot."""
        foreign = {u.prov for u in self.units if u.power != power}
        return [s for s in self.map.swing_states() if self.ownership.get(s) == power and s in foreign]

    def supply(self, power: str) -> int:
        """Units a power may keep: its centres, less contested states when that rule is on."""
        c = self.centres(power)
        return c - len(self.contested(power)) if self.rules.contested_supply else c

    def swing_count(self, power: str) -> int:
        return sum(1 for s in self.map.swing_states() if self.ownership.get(s) == power)

    def votes_weight(self, power: str) -> int:
        return 1 + self.swing_count(power) // 3

    def can_veto(self, power: str) -> bool:
        return power not in self.vetoes_used

    def name(self, x: str) -> str:
        if x in self.map.powers:
            return self.map.powers[x].get("name", x)
        if x in self.map.provinces:
            return self.map.provinces[x].name
        return x

    # ------------------------------------------------------------------ phase loop
    def run(self) -> dict:
        while self.result is None:
            self.play_movement()
            self.season = "fall"
            self.play_movement()
            self.season = "winter"
            self.play_winter()
        return self.record()

    def _reset_year(self):
        self.year_closed: set[str] = set()
        self.year_sanctions: Counter = Counter()
        self.year_commons: Counter = Counter()
        self.year_commons_issued: set[str] = set()
        self.year_clash = False

    def _parse(self, power: str, texts: Iterable[str] | None, sink: list[dict]) -> list[tuple[str, Order]]:
        out = []
        for t in texts or []:
            try:
                out.append((power, parse_order(t)))
            except OrderError as e:
                sink.append({"power": power, "text": t, "unit": None, "kind": "invalid", "to": None,
                             "target": None, "result": "void", "reason": str(e)})
        return out

    def _phase(self, kind: str, units: list[Unit]) -> dict:
        code = SEASON_CODE[self.season]
        suffix = {"movement": "M", "retreat": "R", "winter": "A"}[kind]
        return {
            "name": f"{code}{self.year}{suffix}", "year": self.year, "season": self.season, "kind": kind,
            "units": [u.to_json() for u in units],
            "orders": [], "ownership": dict(sorted(self.ownership.items())), "crisis": self.crisis,
            "umbrellas": sorted([list(x) for x in self.umbrellas]), "closed": {},
            "sanctions": dict(self.year_sanctions), "commons": dict(self.year_commons),
            "assembly": None, "dislodged": [], "events": [],
        }

    def play_movement(self) -> MovementResult:
        self._pos_cache = None
        ctx = self.context()
        rec = self._phase("movement", self.units)
        bad: list[dict] = []
        orders: list[tuple[str, Order]] = []
        for p in self.active_powers():
            orders += self._parse(p, self.players[p].orders(self, p), bad)
        mres = adjudicate(self.map, self.units, orders, ctx)
        self.units = list(mres.units)
        self.umbrellas = set(mres.umbrellas)
        self.year_closed |= set(mres.closed)
        self.year_sanctions.update(mres.sanctions)
        self.year_commons.update(mres.commons)
        self.year_commons_issued |= mres.commons_issued
        self.year_clash = self.year_clash or mres.nuclear_clash
        rec["orders"] = [self._order_rec(r) for r in mres.results] + bad
        rec["closed"] = mres.closed
        rec["sanctions"] = dict(self.year_sanctions)
        rec["commons"] = dict(self.year_commons)
        rec["dislodged"] = [dict(d.unit.to_json(), **{"from": d.attacker_from}) for d in mres.dislodged]
        rec["events"] = self._movement_events(mres)
        self.phases.append(rec)
        if mres.dislodged:
            self.play_retreats(mres, ctx)
        self._pos_cache = None
        return mres

    def play_retreats(self, mres: MovementResult, ctx: TurnContext) -> None:
        ctx = TurnContext(ctx.year, ctx.ownership, set(mres.umbrellas), ctx.effects)
        opts = retreat_options(self.map, mres, ctx)
        rec = self._phase("retreat", mres.units)
        rec["dislodged"] = [dict(d.unit.to_json(), **{"from": d.attacker_from}) for d in mres.dislodged]
        bad: list[dict] = []
        orders: list[tuple[str, Order]] = []
        for p in self.active_powers():
            mine = {u: o for u, o in opts.items() if u.power == p}
            if mine:
                orders += self._parse(p, self.players[p].retreats(self, p, mine), bad)
        rr = resolve_retreats(self.map, mres.units, opts, orders)
        self.units = rr.units
        rec["orders"] = [self._order_rec(r) for r in rr.results] + bad
        events = []
        for r in rr.results:
            if r.unit is None:
                continue
            if r.order.kind == "retreat" and r.result == "ok":
                events.append(f"{self._unit_name(r.unit)} retreats to {self.name(prov_of(r.order.to))}")
        for u in rr.disbanded:
            events.append(f"{self._unit_name(u)} is disbanded")
        rec["events"] = events
        self.phases.append(rec)

    # ------------------------------------------------------------------ winter
    def play_winter(self) -> None:
        self._pos_cache = None
        m, rules = self.map, self.rules
        rec = self._phase("winter", self.units)
        events: list[str] = []
        orders_rec: list[dict] = []

        # 1. alignment, drift, home capture
        slots = {u.slot: u for u in self.units}
        for s in m.swing_states():
            present = {u.power for u in (slots.get((s, MILITARY)), slots.get((s, CIVIL))) if u}
            if len(present) == 1:
                (p,) = present
                if self.ownership.get(s) != p:
                    self.ownership[s] = p
                    events.append(f"{self.name(s)} aligns to {self.name(p)}")
        if rules.drift:
            for s in m.swing_states():
                owner = self.ownership.get(s)
                if owner is None:
                    self.drift_count[s] = 0
                    continue
                if any(u.power == owner for u in (slots.get((s, MILITARY)), slots.get((s, CIVIL))) if u):
                    self.drift_count[s] = 0
                else:
                    self.drift_count[s] += 1
                    if self.drift_count[s] >= rules.drift_winters:
                        self.ownership[s] = None
                        self.drift_count[s] = 0
                        events.append(f"{self.name(s)} drifts away from {self.name(owner)} and is unaligned")
        for h, p in m.provinces.items():
            if p.centre != "home" or m.nuclear(p.home_of):
                continue
            u = slots.get((h, MILITARY))
            if u is None or u.power == self.ownership.get(h):
                continue
            if u.power != p.home_of and m.protected(p.home_of, u.power, self.umbrellas):
                continue
            prev = self.ownership.get(h)
            self.ownership[h] = u.power
            events.append(f"{self.name(u.power)} takes {self.name(h)}" + (f" from {self.name(prev)}" if prev else ""))

        # 2. crisis
        before = self.crisis
        baseline = rules.baseline_late if self.year >= rules.late_from else rules.baseline
        total_commons = sum(self.year_commons.values())
        delta = (baseline + len(self.year_closed) + (1 if self.year_clash else 0)
                 - total_commons // rules.commons_per_point)
        self.crisis = max(0, self.crisis + delta)
        parts = [f"+{baseline} for the year"]
        if self.year_closed:
            n_closed = len(self.year_closed)
            parts.append(f"+{n_closed} for {n_closed} closed chokepoint" + ("s" if n_closed > 1 else ""))
        if self.year_clash:
            parts.append("+1 because nuclear powers fought")
        relief = total_commons // rules.commons_per_point
        if relief:
            parts.append(f"\u2212{relief} for {total_commons} Commons points")
        if self.crisis > before:
            head = f"The Crisis track rises from {before} to {self.crisis}"
        elif self.crisis < before:
            head = f"The Crisis track falls from {before} to {self.crisis}"
        else:
            head = f"The Crisis track holds at {self.crisis}"
        events.append(f"{head}: {', '.join(parts)}.")
        if self.crisis >= rules.collapse:
            events.append(f"Collapse. The Crisis track reached {rules.collapse} and every power scores 0.")
            rec["events"] = events
            rec["orders"] = orders_rec
            self.phases.append(rec)
            self._finish("collapse", None)
            return
        if self.crisis >= rules.state_failure_at:
            for s in m.swing_states():
                if self.ownership.get(s) and (s, MILITARY) not in slots and (s, CIVIL) not in slots:
                    events.append(f"State failure: {self.name(s)} becomes unaligned")
                    self.ownership[s] = None

        # 3. assembly
        rec["assembly"] = self._assembly(events, orders_rec)

        # 4. adjustments
        for p in self.active_powers():
            self._adjust(p, events, orders_rec)
        for p in self.active_powers():
            if not m.nuclear(p) and self.centres(p) == 0:
                self.eliminated.add(p)
                self.units = [u for u in self.units if u.power != p]
                events.append(f"{self.name(p)} is eliminated")

        # 5. umbrellas for next year
        self._umbrellas(events, orders_rec)

        # 6. end checks
        rec["orders"] = orders_rec
        rec["events"] = events
        self.phases.append(rec)
        leaders = [p for p in self.active_powers() if self.swing_count(p) >= rules.primacy]
        if len(leaders) == 1:
            self._finish("primacy", leaders[0])
            return
        if leaders:
            best = max(self.swing_count(p) for p in leaders)
            top = [p for p in leaders if self.swing_count(p) == best]
            if len(top) == 1:
                self._finish("primacy", top[0])
                return
        if self.year >= rules.last_year:
            self._finish("time", None)
            return
        # roll over to next year
        self.resolution = self.pending
        self.pending = None
        self.effects = Effects.from_resolution(self.resolution)
        self._reset_year()
        self.year += 1
        self.season = "spring"

    def resolution_options(self, power: str) -> list[Resolution]:
        m = self.map
        out = [Resolution("sanctions", p) for p in self.active_powers() if p != power]
        out += [Resolution("peacekeeping", s) for s in m.swing_states()]
        out += [Resolution("navigation", c) for c in m.chokepoints()]
        out.append(Resolution("accord"))
        out += [Resolution("recognition", s) for s in m.swing_states()]
        return out

    def _assembly(self, events: list[str], orders_rec: list[dict]) -> dict:
        active = self.active_powers()
        order_idx = {p: i for i, p in enumerate(self.map.power_order)}
        chair = min(active, key=lambda p: (self.centres(p), order_idx[p]))
        text = self.players[chair].propose(self, chair)
        proposal: Resolution | None = None
        if text:
            try:
                od = parse_order(text)
            except OrderError:
                od = None
            valid = isinstance(od, Propose) and (od.resolution is None or od.resolution in
                                                 self.resolution_options(chair))
            orders_rec.append({"power": chair, "text": text, "unit": None, "kind": "propose", "to": None,
                               "target": None, "result": "ok" if valid else "void"})
            if valid:
                proposal = od.resolution
        info = {"chair": chair, "proposal": proposal.text() if proposal else None, "votes": {}, "passed": False}
        if proposal is None:
            events.append(f"The Assembly meets; {self.name(chair)}, in the chair, proposes nothing.")
            return info
        yes = no = 0
        vetoed_by = []
        for p in active:
            vt = self.players[p].vote(self, p, proposal.text())
            try:
                od = parse_order(vt) if vt else None
            except OrderError:
                od = None
            choice = od.choice if isinstance(od, Vote) else None
            result = "ok"
            if choice == "veto" and not self.can_veto(p):
                choice, result = "no", "void"
            if choice is None:
                if vt:
                    orders_rec.append({"power": p, "text": vt, "unit": None, "kind": "vote", "to": None,
                                       "target": None, "result": "void"})
                continue
            orders_rec.append(dict(order_json(p, Vote(choice)), text=vt if vt else Vote(choice).text(),
                                   result=result))
            info["votes"][p] = choice
            w = self.votes_weight(p)
            if choice == "yes":
                yes += w
            elif choice == "no":
                no += w
            else:
                vetoed_by.append(p)
                self.vetoes_used.add(p)
        if vetoed_by:
            events.append(f"{_and(self.name(p) for p in vetoed_by)} veto{'es' if len(vetoed_by) == 1 else ''} "
                          f"{self.describe(proposal)} in the Assembly.")
        elif yes > no:
            info["passed"] = True
            self.pending = proposal
            events.append(f"The Assembly passes {self.describe(proposal)}, {yes} votes to {no}.")
            if proposal.kind == "recognition" and self.ownership.get(proposal.arg):
                events.append(f"{self.name(proposal.arg)} is recognised and becomes unaligned")
                self.ownership[proposal.arg] = None
        else:
            events.append(f"The Assembly rejects {self.describe(proposal)}, {yes} votes to {no}.")
        return info

    def describe(self, res: Resolution) -> str:
        """A resolution in plain words: 'a sanctions regime on China'."""
        arg = self.name(res.arg) if res.arg else ""
        return {"sanctions": f"a sanctions regime on {arg}", "peacekeeping": f"peacekeeping in {arg}",
                "navigation": f"freedom of navigation at {arg}", "accord": "a climate accord",
                "recognition": f"recognition of {arg}"}.get(res.kind, res.text())

    def build_options(self, power: str) -> list[Build]:
        """Every build site open to `power` right now (before any build this Winter)."""
        m = self.map
        slots = {u.slot for u in self.units}
        out: list[Build] = []
        for pid in m.homes(power):
            if self.ownership.get(pid) != power:
                continue
            if (pid, MILITARY) not in slots:
                out.append(Build("A", pid))
                out.extend(Build("F", loc) for loc in m.fleet_locs(pid))
            if (pid, CIVIL) not in slots:
                out += [Build("C", pid), Build("I", pid)]
        mine_civil = {u.prov for u in self.units if u.power == power and u.layer == CIVIL}
        for s in m.swing_states():
            if self.ownership.get(s) != power:
                continue
            if (s, CIVIL) not in slots:
                out += [Build("C", s), Build("I", s)]
            if (s, MILITARY) not in slots and s in mine_civil:
                out.append(Build("A", s))
        return out

    def is_forward_base(self, power: str, b: Build) -> bool:
        return b.utype == "A" and self.map.provinces[prov_of(b.loc)].centre == "swing"

    def _build_ok(self, power: str, b: Build, slots: set, forward_used: int) -> str | None:
        m = self.map
        pid = prov_of(b.loc)
        if pid not in m.provinces or b.utype not in TYPE_NAMES:
            return "unknown site or unit type"
        layer = layer_of(b.utype)
        if (pid, layer) in slots:
            return "slot occupied"
        p = m.provinces[pid]
        own_home = pid in m.homes(power) and self.ownership.get(pid) == power
        own_swing = p.centre == "swing" and self.ownership.get(pid) == power
        if layer == CIVIL:
            return None if (own_home or own_swing) else "civil units build in owned homes or aligned states"
        if b.utype == "F":
            if not own_home:
                return "fleets build only in owned original homes"
            if b.loc not in m.fleet_locs(pid):
                return "fleet needs a coastal home (and a coast, if it has several)"
            return None
        if own_home:
            return None
        if own_swing:
            if forward_used >= self.rules.forward_bases_per_year:
                return "only one forward base a year"
            if not any(u.power == power and u.slot == (pid, CIVIL) for u in self.units):
                return "forward base needs your civil unit there"
            return None
        return "armies build in owned original homes (or one forward base)"

    def _adjust(self, power: str, events: list[str], orders_rec: list[dict]) -> None:
        rules = self.rules
        c = self.supply(power)
        if c < self.centres(power):
            states = [self.name(x) for x in self.contested(power)]
            events.append(f"{_and(states)} {'is' if len(states) == 1 else 'are'} contested, so "
                          f"{self.name(power)} gets no supply from {'it' if len(states) == 1 else 'them'} this Winter.")
        mine = self.units_of(power)
        n = len(mine)
        if n > c:
            need = n - c
            texts = self.players[power].adjust(self, power, -need) or []
            removed: list[Unit] = []
            for t in texts:
                try:
                    od = parse_order(t)
                except OrderError:
                    od = None
                u = None
                if isinstance(od, Remove):
                    u = next((x for x in mine if x.type == od.utype and x.prov == prov_of(od.loc)
                              and x not in removed), None)
                if u is None or len(removed) >= need:
                    orders_rec.append({"power": power, "text": t, "unit": None, "kind": "disband", "to": None,
                                       "target": None, "result": "void"})
                    continue
                removed.append(u)
                orders_rec.append(order_json(power, Remove(u.type, u.loc), "ok"))
            if len(removed) < need:
                for u in self._default_disbands(power, [x for x in mine if x not in removed])[: need - len(removed)]:
                    removed.append(u)
                    orders_rec.append(dict(order_json(power, Remove(u.type, u.loc), "ok"), default=True))
            self.units = [u for u in self.units if u not in removed]
            for u in removed:
                events.append(f"{self._unit_name(u)} is disbanded")
            return
        sanctions = self.year_sanctions.get(power, 0) + (1 if power in self.effects.sanctions_regime else 0)
        recession = 1 if self.crisis >= rules.recession_at else 0
        accord = 1 if self.effects.climate_accord and power not in self.year_commons_issued else 0
        builds = max(0, c - n - sanctions - recession - accord)
        if c - n > 0 and builds < c - n:
            lost = c - n - builds
            why = [w for w, k in (("sanctions", sanctions), ("the recession", recession),
                                  ("the climate accord", accord)) if k]
            events.append(f"{self.name(power)} loses {lost} build{'s' if lost > 1 else ''}"
                          + (f" to {_and(why)}." if why else "."))
        if builds == 0:
            return
        texts = self.players[power].adjust(self, power, builds) or []
        slots = {u.slot for u in self.units}
        done = 0
        forward = 0
        for t in texts:
            try:
                od = parse_order(t)
            except OrderError:
                od = None
            why = "not a build" if not isinstance(od, Build) else (
                "no builds left" if done >= builds else self._build_ok(power, od, slots, forward))
            if why:
                orders_rec.append({"power": power, "text": t, "unit": None, "kind": "build", "to": None,
                                   "target": None, "result": "void", "reason": why})
                continue
            u = Unit(power, od.utype, od.loc)
            if self.is_forward_base(power, od):
                forward += 1
            slots.add(u.slot)
            self.units.append(u)
            done += 1
            orders_rec.append(order_json(power, od, "ok"))
            events.append(f"{self.name(power)} builds {TYPE_NAMES[u.type]} in {self.name(u.prov)}")

    def _default_disbands(self, power: str, units: list[Unit]) -> list[Unit]:
        homes = self.map.homes(power)

        def dist(u: Unit) -> int:
            ds = [self.map.distance(u.prov, h) for h in homes]
            ds = [d for d in ds if d is not None]
            return min(ds) if ds else 10 ** 6

        return sorted(units, key=lambda u: (-dist(u), u.prov, u.type))

    def _umbrellas(self, events: list[str], orders_rec: list[dict]) -> None:
        m = self.map
        active = set(self.active_powers())
        self.umbrellas = {(a, b) for a, b in self.umbrellas if b in active and a in active}
        for p in self.active_powers():
            if not m.nuclear(p):
                continue
            texts = self.players[p].umbrellas(self, p)
            if texts is None:
                continue
            new = set()
            for t in texts:
                try:
                    od = parse_order(t)
                except OrderError:
                    od = None
                ok = (isinstance(od, Umbrella) and od.power == p and od.protected in active
                      and od.protected != p and not m.nuclear(od.protected))
                orders_rec.append(order_json(p, od, "ok") if ok else {
                    "power": p, "text": t, "unit": None, "kind": "umbrella", "to": None, "target": None,
                    "result": "void"})
                if ok:
                    new.add((p, od.protected))
            old = {x for x in self.umbrellas if x[0] == p}
            for a, b in sorted(new - old):
                events.append(f"{self.name(a)} declares an umbrella over {self.name(b)}")
            for a, b in sorted(old - new):
                events.append(f"{self.name(a)} lets its umbrella over {self.name(b)} lapse")
            self.umbrellas = (self.umbrellas - old) | new

    # ------------------------------------------------------------------ end and record
    def scores(self) -> dict[str, float]:
        cs = {p: self.centres(p) for p in self.map.power_order}
        total = sum(c * c for c in cs.values())
        return {p: (round(100 * c * c / total, 2) if total else 0.0) for p, c in cs.items()}

    def _finish(self, kind: str, winner: str | None) -> None:
        if kind == "collapse":
            scores = {p: 0.0 for p in self.map.power_order}
        elif kind == "primacy":
            scores = {p: (100.0 if p == winner else 0.0) for p in self.map.power_order}
        else:
            scores = self.scores()
        self.result = {
            "kind": kind, "winner": winner, "scores": scores, "year": self.year,
            "centres": {p: self.centres(p) for p in self.map.power_order},
            "final": {"units": [u.to_json() for u in self.units], "ownership": dict(sorted(self.ownership.items())),
                      "crisis": self.crisis, "umbrellas": sorted([list(x) for x in self.umbrellas])},
        }

    def record(self) -> dict:
        return {
            "map": self.map_name, "seed": self.seed,
            "players": {p: getattr(pl, "name", type(pl).__name__) for p, pl in self.players.items()},
            "phases": self.phases, "result": self.result,
        }

    # ------------------------------------------------------------------ helpers
    def _order_rec(self, r) -> dict:
        d = order_json(r.power, r.order, r.result)
        if r.unit is not None:
            d["unit"] = {"type": r.unit.type, "at": r.unit.loc}
        if r.reason:
            d["reason"] = r.reason
        return d

    def _unit_name(self, u: Unit) -> str:
        return f"{self.map.powers[u.power].get('adjective', self.name(u.power))} {TYPE_NAMES[u.type]} in {self.name(u.prov)}"

    def _movement_events(self, mres: MovementResult) -> list[str]:
        ev = []
        for a, b in mres.withdrawn:
            ev.append(f"{self.name(a)} withdraws its umbrella over {self.name(b)}")
        for z, ps in sorted(mres.closed.items()):
            ev.append(f"{self.name(z)} is closed to {', '.join(self.name(p) for p in ps)}")
        for r in mres.results:
            if r.result != "ok" or r.unit is None:
                continue
            o = r.order
            if o.kind == "sanction":
                ev.append(f"{self.name(r.power)} sanctions {self.name(o.target)}")
            elif o.kind == "disrupt":
                ev.append(f"{self._unit_name(r.unit)} disrupts {self.name(o.target)}")
        com = Counter(mres.commons)
        for p, n in sorted(com.items()):
            ev.append(f"{self.name(p)} scores {n} Commons")
        for d in mres.dislodged:
            ev.append(f"{self._unit_name(d.unit)} is dislodged (attack from {self.name(d.attacker_from)})")
        if mres.nuclear_clash:
            ev.append("Nuclear powers clash: the Crisis track will rise")
        return ev


def play_game(gmap: BrinkMap, players: dict[str, Player], seed: int | None = None,
              rules: Rules | None = None, map_name: str = "brink-v0") -> dict:
    return Game(gmap, players, seed=seed, rules=rules, map_name=map_name).run()
