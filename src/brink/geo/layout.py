"""Label / unit-slot / centre-marker placement and trade-route curves (board space)."""

from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.geometry import Point
from shapely.ops import nearest_points, polylabel

from .proj import Board

TOKEN_R = 6.5          # unit token radius (board units); the viewer uses the same number
SLOT_DX = 8.0          # slots sit at +-SLOT_DX from the block centre
SLOT_DY = 7.0
LABEL_DY = -4.5
CENTRE_DY = -13.5


def largest_part(g):
    parts = list(getattr(g, "geoms", [g]))
    return max(parts, key=lambda p: p.area)


def label_size(km2: float) -> float:
    # ~1.0 for a mid-sized province (Iran ~1.6M km2), clamp for tiny/huge ones
    return float(round(min(1.6, max(0.7, (km2 / 1.6e6) ** 0.25)), 2))


def place(geom, is_land: bool, has_centre: bool, hint=None):
    """Return dict(label, slots, centre, pole) choosing a block position with the best clearance."""
    main = largest_part(geom)
    if hint is not None:
        hp = Point(hint)
        main = min(getattr(geom, "geoms", [geom]), key=lambda p: p.distance(hp))
    pole = polylabel(main, tolerance=0.3)
    minx, miny, maxx, maxy = main.bounds
    w, h = maxx - minx, maxy - miny
    step = max(0.6, min(w, h) / 60, max(w, h) / 140)
    xs = np.arange(minx, maxx + step, step)
    ys = np.arange(miny, maxy + step, step)
    X, Y = np.meshgrid(xs, ys)
    X, Y = X.ravel(), Y.ravel()
    inside = shapely.contains_xy(main, X, Y)
    X, Y = X[inside], Y[inside]
    if len(X) == 0:
        X, Y = np.array([pole.x]), np.array([pole.y])
    else:
        X = np.append(X, pole.x)
        Y = np.append(Y, pole.y)

    if is_land:
        elems = [(-SLOT_DX, SLOT_DY, TOKEN_R + 1.2), (SLOT_DX, SLOT_DY, TOKEN_R + 1.2), (0, LABEL_DY, 3.0),
                 (-7, LABEL_DY, 2.0), (7, LABEL_DY, 2.0)]
        if has_centre:
            elems.append((0, CENTRE_DY, 3.5))
    else:
        elems = [(0, SLOT_DY + 1, TOKEN_R + 1.5), (0, LABEL_DY, 3.0), (-12, LABEL_DY, 2.0), (12, LABEL_DY, 2.0)]

    bnd = main.boundary
    score = np.full(len(X), np.inf)
    for dx, dy, req in elems:
        px, py = X + dx, Y + dy
        pts = shapely.points(px, py)
        d = shapely.distance(bnd, pts)
        ins = shapely.contains_xy(main, px, py)
        d = np.where(ins, d, -d)
        score = np.minimum(score, d - req)
    # prefer good clearance, then closeness to the pole of inaccessibility
    dist = np.hypot(X - pole.x, Y - pole.y)
    if hint is not None:
        dist = np.hypot(X - hint[0], Y - hint[1])
        score = np.where(dist < 45, score, score - 100)
    capped = np.minimum(score, 4.0)
    best = int(np.lexsort((dist, -capped))[0])
    cx, cy = float(X[best]), float(Y[best])
    out = {
        "pole": [round(pole.x, 1), round(pole.y, 1)],
        "label": [round(cx, 1), round(cy + LABEL_DY, 1)],
        "fit": round(float(score[best]), 2),
    }
    if is_land:
        out["slots"] = {"military": [round(cx - SLOT_DX, 1), round(cy + SLOT_DY, 1)],
                        "civil": [round(cx + SLOT_DX, 1), round(cy + SLOT_DY, 1)]}
        out["centre"] = [round(cx, 1), round(cy + CENTRE_DY, 1)] if has_centre else None
    else:
        out["slots"] = {"military": [round(cx, 1), round(cy + SLOT_DY + 1, 1)]}
        out["centre"] = None
    return out


# ------------------------------------------------------------------ routes

