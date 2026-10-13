"""Derive the adjacency graph from board-space geometry."""

from __future__ import annotations

from dataclasses import dataclass, field

import shapely

from .proj import Board
from .spec import Spec, SpecError

MIN_BORDER_KM = 20.0      # land<->land, land<->sea
MIN_SEA_KM = 10.0         # sea<->sea
FLEET_CORNER_KM = 30.0    # fleet land<->land: the shared border must reach a common sea zone


@dataclass
class Adjacency:
    army: dict[str, set] = field(default_factory=dict)
    fleet: dict[str, set] = field(default_factory=dict)
    any: dict[str, set] = field(default_factory=dict)
    border_km: dict[tuple, float] = field(default_factory=dict)   # (a,b) sorted -> km
    seam_pairs: list = field(default_factory=list)               # (left_id, right_id, y0, y1)
    notes: list[str] = field(default_factory=list)

    def add(self, kind, a, b):
        getattr(self, kind).setdefault(a, set()).add(b)
        getattr(self, kind).setdefault(b, set()).add(a)

    def remove(self, kind, a, b):
        getattr(self, kind).get(a, set()).discard(b)
        getattr(self, kind).get(b, set()).discard(a)


def _shared(a, b, eps=0.03):
    """The part of a's boundary that runs along b's boundary (tolerant to tiny vertex noise)."""
    x0, y0, x1, y1 = a.bounds
    bb = shapely.clip_by_rect(b.boundary, x0 - 1, y0 - 1, x1 + 1, y1 + 1)
    if bb.is_empty:
        return shapely.LineString()
    ab = shapely.clip_by_rect(a.boundary, *bb.bounds)
    if ab.is_empty:
        return shapely.LineString()
    return ab.intersection(bb.buffer(eps, quad_segs=2))


def _edge_intervals(g, edge, eps=0.05):
    """y-intervals along which a geometry's boundary runs on a seam edge."""
    inter = g.boundary.intersection(edge.buffer(eps, quad_segs=2))
    out = []
    for part in shapely.get_parts(inter):
        if part.is_empty or part.length < 0.2:
            continue
        _, y0, _, y1 = part.bounds
        out.append((y0, y1))
    return out


def derive(spec: Spec, board: Board, geoms: dict[str, object]) -> Adjacency:
    adj = Adjacency()
    ids = list(geoms)
    land = {p for p in ids if p in spec.land}
    sea = {p for p in ids if p in spec.seas}
    impass = {p for p in land if spec.land[p].group == "impassable"}
    for p in ids:
        adj.army.setdefault(p, set())
        adj.fleet.setdefault(p, set())
        adj.any.setdefault(p, set())

    arr = [geoms[p] for p in ids]
    tree = shapely.STRtree(arr)
    shared_geom: dict[tuple, object] = {}
    left, right = board.edge("left"), board.edge("right")

    pairs = tree.query(arr, predicate="dwithin", distance=0.1)
    for i, j in zip(*pairs):
        if i >= j:
            continue
        a, b = ids[i], ids[j]
        line = _shared(geoms[a], geoms[b])
        km = board.length_km(line) if line.length > 0 else 0.0
        if km <= 0:
            continue
        key = tuple(sorted((a, b)))
        adj.border_km[key] = km
        shared_geom[key] = line

    # across the seam: same y-range on the left edge and the right edge
    li = {p: _edge_intervals(geoms[p], left) for p in ids}
    ri = {p: _edge_intervals(geoms[p], right) for p in ids}
    for a, ivs_a in li.items():
        for b, ivs_b in ri.items():
            ov = 0.0
            span = None
            for (a0, a1) in ivs_a:
                for (b0, b1) in ivs_b:
                    lo, hi = max(a0, b0), min(a1, b1)
                    if hi - lo > 0:
                        ov += hi - lo
                        span = (lo, hi) if span is None else (min(span[0], lo), max(span[1], hi))
            if ov <= 0:
                continue
            # km along the edge (y spacing of latitude)
            _, lat0 = board.inv(left.interpolate(0).x, span[0])
            _, lat1 = board.inv(left.interpolate(0).x, span[1])
            km = abs(float(lat1) - float(lat0)) * 111.2
            adj.seam_pairs.append((a, b, span[0], span[1], km))
            if a == b:
                continue
            key = tuple(sorted((a, b)))
            adj.border_km[key] = adj.border_km.get(key, 0.0) + km

    for (a, b), km in adj.border_km.items():
        if a in impass or b in impass:
            continue
        if a in land and b in land:
            if km > MIN_BORDER_KM:
                adj.add("army", a, b)
        elif a in sea and b in sea:
            if km > MIN_SEA_KM:
                adj.add("fleet", a, b)
        else:
            if km > MIN_BORDER_KM:
                adj.add("fleet", a, b)

    # removals first, so a removed coast doesn't create fleet land<->land links
    for item in spec.overrides_remove:
        a, b, kind = item
        for p in (a, b):
            if p not in geoms:
                raise SpecError(f"adjacency override uses unknown id {p}")
        kinds = ["army", "fleet"] if kind == "any" else [kind]
        for k in kinds:
            adj.remove(k, a, b)

    # fleet land<->land: shared border that reaches a sea zone both of them border
    for a in sorted(land - impass):
        for b in sorted(adj.army.get(a, ())):
            if b <= a:
                continue
            common = (adj.fleet[a] & adj.fleet[b]) & sea
            line = shared_geom.get(tuple(sorted((a, b))))
            ok = False
            for s in common:
                if line is not None and line.distance(geoms[s]) * board.km_per_unit < FLEET_CORNER_KM:
                    ok = True
                    break
            if ok:
                adj.add("fleet", a, b)
            elif common:
                adj.notes.append(f"{a}-{b}: share a border and both border {sorted(common)} "
                                 f"but the border does not reach that coast -> no fleet link")

    # overrides
    for item in spec.overrides_add:
        a, b, kind = item
        for p in (a, b):
            if p not in geoms:
                raise SpecError(f"adjacency override uses unknown id {p}")
        kinds = ["army", "fleet"] if kind == "any" else [kind]
        for k in kinds:
            if b in getattr(adj, k).get(a, ()):
                adj.notes.append(f"override add {a}-{b} {k}: already adjacent from geometry")
            adj.add(k, a, b)

    for k in ("army", "fleet"):
        for a, bs in getattr(adj, k).items():
            for b in bs:
                adj.add("any", a, b)
    return adj
