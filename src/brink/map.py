"""BrinkMap: the engine's view of build/map.json (provinces, adjacency, trade routes, start)."""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .state import CIVIL, MILITARY, Unit, layer_of, prov_of


@dataclass(frozen=True)
class Province:
    id: str
    name: str
    kind: str                 # land | sea
    centre: str = "none"      # home | swing | none
    home_of: str | None = None
    coastal: bool = False
    chokepoint: bool = False
    impassable: bool = False
    opens: int | None = None
    ocean: bool = False       # open ocean: Capital can't short-hop across it, only use trade routes

    @property
    def is_land(self) -> bool:
        return self.kind == "land"

    @property
    def is_sea(self) -> bool:
        return self.kind == "sea"


@dataclass(frozen=True)
class TradeRoute:
    id: str
    name: str
    ends: tuple[str, str]
    path: tuple[str, ...]
    opens: int | None = None

    def other_end(self, prov: str) -> str | None:
        a, b = self.ends
        return b if prov == a else a if prov == b else None


@dataclass(frozen=True)
class CapitalMeans:
    """One way a Capital can reach a province: 'land' step, short 'hop', or trade 'route'."""

    kind: str
    zones: frozenset[str]
    route: str | None = None


class BrinkMap:
    def __init__(self, data: dict):
        self.data = data
        self.version = data.get("version", 1)
        self.powers: dict[str, dict] = {k: dict(v) for k, v in data["powers"].items()}
        self.power_order: list[str] = list(self.powers)
        self.provinces: dict[str, Province] = {}
        for pid, p in data["provinces"].items():
            self.provinces[pid] = Province(
                id=pid, name=p.get("name", pid), kind=p["kind"], centre=p.get("centre", "none") or "none",
                home_of=p.get("home_of"), coastal=bool(p.get("coastal", False)),
                chokepoint=bool(p.get("chokepoint", False)), impassable=bool(p.get("impassable", False)),
                opens=p.get("opens"), ocean=bool(p.get("ocean", False)))
        adj = data["adj"]
        self.army_adj: dict[str, frozenset[str]] = {k: frozenset(v) for k, v in adj.get("army", {}).items()}
        self.fleet_adj: dict[str, frozenset[str]] = {k: frozenset(v) for k, v in adj.get("fleet", {}).items()}
        self.any_adj: dict[str, frozenset[str]] = {k: frozenset(v) for k, v in adj.get("any", {}).items()}
        self.coasts: dict[str, list[str]] = {k: list(v) for k, v in (data.get("coasts") or {}).items()}
        self.routes: list[TradeRoute] = [
            TradeRoute(r["id"], r.get("name", r["id"]), tuple(r["ends"]), tuple(r["path"]), r.get("opens"))
            for r in data.get("trade_routes", [])]
        self.start: dict = data.get("start", {})
        for pw, info in self.powers.items():
            info.setdefault("homes", [p.id for p in self.provinces.values() if p.home_of == pw])
        # every province gets an entry, so lookups never KeyError
        for pid in self.provinces:
            self.army_adj.setdefault(pid, frozenset())
            self.any_adj.setdefault(pid, frozenset())
        # seas adjacent to each province (via any of its fleet locations)
        self.sea_neighbours: dict[str, frozenset[str]] = {}
        for pid, p in self.provinces.items():
            seas = set()
            for loc in self.fleet_locs(pid):
                seas |= {prov_of(n) for n in self.fleet_adj.get(loc, ()) if self.provinces[prov_of(n)].is_sea}
            self.sea_neighbours[pid] = frozenset(seas)
        self._dist_cache: dict[str, dict[str, int]] = {}

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, path: str | Path) -> "BrinkMap":
        with open(path) as f:
            return cls(json.load(f))

    # ------------------------------------------------------------------ basic queries
    def prov(self, loc: str) -> Province:
        return self.provinces[prov_of(loc)]

    def is_open(self, pid: str, year: int) -> bool:
        p = self.provinces.get(pid)
        return p is not None and not p.impassable and (p.opens is None or year >= p.opens)

    def nuclear(self, power: str) -> bool:
        return bool(self.powers[power].get("nuclear"))

    def homes(self, power: str) -> list[str]:
        return list(self.powers[power]["homes"])

    def swing_states(self) -> list[str]:
        return [p.id for p in self.provinces.values() if p.centre == "swing"]

    def centres(self) -> list[str]:
        return [p.id for p in self.provinces.values() if p.centre in ("home", "swing")]

    def chokepoints(self) -> list[str]:
        return [p.id for p in self.provinces.values() if p.chokepoint]

    def fleet_locs(self, pid: str) -> list[str]:
        """Where a Fleet can stand in this province: its coasts, or the province itself."""
        if pid in self.coasts:
            return list(self.coasts[pid])
        return [pid] if self.fleet_adj.get(pid) else []

    # ------------------------------------------------------------------ movement reach
    def army_moves(self, pid: str, year: int) -> set[str]:
        return {q for q in self.army_adj.get(pid, ()) if self.is_open(q, year)}

    def fleet_moves(self, loc: str, year: int) -> set[str]:
        return {q for q in self.fleet_adj.get(loc, ()) if self.is_open(prov_of(q), year)}

    def fleet_dest_locs(self, loc: str, dest_prov: str, year: int) -> list[str]:
        return sorted(q for q in self.fleet_moves(loc, year) if prov_of(q) == dest_prov)

    def convoy_path_exists(self, src: str, dst: str, fleet_zones: set[str] | frozenset[str],
                           closed: set[str] | frozenset[str] = frozenset()) -> bool:
        """Army convoy from src to dst through sea zones in fleet_zones (minus closed ones)."""
        if src == dst:
            return False
        dst_p = self.provinces.get(dst)
        if dst_p is None or not dst_p.is_land:
            return False
        usable = set(fleet_zones) - set(closed)
        start = [z for z in self.sea_neighbours.get(src, ()) if z in usable]
        targets = self.sea_neighbours.get(dst, frozenset())
        seen = set(start)
        queue = deque(start)
        while queue:
            z = queue.popleft()
            if z in targets:
                return True
            for n in self.fleet_adj.get(z, ()):
                n = prov_of(n)
                if n in usable and n not in seen:
                    seen.add(n)
                    queue.append(n)
        return False

    def convoy_destinations(self, src: str, fleet_zones: set[str] | frozenset[str],
                            via_zone: str | None = None) -> set[str]:
        """Coastal land provinces an Army at src could reach by convoy through fleet_zones
        (optionally only along chains that include via_zone)."""
        usable = set(fleet_zones)
        start = [z for z in self.sea_neighbours.get(src, ()) if z in usable]
        out: set[str] = set()
        # connected components of usable seas reachable from src
        seen = set(start)
        queue = deque(start)
        while queue:
            z = queue.popleft()
            for n in self.fleet_adj.get(z, ()):
                n = prov_of(n)
                if n in usable and n not in seen:
                    seen.add(n)
                    queue.append(n)
        if via_zone is not None and via_zone not in seen:
            return set()
        for z in seen:
            for n in self.fleet_adj.get(z, ()):
                q = prov_of(n)
                if self.provinces[q].is_land and q != src:
                    out.add(q)
        if via_zone is not None:
            # every chain in a connected component can be routed through any of its members only if
            # the zone is needed; keep it simple and exact: check with via_zone forced in the path
            out = {q for q in out if self._convoy_via(src, q, usable, via_zone)}
        return out

    def _convoy_via(self, src: str, dst: str, usable: set[str], via: str) -> bool:
        """Is there a simple chain src -> ... via ... -> dst through usable seas?"""
        # path src->via and via->dst must exist; seas may be reused in classic convoys only once,
        # but any walk can be shortened to a simple path, and a walk through `via` that repeats seas
        # still proves that `via` lies on some simple path only if the two halves are disjoint.
        # Do an exact DFS (zones are few).
        targets = self.sea_neighbours.get(dst, frozenset())
        starts = [z for z in self.sea_neighbours.get(src, ()) if z in usable]

        def dfs(z: str, visited: set[str], used_via: bool) -> bool:
            used_via = used_via or z == via
            if used_via and z in targets:
                return True
            for n in self.fleet_adj.get(z, ()):
                n = prov_of(n)
                if n in usable and n not in visited:
                    visited.add(n)
                    if dfs(n, visited, used_via):
                        return True
                    visited.discard(n)
            return False

        return any(dfs(z, {z}, False) for z in starts)

    def capital_means(self, pid: str, year: int) -> dict[str, list[CapitalMeans]]:
        """Every province a Capital at pid can reach, with the ways of getting there
        (closures not applied). A land step, if available, is listed first."""
        return dict(self._capital_means(pid, year))

    @lru_cache(maxsize=None)
    def _capital_means_cached(self, pid: str, year: int) -> tuple:
        out: dict[str, list[CapitalMeans]] = {}
        for q in self.army_moves(pid, year):
            out.setdefault(q, []).append(CapitalMeans("land", frozenset()))
        p = self.provinces[pid]
        if p.is_land:
            for z in sorted(self.sea_neighbours.get(pid, ())):
                if not self.is_open(z, year) or self.provinces[z].ocean:
                    continue
                for q, qp in self.provinces.items():
                    if q == pid or not qp.is_land or not self.is_open(q, year):
                        continue
                    if z in self.sea_neighbours.get(q, ()):
                        out.setdefault(q, []).append(CapitalMeans("hop", frozenset([z])))
        for r in self.routes:
            q = r.other_end(pid)
            if q is None or (r.opens is not None and year < r.opens) or not self.is_open(q, year):
                continue
            if all(self.is_open(z, year) for z in r.path):
                out.setdefault(q, []).append(CapitalMeans("route", frozenset(r.path), r.id))
        return tuple((k, tuple(v)) for k, v in sorted(out.items()))

    def _capital_means(self, pid: str, year: int):
        for k, v in self._capital_means_cached(pid, year):
            yield k, list(v)

    def capital_moves(self, pid: str, year: int, closed: set[str] | frozenset[str] = frozenset()) -> set[str]:
        """Provinces a Capital can reach, given sea zones closed to it."""
        out = set()
        for q, means in self._capital_means(pid, year):
            if any(not (m.zones & set(closed)) for m in means):
                out.add(q)
        return out

    @lru_cache(maxsize=None)
    def influence_moves(self, pid: str, year: int) -> frozenset[str]:
        """Land provinces within 2 steps of adj.any; jumps over units and closed chokepoints,
        but not through impassable or not-yet-open provinces."""
        out = set()
        for n in self.any_adj.get(pid, ()):
            if not self.is_open(n, year):
                continue
            if self.provinces[n].is_land:
                out.add(n)
            for m in self.any_adj.get(n, ()):
                if m != pid and self.is_open(m, year) and self.provinces[m].is_land:
                    out.add(m)
        out.discard(pid)
        return frozenset(out)

    @lru_cache(maxsize=None)
    def within_two(self, pid: str, year: int) -> frozenset[str]:
        """Any province (land or sea) within 2 steps of adj.any (Disrupt range)."""
        out = set()
        for n in self.any_adj.get(pid, ()):
            if not self.is_open(n, year):
                continue
            out.add(n)
            for m in self.any_adj.get(n, ()):
                if self.is_open(m, year):
                    out.add(m)
        out.discard(pid)
        return frozenset(out)

    def moves_for(self, utype: str, loc: str, year: int) -> set[str]:
        """Provinces (fleet: locations) a unit could move to by its own adjacency/reach,
        ignoring convoys, closures and entry restrictions."""
        pid = prov_of(loc)
        if utype == "A":
            return self.army_moves(pid, year)
        if utype == "F":
            return self.fleet_moves(loc, year)
        if utype == "C":
            return self.capital_moves(pid, year)
        return set(self.influence_moves(pid, year))

    def support_reach(self, utype: str, loc: str, year: int) -> set[str]:
        """Provinces this unit could move to (for supports): no convoys, no coast detail."""
        return {prov_of(q) for q in self.moves_for(utype, loc, year)}

    def retreat_moves(self, utype: str, loc: str, year: int) -> set[str]:
        """Adjacent destinations for a retreat (no convoys, hops, routes or 2-step jumps)."""
        pid = prov_of(loc)
        if utype in ("A", "C"):
            return self.army_moves(pid, year)
        if utype == "F":
            return self.fleet_moves(loc, year)
        return {q for q in self.any_adj.get(pid, ()) if self.is_open(q, year) and self.provinces[q].is_land}

    # ------------------------------------------------------------------ distance
    def distances_from(self, pid: str) -> dict[str, int]:
        """BFS distance in adj.any (ignores opens; impassable provinces have no edges)."""
        if pid not in self._dist_cache:
            dist = {pid: 0}
            queue = deque([pid])
            while queue:
                x = queue.popleft()
                for n in self.any_adj.get(x, ()):
                    if n not in dist:
                        dist[n] = dist[x] + 1
                        queue.append(n)
            self._dist_cache[pid] = dist
        return self._dist_cache[pid]

    def distance(self, a: str, b: str) -> int | None:
        return self.distances_from(prov_of(a)).get(prov_of(b))

    # ------------------------------------------------------------------ entry restrictions
    @staticmethod
    def protected(power: str, against: str, umbrellas) -> bool:
        """Is `power`'s home protected against `against` by an umbrella from someone else?"""
        return any(prot == power and protector != against for protector, prot in umbrellas)

    def entry_block(self, power: str, utype: str, pid: str, ownership: dict, umbrellas,
                    year: int | None = None) -> str | None:
        """Reason a unit of `power`/`utype` may not enter province pid, or None if it may."""
        p = self.provinces.get(pid)
        if p is None:
            return f"unknown province {pid}"
        if p.impassable:
            return f"{pid} is impassable"
        if year is not None and p.opens is not None and year < p.opens:
            return f"{pid} does not open until {p.opens}"
        layer = layer_of(utype)
        if layer == CIVIL:
            if p.is_sea:
                return "civil units cannot enter sea zones"
            if p.home_of and p.home_of != power:
                return "civil units cannot enter another power's home"
            return None
        if p.centre == "swing" and ownership.get(pid) is None:
            return "military units cannot enter an unaligned swing state"
        if p.home_of and p.home_of != power:
            if self.nuclear(p.home_of):
                return "military units cannot enter a nuclear power's home"
            if self.protected(p.home_of, power, umbrellas):
                return "that home is protected by an umbrella"
        return None

    # ------------------------------------------------------------------ start position
    def start_units(self) -> list[Unit]:
        return [Unit(u["power"], u["type"], u["at"]) for u in self.start.get("units", [])]

    def start_ownership(self) -> dict[str, str | None]:
        own: dict[str, str | None] = {}
        for p in self.provinces.values():
            if p.centre == "home":
                own[p.id] = p.home_of
            elif p.centre == "swing":
                own[p.id] = None
        own.update(self.start.get("ownership", {}) or {})
        return own

    # ------------------------------------------------------------------ sanity
    def check(self) -> list[str]:
        """Structural problems (asymmetric adjacency, unknown ids)."""
        problems = []
        for name, table in (("army", self.army_adj), ("fleet", self.fleet_adj), ("any", self.any_adj)):
            for a, ns in table.items():
                for b in ns:
                    if a not in table.get(b, ()):
                        problems.append(f"adj.{name}: {a}->{b} not symmetric")
                    if prov_of(b) not in self.provinces:
                        problems.append(f"adj.{name}: unknown {b}")
        for r in self.routes:
            for x in list(r.ends) + list(r.path):
                if x not in self.provinces:
                    problems.append(f"route {r.id}: unknown {x}")
        return problems


__all__ = ["BrinkMap", "Province", "TradeRoute", "CapitalMeans", "MILITARY", "CIVIL"]