def _catmull_rom(pts, alpha=0.5):
    """Centripetal Catmull-Rom through pts -> SVG cubic path string."""
    if len(pts) < 2:
        return ""
    P = [np.array(p, float) for p in pts]
    P = [P[0] + (P[0] - P[1])] + P + [P[-1] + (P[-1] - P[-2])]
    d = f"M{P[1][0]:.1f} {P[1][1]:.1f}"
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]

        def tj(a, b):
            return max(np.linalg.norm(b - a) ** alpha, 1e-6)
        t01, t12, t23 = tj(p0, p1), tj(p1, p2), tj(p2, p3)
        m1 = (p2 - p1 + t12 * ((p1 - p0) / t01 - (p2 - p0) / (t01 + t12)))
        m2 = (p2 - p1 + t12 * ((p3 - p2) / t23 - (p3 - p1) / (t12 + t23)))
        c1 = p1 + m1 / 3
        c2 = p2 - m2 / 3
        d += f"C{c1[0]:.1f} {c1[1]:.1f} {c2[0]:.1f} {c2[1]:.1f} {p2[0]:.1f} {p2[1]:.1f}"
    return d


def route_curve(route, geoms, poles, board: Board, seam_pairs):
    """Smooth curve: end coast -> pole(path0) -> border -> pole(path1) ... -> other end coast.

    Returns (d, seam_points) where d may hold two subpaths when the route crosses the seam.
    """
    ends, path = route["ends"], route["path"]
    W = board.width

    def coast_point(land_id, zone_id, toward):
        shared = geoms[land_id].boundary.intersection(geoms[zone_id].buffer(0.05))
        if shared.is_empty:
            shared = geoms[land_id].boundary
        p = nearest_points(shared, Point(toward))[0]
        return (p.x, p.y)

    def side(x):
        return "L" if x < W / 2 else "R"

    # waypoints: coast, then each zone's pole with a border point between consecutive zones
    pts = [coast_point(ends[0], path[0], poles[path[0]])]
    for i, z in enumerate(path):
        pole = poles[z]
        if i > 0:
            a = path[i - 1]
            border = _border_point(geoms[a], geoms[z], poles[a], pole)
            if border is not None:
                pts.append(border)
        pts.append(pole)
    pts.append(coast_point(ends[1], path[-1], poles[path[-1]]))

    # split where consecutive points sit on opposite edges and are far apart (wrap across the seam)
    subpaths = [[pts[0]]]
    seam_points = []
    for p, q in zip(pts, pts[1:]):
        if abs(p[0] - q[0]) > W * 0.5:
            lon_p, lat_p = board.inv(p[0], p[1])
            lon_q, lat_q = board.inv(q[0], q[1])
            lon_p, lon_q = float(lon_p), float(lon_q)
            # unwrap q relative to p
            if lon_q - lon_p > 180:
                lon_q -= 360
            elif lon_q - lon_p < -180:
                lon_q += 360
            edge_lon = board.seam if min(lon_p, lon_q) < board.seam else board.seam + 360
            t = (edge_lon - lon_p) / (lon_q - lon_p)
            lat_x = float(lat_p) + t * (float(lat_q) - float(lat_p))
            ex1 = board.fwd(edge_lon, lat_x)
            other = board.seam + 360 if edge_lon == board.seam else board.seam
            ex2 = board.fwd(other, lat_x)
            subpaths[-1].append((float(ex1[0]), float(ex1[1])))
            subpaths.append([(float(ex2[0]), float(ex2[1]))])
            seam_points.append([[round(float(ex1[0]), 1), round(float(ex1[1]), 1)],
                                [round(float(ex2[0]), 1), round(float(ex2[1]), 1)]])
        subpaths[-1].append(q)
    d = "".join(_catmull_rom(_dedupe(sp)) for sp in subpaths)
    return d, seam_points


def _dedupe(pts):
    out = [pts[0]]
    for p in pts[1:]:
        if math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > 2.0:
            out.append(p)
    if len(out) == 1:
        out.append(pts[-1])
    return out


def _border_point(ga, gb, pa, pb):
    shared = ga.boundary.intersection(gb.buffer(0.05))
    if shared.is_empty or shared.length < 0.5:
        return None
    mid = Point((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2)
    if abs(pa[0] - pb[0]) > 1000:
        return None
    p = nearest_points(shared, mid)[0]
    return (p.x, p.y)
