"""Static balance analysis of the board graph: no bots, no engine.

Answers "who can reach what, how fast, and who is exposed to whom" straight from
build/map.json, so map problems show up before any simulation runs.

Movement model (start of game, chokepoints open, Arctic closed):
- Civil reach: a Capital moves one land step, one short hop across a non-ocean sea, or a whole
  trade route; an Influence unit jumps up to 2 steps of the `any` graph. A civil unit can be
  built in any home, so distances are measured from the power's homes.
- Military can't enter unaligned swing states, so the opening scramble for swing states is
  civil-only. Military exposure is measured between homes over army + fleet adjacency.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

INF = 99


@dataclass
class Board:
    powers: dict
    provinces: dict
    army: dict[str, list[str]]
    fleet: dict[str, list[str]]
    any: dict[str, list[str]]
    routes: list[dict]
    start_year: int

    @classmethod
    def load(cls, path: str | Path) -> "Board":
        data = json.loads(Path(path).read_text())
        return cls(
            powers=data["powers"],
            provinces=data["provinces"],
            army=data["adj"]["army"],
            fleet=data["adj"]["fleet"],
            any=data["adj"]["any"],
            routes=data["trade_routes"],
            start_year=data["start"]["year"],
        )

    def is_open(self, pid: str, year: int) -> bool:
        p = self.provinces[pid]
        return not p.get("impassable") and (p.get("opens") is None or p["opens"] <= year)

    def homes(self, power: str) -> list[str]:
        return self.powers[power]["homes"]

    def swing(self) -> list[str]:
        return sorted(k for k, p in self.provinces.items() if p["centre"] == "swing")

    def land(self) -> list[str]:
        return [k for k, p in self.provinces.items() if p["kind"] == "land" and not p.get("impassable")]


def capital_graph(b: Board, power: str, year: int) -> dict[str, set[str]]:
    """One Capital move = one edge. Foreign homes excluded."""
    foreign_homes = {h for pw in b.powers if pw != power for h in b.homes(pw)}
    ok = lambda p: b.provinces[p]["kind"] == "land" and b.is_open(p, year) and p not in foreign_homes
    g: dict[str, set[str]] = defaultdict(set)
    for p in b.land():
        if not ok(p):
            continue
        for q in b.army.get(p, []):
            if ok(q):
                g[p].add(q)
        if b.provinces[p].get("coastal"):
            for sea in b.any.get(p, []):
                if b.provinces[sea]["kind"] != "sea" or b.provinces[sea].get("ocean") or not b.is_open(sea, year):
                    continue
                for q in b.any.get(sea, []):
                    if q != p and ok(q) and b.provinces[q].get("coastal"):
                        g[p].add(q)
    for r in b.routes:
        if r.get("opens") and r["opens"] > year:
            continue
        a, z = r["ends"]
        if ok(a) and ok(z):
            g[a].add(z)
            g[z].add(a)
    return g


def influence_graph(b: Board, power: str, year: int) -> dict[str, set[str]]:
    foreign_homes = {h for pw in b.powers if pw != power for h in b.homes(pw)}
    g: dict[str, set[str]] = defaultdict(set)
    for p in b.land():
        if not b.is_open(p, year):
            continue
        for q, d in bfs(b.any, [p], lambda x: b.is_open(x, year), limit=2).items():
            if 0 < d and b.provinces[q]["kind"] == "land" and q not in foreign_homes:
                g[p].add(q)
    return g


def bfs(graph, sources, passable=lambda _: True, limit=INF) -> dict[str, int]:
    dist = {s: 0 for s in sources}
    queue = deque(sources)
    while queue:
        p = queue.popleft()
        if dist[p] >= limit:
            continue
        for q in graph.get(p, ()):
            if q not in dist and passable(q):
                dist[q] = dist[p] + 1
                queue.append(q)
    return dist


def swing_distances(b: Board, year: int | None = None) -> dict[str, dict[str, int]]:
    """Fewest civil moves from any of a power's homes to each swing state."""
    year = year or b.start_year
    out: dict[str, dict[str, int]] = {}
    for power in b.powers:
        cap = bfs(capital_graph(b, power, year), b.homes(power))
        inf = bfs(influence_graph(b, power, year), b.homes(power))
        out[power] = {s: min(cap.get(s, INF), inf.get(s, INF)) for s in b.swing()}
    return out


def military_exposure(b: Board) -> dict[str, dict[str, int]]:
    """Fewest military moves from each power's homes to another power's homes (army + fleet graph)."""
    graph = {p: set(b.army.get(p, [])) | set(b.fleet.get(p, [])) for p in b.provinces}
    passable = lambda p: b.is_open(p, b.start_year)
    out: dict[str, dict[str, int]] = {}
    for power in b.powers:
        dist = bfs(graph, b.homes(power), passable)
        out[power] = {
            other: min(dist.get(h, INF) for h in b.homes(other)) for other in b.powers if other != power
        }
    return out


def report(map_path: str | Path) -> str:
    b = Board.load(map_path)
    powers = list(b.powers)
    dist = swing_distances(b)
    lines = ["# Static map analysis", ""]

    lines += ["## Swing states: civil moves needed from each power's nearest home", ""]
    lines.append("| Swing state | " + " | ".join(powers) + " | Nearest |")
    lines.append("| --- |" + " --- |" * (len(powers) + 1))
    natural: dict[str, list[str]] = defaultdict(list)
    contested: list[str] = []
    for s in b.swing():
        row = [dist[p][s] for p in powers]
        best = min(row)
        winners = [p for p in powers if dist[p][s] == best]
        if len(winners) == 1:
            natural[winners[0]].append(s)
        else:
            contested.append(s)
        cells = [("**%d**" % d if d == best else str(d)) if d < INF else "-" for d in row]
        lines.append(f"| {b.provinces[s]['name']} | " + " | ".join(cells) + f" | {', '.join(winners)} |")
    lines += ["", "## Opening shares", ""]
    lines.append("| Power | Uncontested nearest swing states | Within 1 move | Within 2 moves |")
    lines.append("| --- | --- | --- | --- |")
    for p in powers:
        one = sum(1 for s in b.swing() if dist[p][s] <= 1)
        two = sum(1 for s in b.swing() if dist[p][s] <= 2)
        names = ", ".join(b.provinces[s]["name"] for s in natural[p]) or "none"
        lines.append(f"| {p} | {len(natural[p])}: {names} | {one} | {two} |")
    lines.append("")
    lines.append(f"Tied (contested at equal distance): {', '.join(b.provinces[s]['name'] for s in contested) or 'none'}")

    exp = military_exposure(b)
    lines += ["", "## Military distance between home regions (army + fleet moves)", ""]
    lines.append("| From \\ To | " + " | ".join(powers) + " |")
    lines.append("| --- |" + " --- |" * len(powers))
    for p in powers:
        cells = ["-" if q == p else str(exp[p][q]) for q in powers]
        lines.append(f"| {p} | " + " | ".join(cells) + " |")
    lines += ["", "## Exposure (other powers whose homes are within 3 military moves)", ""]
    for p in powers:
        near = [q for q in powers if q != p and exp[q][p] <= 3]
        lines.append(f"- **{p}** ({'nuclear' if b.powers[p].get('nuclear') else 'non-nuclear'}): "
                     f"{len(near)} — {', '.join(near) or 'none'}")
    return "\n".join(lines) + "\n"
