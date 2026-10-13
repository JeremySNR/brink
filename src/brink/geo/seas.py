"""Sea zones: spherical Voronoi cells of the seeds (exact great-circle bisectors), clipped to ocean."""

from __future__ import annotations

import numpy as np
import shapely
from shapely.geometry import MultiPoint, box

from .proj import Board, geodesic_circle, wrap_lonlat_shape
from .spec import Spec, SpecError


def _shift_lon(lon, seam):
    return lon + 360 if lon < seam else lon


def _unit(lon, lat):
    lo, la = np.radians(lon), np.radians(lat)
    return np.array([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])


def _clip_halfplane(poly: np.ndarray, a: float, b: float, c: float) -> np.ndarray:
    """Sutherland-Hodgman: keep a + b*u + c*v >= 0 of a convex polygon (N x 2, open ring)."""
    if len(poly) == 0:
        return poly
    out = []
    f = a + b * poly[:, 0] + c * poly[:, 1]
    n = len(poly)
    for k in range(n):
        p, q = poly[k], poly[(k + 1) % n]
        fp, fq = f[k], f[(k + 1) % n]
        if fp >= 0:
            out.append(p)
        if (fp >= 0) != (fq >= 0):
            t = fp / (fp - fq)
            out.append(p + t * (q - p))
    return np.array(out) if out else np.zeros((0, 2))


def _spherical_cell(i: int, S: np.ndarray, lon_i: float, lat_i: float, step: float = 0.004):
    """Exact spherical Voronoi cell of seed i as a lon/lat polygon (continuous longitudes).

    In the gnomonic frame centred on seed i, every bisector great circle is a straight line, so the
    cell is an intersection of half-planes. Edges are densified, then mapped back to the sphere.
    """
    c = S[i]
    lo, la = np.radians(lon_i), np.radians(lat_i)
    e1 = np.array([-np.sin(lo), np.cos(lo), 0.0])
    e2 = np.array([-np.sin(la) * np.cos(lo), -np.sin(la) * np.sin(lo), np.cos(la)])
    R = 30.0
    poly = np.array([[-R, -R], [R, -R], [R, R], [-R, R]], float)
    for j in range(len(S)):
        if j == i:
            continue
        n = c - S[j]
        if np.allclose(n, 0):
            continue
        poly = _clip_halfplane(poly, float(c @ n), float(e1 @ n), float(e2 @ n))
    ring = shapely.LineString(np.vstack([poly, poly[:1]]))
    ring = shapely.segmentize(ring, step)
    uv = np.asarray(ring.coords)[:-1]
    xyz = c[None, :] + uv[:, :1] * e1[None, :] + uv[:, 1:] * e2[None, :]
    xyz /= np.linalg.norm(xyz, axis=1)[:, None]
    lat = np.degrees(np.arcsin(np.clip(xyz[:, 2], -1, 1)))
    lon = np.degrees(np.arctan2(xyz[:, 1], xyz[:, 0]))
    # continuous longitudes along the ring
    d = np.diff(lon)
    d = (d + 180) % 360 - 180
    lon = np.concatenate([[lon_i + ((lon[0] - lon_i + 180) % 360 - 180)], np.zeros(len(d))])
    lon[1:] = lon[0] + np.cumsum(d)
    pts = list(zip(lon, lat))
    net = lon[-1] - lon[0] + (((lon[0] - lon[-1]) + 180) % 360 - 180)
    if abs(net) > 180:   # the ring winds around a pole: close it along the pole
        pole = 90.0 if c[2] > 0 else -90.0
        pts += [(lon[0] + net, lat[0]), (lon[0] + net, pole), (lon[0], pole)]
    g = shapely.make_valid(shapely.Polygon(pts))
    return g


def _voronoi_regions(seeds: list[tuple[str, float, float]], seam: float, lat_range):
    """seeds: (zone, lon, lat) -> {zone: shifted lon/lat region inside the domain} (spherical Voronoi)."""
    S = np.array([_unit(lon, lat) for _, lon, lat in seeds])
    dom = box(seam, lat_range[0] - 0.5, seam + 360, lat_range[1] + 0.5)
    by_zone: dict[str, list] = {}
    for i, (z, lon, lat) in enumerate(seeds):
        cell = _spherical_cell(i, S, lon, lat)
        folded = _fold(cell, seam).intersection(dom)
        if not folded.is_empty:
            by_zone.setdefault(z, []).append(folded)
    return {z: shapely.union_all(cs) for z, cs in by_zone.items()}


def _fold(g, seam):
    pieces = []
    for k in range(-2, 3):
        lo = seam + 360 * k
        part = g.intersection(box(lo, -90, lo + 360, 90))
        if not part.is_empty:
            pieces.append(shapely.transform(part, lambda c, k=k: c - np.array([360.0 * k, 0.0])))
    return shapely.make_valid(shapely.union_all(pieces))


def _ll_to_board(g, board: Board):
    return shapely.make_valid(board.project(g, 0.25))


def _polys(g):
    parts = [p for p in shapely.get_parts(g) if p.geom_type == "Polygon" and not p.is_empty]
    return parts


