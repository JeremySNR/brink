"""StrategistBot: a DumbBot-style heuristic player for Brink.

Each movement phase it
1. values every (province, layer) slot for its power: swing states to align or defend, enemy-aligned
   states to contest, threatened homes, chokepoints; then spreads that value outward so far-away units
   drift towards it,
2. assigns moves greedily (best unit/destination first, never two own units into one slot),
3. backs attacks on occupied slots and threatened holds with supports, across layers,
4. gives spare units a job: sanctions from Capitals, disrupts from Influence, chokepoint closures,
   and a share of Commons sized by its temperament and the Crisis track.

Alliances come from outside (a callable returning the current allies), so a simulation can assign,
change and break them. Allies are never attacked, sanctioned or voted against.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Iterable

from ..orders import (Close, Commons, Disrupt, Hold, Move, Order, OrderError, Sanction, Support, Withdraw,
                      parse_order)
from ..state import CIVIL, MILITARY, Unit, prov_of
from .base import Player

if TYPE_CHECKING:  # pragma: no cover
    from ..game import Game
    from ..orders import Position


@dataclass(frozen=True)
class Temperament:
    civic: float = 0.5        # share of the Crisis burden it accepts (0 = free-rider)
    aggression: float = 0.5   # appetite for attacks, closures, sanctions and betrayals
    military: float = 0.35    # target share of military units in its force


class StrategistBot(Player):
    name = "strategist"

    def __init__(self, seed: int | None = None, temperament: Temperament | None = None,
                 allies: Callable[["Game", str], Iterable[str]] | Iterable[str] = (), noise: float = 0.6,
                 coalition: dict | None = None):
        self.rng = random.Random(seed)
        self.t = temperament or Temperament()
        self._allies = allies
        self._cache: dict = {}
        self.noise = noise            # random jitter on move scores; search bots raise it to sample plans
        self.coalition = coalition    # shared board: {phase index: {power: [order text]}}; allies read it
        self.post = True

    # ------------------------------------------------------------------ alliances
    def allies(self, game: "Game", power: str) -> set[str]:
        a = self._allies(game, power) if callable(self._allies) else self._allies
        return set(a) - {power}

    def friendly(self, game: "Game", power: str, other: str | None) -> bool:
        return other is not None and (other == power or other in self.allies(game, power))

    # ------------------------------------------------------------------ valuation
    def _threats(self, game: "Game", power: str) -> dict[tuple[str, str], int]:
        """How many non-friendly units could move into each slot this turn."""
        pos = game.position()
        out: dict[tuple[str, str], int] = defaultdict(int)
        for u in pos.units:
            if self.friendly(game, power, u.power):
                continue
            for mv in pos.legal_moves(u):
                out[(prov_of(mv.to), u.layer)] += 1
        return out

    def _values(self, game: "Game", power: str):
        key = (len(game.phases), power)
        if self._cache.get("key") == key:
            return self._cache["val"]
        m, own, t = game.map, game.ownership, self.t
        threats = self._threats(game, power)
        pos = game.position()
        my_civil = {u.prov for u in pos.units if u.power == power and u.layer == CIVIL}
        civil_near = set(my_civil)
        for p in my_civil:
            civil_near |= m.any_adj.get(p, frozenset())
        ceded = self._ceded_to_allies(game, power, pos)
        base = {CIVIL: {}, MILITARY: {}}
        for pid, p in m.provinces.items():
            if not m.is_open(pid, game.year):
                continue
            owner = own.get(pid)
            civ = mil = 0.0
            if p.centre == "swing":
                if owner is None:
                    civ = 2.0 if pid in ceded else 10.0
                elif owner == power:
                    civ = 3.0 + 2.5 * threats.get((pid, CIVIL), 0)
                    mil = 2.0 + 2.0 * threats.get((pid, MILITARY), 0)
                    if any(u.prov == pid and u.power != power for u in pos.units):
                        mil += 4.0   # an occupier denies this state's supply: evict it
                elif not self.friendly(game, power, owner):
                    civ = 8.0
                    # occupying a rival's state denies its supply even without a civil follow-up
                    mil = (6.5 if pid in civil_near else 4.5) * (0.6 + t.aggression)
            elif p.centre == "home":
                if p.home_of == power:
                    civ = 0.5
                    if not m.nuclear(power):
                        mil = 1.0 + 9.0 * min(1, threats.get((pid, MILITARY), 0))
                elif not m.nuclear(p.home_of) and not self.friendly(game, power, p.home_of):
                    if pos.entry_block(power, "A", pid) is None:
                        mil = 9.0 * t.aggression
            if p.chokepoint:
                mil = max(mil, 2.5)
            base[CIVIL][pid] = civ
            base[MILITARY][pid] = mil
        spread = {}
        for layer, decay in ((CIVIL, 0.62), (MILITARY, 0.55)):
            v = dict(base[layer])
            for _ in range(5):
                nv = {}
                for pid in v:
                    best = max((v.get(n, 0.0) for n in m.any_adj.get(pid, ())), default=0.0)
                    nv[pid] = max(base[layer][pid], decay * best)
                v = nv
            spread[layer] = v
        val = (base, spread, threats)
        self._cache = {"key": key, "val": val}
        return val

    def _ceded_to_allies(self, game: "Game", power: str, pos) -> set[str]:
        """Unaligned swing states an ally's civil units are strictly closer to: leave them to the ally."""
        allies = self.allies(game, power)
        if not allies:
            return set()
        m = game.map

        def nearest(p: str, s: str) -> int:
            ds = [m.distance(u.prov, s) for u in pos.units if u.power == p and u.layer == CIVIL]
            return min((d for d in ds if d is not None), default=99)

        out = set()
        for s in m.swing_states():
            if game.ownership.get(s) is None:
                mine = nearest(power, s)
                if any(nearest(a, s) < mine for a in allies):
                    out.add(s)
        return out

    def _recent_bounces(self, game: "Game", power: str) -> set[tuple[str, str]]:
        """(unit location, destination) of this power's moves that bounced last movement phase."""
        for ph in reversed(game.phases):
            if ph["kind"] == "movement":
                return {(o["unit"]["at"], o.get("to")) for o in ph["orders"]
                        if o.get("power") == power and o.get("kind") == "move" and o.get("result") == "bounce"}
        return set()

    # ------------------------------------------------------------------ movement
    def orders(self, game: "Game", power: str) -> list[str]:
        pos = game.position()
        m = game.map
        base, spread, threats = self._values(game, power)
        mine = [u for u in pos.units if u.power == power]
        occupied = {u.slot: u for u in pos.units}
        bounced = self._recent_bounces(game, power)

        # 1. score every candidate (unit, order)
        cands: list[tuple[float, Unit, Order]] = []
        for u in mine:
            here = base[u.layer].get(u.prov, 0.0)
            stay = here * 1.15 + 0.3
            if u.layer == CIVIL and m.provinces[u.prov].centre == "swing" and \
                    game.ownership.get(u.prov) != power and occupied.get((u.prov, MILITARY)) in (None, u) :
                stay += 4.0   # sitting in a state you don't own yet is how you align it
            cands.append((stay, u, Hold(u.type, u.loc)))
            for mv in pos.legal_moves(u):
                d = prov_of(mv.to)
                occ = occupied.get((d, u.layer))
                if occ is not None and self.friendly(game, power, occ.power) and occ.power != power:
                    continue
                score = spread[u.layer].get(d, 0.0)
                rivals = threats.get((d, u.layer), 0)
                if occ is not None and occ.power != power:
                    score *= 0.55 + 0.5 * self.t.aggression
                    rivals = max(0, rivals - 1)
                score -= 0.7 * rivals
                if (u.loc, mv.to) in bounced:
                    score -= 2.5
                score += self.rng.random() * self.noise
                cands.append((score, u, mv))
        cands.sort(key=lambda c: -c[0])

        # 2. greedy assignment (slots an ally already plans to take are off limits)
        ally_moves = self._ally_moves(game, power, pos)
        plan: dict[Unit, Order] = {}
        claimed: dict[tuple[str, str], Unit | None] = {(prov_of(mv.to), au.layer): None for au, mv in ally_moves}
        leaving: set[tuple[str, str]] = set()
        for score, u, od in cands:
            if u in plan:
                continue
            if isinstance(od, Move):
                slot = (prov_of(od.to), u.layer)
                if slot in claimed:
                    continue
                occ = occupied.get(slot)
                if occ is not None and occ.power == power and slot not in leaving:
                    continue
                claimed[slot] = u
                leaving.add(u.slot)
            else:
                if u.slot in claimed:
                    continue
                claimed[u.slot] = u
            plan[u] = od
        stay_value = {u: base[u.layer].get(u.prov, 0.0) for u in mine}

        # 3. supports: attacks on occupied slots first, then threatened holds
        def supporters_for(target: Unit, dest: str) -> list[Unit]:
            out = []
            for s in mine:
                if s == target or not isinstance(plan.get(s), Hold):
                    continue
                if stay_value[s] >= 6 and threats.get(s.slot, 0):
                    continue  # busy defending something that matters
                if pos.can_support_into(s, dest, target.layer):
                    out.append(s)
            out.sort(key=lambda s: stay_value[s])
            return out

        attacks = [(u, od) for u, od in plan.items() if isinstance(od, Move)
                   and occupied.get((prov_of(od.to), u.layer)) not in (None,)
                   and occupied[(prov_of(od.to), u.layer)].power != power]
        attacks.sort(key=lambda a: -spread[a[0].layer].get(prov_of(a[1].to), 0))
        for u, od in attacks:
            dest = prov_of(od.to)
            need = 1 + min(2, threats.get((dest, u.layer), 0))
            for s in supporters_for(u, dest)[:need]:
                plan[s] = Support(s.type, s.loc, u.type, u.loc, dest)
        contested_moves = [(u, od) for u, od in plan.items() if isinstance(od, Move)
                           and threats.get((prov_of(od.to), u.layer), 0) > 0 and (u, od) not in attacks]
        for u, od in contested_moves:
            dest = prov_of(od.to)
            for s in supporters_for(u, dest)[:1]:
                plan[s] = Support(s.type, s.loc, u.type, u.loc, dest)
        for au, mv in ally_moves:   # back an ally's attack with a spare unit
            dest = prov_of(mv.to)
            occ = occupied.get((dest, au.layer))
            if (occ is not None and not self.friendly(game, power, occ.power)) or threats.get((dest, au.layer), 0):
                for s in supporters_for(au, dest)[:1]:
                    plan[s] = Support(s.type, s.loc, au.type, au.loc, dest)
        for u in sorted(mine, key=lambda x: -stay_value[x]):
            if isinstance(plan.get(u), Hold) and threats.get(u.slot, 0) and stay_value[u] >= 2:
                for s in supporters_for(u, u.prov)[: min(2, threats[u.slot])]:
                    plan[s] = Support(s.type, s.loc, u.type, u.loc, None)

        # 4. jobs for units still holding
        self._specials(game, power, pos, plan, stay_value, threats)
        self._commons(game, power, plan, stay_value)
        out = [o.text() for o in plan.values()]
        out += self._withdrawals(game, power, pos, plan)
        if self.post:
            self.publish(game, power, out)
        return out

    def publish(self, game: "Game", power: str, orders: list[str]) -> None:
        if self.coalition is not None:
            self.coalition.setdefault(len(game.phases), {})[power] = list(orders)

    def _ally_moves(self, game: "Game", power: str, pos) -> list[tuple[Unit, Move]]:
        """Moves allies have already committed to this phase (allies plan in power order)."""
        if self.coalition is None:
            return []
        posted = self.coalition.get(len(game.phases), {})
        out = []
        for ally in self.allies(game, power):
            for text in posted.get(ally, ()):
                try:
                    od = parse_order(text)
                except OrderError:
                    continue
                if isinstance(od, Move):
                    u = pos.find(od.utype, od.loc, ally)
                    if u is not None:
                        out.append((u, od))
        return out

    def _leader(self, game: "Game", power: str) -> str | None:
        rivals = [p for p in game.active_powers() if not self.friendly(game, power, p)]
        if not rivals:
            return None
        return max(rivals, key=lambda p: (game.centres(p), game.swing_count(p)))

    def _specials(self, game, power, pos: "Position", plan, stay_value, threats):
        m, t = game.map, self.t
        leader = self._leader(game, power)
        attack_targets = {prov_of(o.to) for o in plan.values() if isinstance(o, Move)}
        defended = {u.prov for u in plan if isinstance(plan[u], Hold) and threats.get(u.slot, 0)}
        hot = attack_targets | defended
        for u, od in list(plan.items()):
            if not isinstance(od, Hold):
                continue
            specials = pos.legal_specials(u)
            if u.type == "C" and leader and self.rng.random() < 0.35 + 0.6 * t.aggression:
                for s in specials:
                    if isinstance(s, Sanction) and s.target == leader:
                        plan[u] = s
                        break
            elif u.type == "I":
                best, best_score = None, 0
                for s in specials:
                    if not isinstance(s, Disrupt):
                        continue
                    enemies = sum(1 for e in pos.units if e.prov == s.target
                                  and not self.friendly(game, power, e.power)
                                  and any(n in hot for n in m.any_adj.get(e.prov, ())))
                    if enemies > best_score:
                        best, best_score = s, enemies
                if best is not None:
                    plan[u] = best
            elif u.type == "F" and m.provinces[u.prov].chokepoint and game.crisis <= 4:
                enemies = [p for p in game.active_powers() if not self.friendly(game, power, p)]
                if enemies and self.rng.random() < 0.6 * t.aggression:
                    targets = tuple(sorted(enemies, key=lambda p: -game.centres(p))[:2])
                    close = Close("F", u.loc, targets)
                    if pos.validate(power, close)[1] is None:
                        plan[u] = close

    def _commons(self, game, power, plan, stay_value):
        r, t = game.rules, self.t
        n = max(1, len(game.active_powers()))
        baseline = r.baseline_late if game.year >= r.late_from else r.baseline
        projected = game.crisis + baseline + len(getattr(game, "year_closed", ()))
        tolerance = 8.6 - 4.5 * t.civic
        if game.crisis >= 8:
            tolerance -= 2
        deficit = max(0.0, projected - tolerance)
        share = math.ceil(deficit * r.commons_per_point / n * (0.4 + 1.2 * t.civic))
        accord = game.effects.climate_accord and power not in game.year_commons_issued
        need = max(share // 2 + (share % 2 if game.season == "spring" else 0), 1 if accord else 0)
        if need <= 0:
            return
        idle = sorted((u for u, o in plan.items() if isinstance(o, (Hold, Sanction, Disrupt, Close))),
                      key=lambda u: stay_value[u])
        for u in idle[:need]:
            plan[u] = Commons(u.type, u.loc)

    def _withdrawals(self, game, power, pos, plan) -> list[str]:
        """A betrayal: drop an umbrella and attack the newly exposed home in the same turn."""
        m = game.map
        out = []
        for protector, protected in sorted(game.umbrellas):
            if protector != power or self.friendly(game, power, protected):
                continue
            if self.rng.random() > 0.25 * self.t.aggression:
                continue
            others = [(a, b) for a, b in game.umbrellas if b == protected and a != power]
            if others:
                continue
            for u, od in plan.items():
                if u.type != "A" or isinstance(od, Move):
                    continue
                targets = [h for h in m.homes(protected) if h in m.army_moves(u.prov, game.year)
                           and game.ownership.get(h) == protected]
                if targets:
                    plan[u] = Move("A", u.loc, targets[0])
                    out.append(Withdraw(power, protected).text())
                    break
        if out:
            return out
        return []

    # ------------------------------------------------------------------ other phases
    def retreats(self, game, power, options):
        _, spread, _ = self._values(game, power)
        out = []
        for u, opts in options.items():
            if u.power != power:
                continue
            if opts:
                best = max(opts, key=lambda d: spread[u.layer].get(prov_of(d), 0) + self.rng.random() * 0.1)
                out.append(f"{u.type} {u.loc} R {best}")
            else:
                out.append(f"{u.type} {u.loc} D")
        return out

    def adjust(self, game, power, change):
        m = game.map
        if change < 0:
            base, _, _ = self._values(game, power)
            units = sorted(game.units_of(power), key=lambda u: base[u.layer].get(u.prov, 0))
            return [f"DISBAND {u.type} {u.loc}" for u in units[:-change]]
        if change == 0:
            return []
        self._cache = {}
        own = game.ownership
        units = game.units_of(power)
        n_mil = sum(1 for u in units if u.layer == MILITARY)
        n_cap = sum(1 for u in units if u.type == "C")
        n_inf = sum(1 for u in units if u.type == "I")
        frontier: dict[str, float] = {}
        for pid in m.provinces:
            for n in m.any_adj.get(pid, ()):
                pn = m.provinces[n]
                if pn.centre == "swing" and own.get(n) != power and not self.friendly(game, power, own.get(n)):
                    frontier[pid] = frontier.get(pid, 0) + (2 if own.get(n) is None else 1)
        builds = game.build_options(power)
        out, used, forward = [], set(), False
        for _ in range(change):
            total = n_mil + n_cap + n_inf + 1
            want_mil = n_mil / total < self.t.military
            scored = []
            for b in builds:
                site = prov_of(b.loc)
                key = (site, MILITARY if b.utype in "AF" else CIVIL)
                if key in used:
                    continue
                is_fwd = game.is_forward_base(power, b)
                if is_fwd and forward:
                    continue
                s = frontier.get(site, 0) + self.rng.random()
                if b.utype in "AF":
                    s += 3 if want_mil else -3
                    if b.utype == "F":
                        s += 0.8 if any(m.provinces[q].chokepoint for q in m.any_adj.get(site, ())) else -0.5
                    if is_fwd:
                        s += 2
                else:
                    s += -3 if want_mil else 3
                    if b.utype == "I":
                        s += 1 if n_inf < n_cap else -1
                    else:
                        s += 1 if n_cap <= n_inf else -1
                scored.append((s, b, key, is_fwd))
            if not scored:
                break
            s, b, key, is_fwd = max(scored, key=lambda x: x[0])
            used.add(key)
            forward = forward or is_fwd
            out.append(b.text())
            if b.utype in "AF":
                n_mil += 1
            elif b.utype == "C":
                n_cap += 1
            else:
                n_inf += 1
        return out

    def propose(self, game, power):
        if game.crisis >= 6 and self.t.civic >= 0.35:
            return "PROPOSE ACCORD"
        leader = self._leader(game, power)
        if leader and game.centres(leader) >= game.centres(power) + 2:
            return f"PROPOSE SANCTIONS {leader}"
        return None

    def vote(self, game, power, proposal):
        words = proposal.upper().replace("PROPOSE", "").split()
        if not words:
            return "VOTE NO"
        kind, arg = words[0], (words[1] if len(words) > 1 else None)
        if kind == "SANCTIONS":
            if arg == power:
                if game.can_veto(power) and game.centres(power) >= 8:
                    return "VOTE VETO"
                return "VOTE NO"
            if self.friendly(game, power, arg):
                return "VOTE NO"
            ranked = sorted(game.active_powers(), key=lambda p: -game.centres(p))
            return "VOTE YES" if arg in ranked[:3] else ("VOTE YES" if self.rng.random() < 0.4 else "VOTE NO")
        if kind == "ACCORD":
            return "VOTE YES" if self.t.civic >= 0.5 or game.crisis >= 7 else "VOTE NO"
        if kind == "NAVIGATION":
            mine = any(u.type == "F" and u.prov == arg for u in game.units_of(power))
            return "VOTE NO" if mine else "VOTE YES"
        owner = game.ownership.get(arg)
        if kind == "PEACEKEEPING":
            return "VOTE YES" if owner is None or self.friendly(game, power, owner) else "VOTE NO"
        if kind == "RECOGNITION":
            return "VOTE YES" if owner is not None and not self.friendly(game, power, owner) else "VOTE NO"
        return "VOTE NO"

    def umbrellas(self, game, power):
        m = game.map
        if not m.nuclear(power):
            return None
        out = []
        for p in game.active_powers():
            if p == power or m.nuclear(p):
                continue
            current = (power, p) in game.umbrellas
            if self.friendly(game, power, p) or (current and self.t.aggression < 0.7):
                out.append(f"{power} UMBRELLA {p}")
        return out