def shared_len(a, b, eps=0.02) -> float:
    """Board-unit length of the boundary a shares with b."""
    from .adjacency import _shared
    if a.distance(b) > eps:
        return 0.0
    return _shared(a, b, eps).length


def build_seas(spec: Spec, board: Board, land_union, edges) -> tuple[dict, list, list[str]]:
    """Returns ({zone: board geometry}, [lake geometries], notes)."""
    notes: list[str] = []
    seam = spec.seam
    domain = board.domain()
    water = shapely.make_valid(domain.difference(land_union))

    # which water bodies are ocean (contain a seed) vs lakes (Caspian, enclosed holes)
    seed_pts = []
    for z, s in spec.seas.items():
        for lon, lat in s.seeds:
            seed_pts.append((z, *board.fwd(_shift_lon(lon, seam), lat)))
        if s.point:
            seed_pts.append((z, *board.fwd(_shift_lon(s.point[0], seam), s.point[1])))
    comps = _polys(water)
    ocean_parts, lakes = [], []
    seedgeom = MultiPoint([(x, y) for _, x, y in seed_pts])
    for c in comps:
        if c.intersects(seedgeom):
            ocean_parts.append(c)
        else:
            lakes.append(c)
    big_lakes = sorted(((board.km2(lk), lk) for lk in lakes), key=lambda t: -t[0])
    for a, lk in big_lakes:
        if a > 20_000:
            lon, lat = board.inv(lk.representative_point().x, lk.representative_point().y)
            notes.append(f"enclosed water (lake, not a zone): {a:,.0f} km2 at {float(lon):.1f},{float(lat):.1f}")
    ocean = shapely.union_all(ocean_parts)

    sea_seeds = [(z, lon, lat) for z, s in spec.seas.items() if not s.chokepoint for lon, lat in s.seeds]
    all_seeds = sea_seeds + [(z, lon, lat) for z, s in spec.seas.items() if s.chokepoint for lon, lat in s.seeds]
    v_sea = _voronoi_regions(sea_seeds, seam, spec.lat_range)
    v_all = _voronoi_regions(all_seeds, seam, spec.lat_range) if len(all_seeds) > len(sea_seeds) else {}

    zones: dict[str, object] = {}
    assigned = None

    def take(z, region):
        nonlocal assigned
        g = ocean.intersection(region)
        if assigned is not None:
            g = g.difference(assigned)
        g = shapely.make_valid(g)
        parts = _polys(g)
        g = shapely.union_all(parts) if parts else shapely.Polygon()
        zones[z] = g
        assigned = g if assigned is None else shapely.union_all([assigned, g])

    # chokepoints first
    for z, s in spec.seas.items():
        if not s.chokepoint:
            continue
        if s.point:
            lon = _shift_lon(s.point[0], seam)
            circ = geodesic_circle(lon, s.point[1], s.radius_km)
            region_ll = wrap_lonlat_shape(circ, seam)
            region = shapely.make_valid(board.project(region_ll, 0.1))
        else:
            x0, y0, x1, y1 = s.bounds
            bx = box(_shift_lon(x0, seam), y0, _shift_lon(x1, seam), y1)
            region = _ll_to_board(v_all[z].intersection(bx), board)
        take(z, region)
        if zones[z].is_empty:
            raise SpecError(f"chokepoint {z} has no water")
    for z, s in spec.seas.items():
        if s.chokepoint:
            continue
        if z not in v_sea:
            raise SpecError(f"sea {z}: no voronoi region")
        take(z, _ll_to_board(v_sea[z], board))
        if zones[z].is_empty:
            raise SpecError(f"sea {z} has no water")

    # leftovers from numerical noise
    left = shapely.make_valid(ocean.difference(assigned))
    for p in _polys(left):
        _absorb(p, zones, board, notes, why="leftover")

    # slivers: small parts not attached to the main body (and not wrapping across the seam)
    for z in list(zones):
        s = spec.seas[z]
        parts = sorted(_polys(zones[z]), key=lambda p: -p.area)
        if len(parts) <= 1:
            continue
        keep = [parts[0]]
        for p in parts[1:]:
            km2 = board.km2(p)
            touches_edge = any(p.distance(e) < 0.05 for e in edges)
            limit = 2_000 if s.chokepoint else 25_000
            if km2 < limit and not touches_edge:
                others = {o: shared_len(p, g) for o, g in zones.items() if o != z}
                best = max(others, key=others.get) if others else None
                if best and others[best] > 0:
                    zones[best] = shapely.union_all([zones[best], p])
                    if km2 > 500:
                        notes.append(f"merged a {km2:,.0f} km2 detached piece of {z} into {best}")
                    continue
            keep.append(p)
        zones[z] = shapely.union_all(keep)
    return zones, lakes, notes


def _absorb(p, zones, board, notes, why):
    others = {o: shared_len(p, g) for o, g in zones.items()}
    best = max(others, key=others.get)
    zones[best] = shapely.union_all([zones[best], p])
    if board.km2(p) > 500:
        notes.append(f"{why}: {board.km2(p):,.0f} km2 given to {best}")
