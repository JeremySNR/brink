"""Build build/map.json (engine graph) and web/public/board.json (drawn board).

    python -m brink.geo.build [--quiet] [--no-png]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from collections import deque
from pathlib import Path

import numpy as np
import shapely
from shapely.ops import polylabel

from . import data
from .adjacency import derive
from .land import _polys, assemble_land
from .layout import label_size, largest_part, place, route_curve
from .proj import Board, shift_lonlat, to_svg_path
from .seas import build_seas
from .spec import Spec, SpecError, load_spec

ROOT = Path(__file__).resolve().parents[3]
SIMPLIFY_TOL = 0.55       # board units (~9 km): coverage simplification for drawing
DECOR_MIN_KM2 = 400       # unassigned / dropped islands drawn as decoration when at least this big
LAKE_MIN_KM2 = 2_500      # Natural Earth lakes subtracted from land (and drawn) when at least this big


class ValidationError(Exception):
    pass


_T0 = time.time()


def log(msg=""):
    if msg and not msg.startswith(" ") and msg.endswith("..."):
        msg = f"[{time.time() - _T0:5.0f}s] {msg}"
    print(msg, flush=True)


def build(root: Path = ROOT, quiet: bool = False, png: bool = True) -> dict:
    t0 = time.time()
    spec = load_spec(root / "data" / "provinces.yaml")
    cache = root / "build" / "cache"
    board = Board(spec.seam, spec.lat_range)
    log(f"board {board.width:.0f} x {board.height:.1f} units, 1 unit ~ {board.km_per_unit:.1f} km at the equator")

    log("loading Natural Earth ...")
    countries = data.load(cache, "countries")
    admin1 = data.load(cache, "admin1")
    lakes_ne = data.load(cache, "lakes")

    log("assembling land provinces ...")
    lr = assemble_land(spec, countries, admin1, board)

    # lakes: subtract big ones from land (they're drawn as water, never provinces)
    lake_geoms = []
    for g in lakes_ne[lakes_ne.scalerank <= 4].geometry:
        g = shift_lonlat(shapely.make_valid(g), spec.seam).intersection(board.domain_lonlat())
        if g.is_empty:
            continue
        pg = shapely.make_valid(board.project(g))
        if board.km2(pg) >= LAKE_MIN_KM2:
            lake_geoms.append(pg)
    lakes_union = shapely.union_all(lake_geoms)
    land = {}
    for pid, g in lr.geoms.items():
        g2 = shapely.make_valid(g.difference(lakes_union))
        land[pid] = _clean(g2)
    land_union = shapely.union_all(list(land.values()))

    log("building sea zones ...")
    edges = [board.edge("left"), board.edge("right")]
    zones, enclosed, sea_notes = build_seas(spec, board, land_union, edges)

    geoms = {**land, **{z: _clean(g) for z, g in zones.items()}}
    for pid in spec.ids:
        if pid not in geoms or geoms[pid].is_empty:
            raise ValidationError(f"{pid} has no geometry")

    log("deriving adjacency ...")
    adj = derive(spec, board, geoms)

    # ---------------------------------------------------------------- drawing geometry
    log("simplifying coverage ...")
    ids = list(geoms)
    lake_parts = [p for p in enclosed if board.km2(p) >= 300]
    small_water = [p for p in enclosed if board.km2(p) < 300]   # tiny enclosed holes: fold into land
    cov = [geoms[p] for p in ids] + lake_parts
    if small_water:
        # give each tiny enclosed hole to the land province around it so the coverage has no gaps
        for hole in small_water:
            best = max(land, key=lambda p: geoms[p].boundary.intersection(hole.buffer(0.05)).length)
            geoms[best] = shapely.union_all([geoms[best], hole])
        cov = [geoms[p] for p in ids] + lake_parts
    cov = [shapely.set_precision(g, 0.001) for g in cov]
    valid = shapely.coverage_is_valid(np.array(cov, dtype=object), gap_width=0.0)
    if not valid:
        log("  coverage not perfectly edge-matched; snapping")
    simp = shapely.coverage_simplify(np.array(cov, dtype=object), SIMPLIFY_TOL)
    simp = [shapely.make_valid(g) for g in simp]
    draw = {pid: _clean(simp[i]) for i, pid in enumerate(ids)}
    draw_lakes = [g for g in simp[len(ids):] if not g.is_empty]

    decor = []
    for label, km2, g in lr.unassigned:
        for part in getattr(g, "geoms", [g]):
            if board.km2(part) >= DECOR_MIN_KM2:
                decor.append(part)
    for part in lr.dropped:
        if board.km2(part) >= DECOR_MIN_KM2:
            decor.append(part)
    decor_union = shapely.union_all(decor).difference(land_union).simplify(0.4)
    decor_union = _clean(decor_union)

    # ---------------------------------------------------------------- layout
    log("placing labels, slots and routes ...")
    provinces_out = {}
    poles = {}
    fits = {}
    for pid in ids:
        g = draw[pid]
        is_land = pid in spec.land
        has_centre = is_land and spec.land[pid].centre != "none"
        sp = spec.land[pid] if is_land else spec.seas[pid]
        hint = None
        if sp.label_at:
            lon = sp.label_at[0] + (360 if sp.label_at[0] < spec.seam else 0)
            hx, hy = board.fwd(lon, sp.label_at[1])
            hint = (float(hx), float(hy))
        pl = place(g, is_land, has_centre, hint)
        poles[pid] = tuple(pl["pole"])
        fits[pid] = pl["fit"]
        main = largest_part(g)
        extra = []
        if not is_land:
            for part in getattr(g, "geoms", []):
                if not part.equals_exact(main, 0) and part.area > 0.25 * main.area and board.km2(part) > 150_000:
                    pp = polylabel(part, tolerance=0.5)
                    extra.append([round(pp.x, 1), round(pp.y, 1)])
        entry = {
            "name": spec.land[pid].name if is_land else spec.seas[pid].name,
            "kind": _group(spec, pid),
            "d": to_svg_path(g),
            "label": pl["label"],
            "slots": pl["slots"],
            "centre": pl["centre"],
            "label_size": label_size(board.km2(geoms[pid])),
            "area_km2": int(board.km2(geoms[pid])),
        }
        if extra:
            entry["labels_extra"] = extra
        if not is_land:
            entry["ocean"] = spec.seas[pid].ocean
        provinces_out[pid] = entry

    routes_out = {}
    for r in spec.routes:
        d, seam_pts = route_curve(r, draw, poles, board, adj.seam_pairs)
        routes_out[r["id"]] = {
            "d": d, "name": r["name"], "ends": r["ends"], "path": r["path"],
            "chokepoints": [z for z in r["path"] if spec.seas[z].chokepoint],
            "opens": _route_opens(spec, r),
            "seam_points": seam_pts,
        }

    wraps, wrap_marks = [], []
    seen = set()
    for a, b, y0, y1, km in adj.seam_pairs:
        if a in spec.seas or b in spec.seas:
            if (a, b) not in seen:
                wraps.append([a, b])
                seen.add((a, b))
            ym = (y0 + y1) / 2
            # x of both edges at that y (y depends only on latitude in Equal Earth)
            _, lat = board.inv(board.width / 2, ym)
            xl, _ = board.fwd(board.seam, float(lat))
            xr, _ = board.fwd(board.seam + 360, float(lat))
            wrap_marks.append({"left": a, "right": b, "y": [round(y0, 1), round(y1, 1)],
                               "at": [[round(float(xl), 1), round(ym, 1)], [round(float(xr), 1), round(ym, 1)]]})

    outline = board.domain()
    board_json = {
        "version": 1,
        "width": round(board.width, 1), "height": round(board.height, 1),
        "seam_lon": spec.seam,
        "token_r": 6.5,
        "outline": to_svg_path(outline.simplify(0.2)),
        "provinces": provinces_out,
        "lakes": to_svg_path(shapely.union_all(draw_lakes)) if draw_lakes else "",
        "decor": to_svg_path(decor_union),
        "routes": routes_out,
        "seam": {"left_x": 0, "right_x": round(board.width, 1),
                 "left": to_svg_path(board.edge("left").simplify(0.2)),
                 "right": to_svg_path(board.edge("right").simplify(0.2))},
        "wraps": wraps,
        "wrap_marks": wrap_marks,
        "graticule": _graticule(board),
    }

    # ---------------------------------------------------------------- engine graph
    map_json = _map_json(spec, adj, geoms)

    # ---------------------------------------------------------------- validation + summary
    problems, oddities = validate(spec, map_json, adj)
    summary(spec, map_json, adj, lr, sea_notes, oddities, fits, board, quiet)
    out_build = root / "build"
    out_web = root / "web" / "public"
    out_build.mkdir(parents=True, exist_ok=True)
    from .preview import render_preview, svg_to_png
    svg_path = out_build / "board-preview.svg"
    render_preview(board_json, map_json, svg_path)
    log(f"wrote {svg_path.relative_to(root)}")
    if png:
        pngp = svg_to_png(svg_path, out_build / "board-preview.png", board_json["width"], board_json["height"])
        log(f"wrote {pngp.relative_to(root)}" if pngp else
            "no PNG renderer found (Chrome/rsvg-convert/cairosvg); skipped board-preview.png")
    if problems:
        for p in problems:
            log(f"VALIDATION ERROR: {p}")
        raise ValidationError(f"{len(problems)} validation error(s); map.json/board.json NOT written "
                              f"(preview written for debugging)")

    out_web.mkdir(parents=True, exist_ok=True)
    (out_build / "map.json").write_text(json.dumps(map_json, indent=1, ensure_ascii=False) + "\n")
    bj = json.dumps(board_json, separators=(",", ":"), ensure_ascii=False)
    (out_web / "board.json").write_text(bj)
    shutil.copyfile(out_build / "map.json", out_web / "map.json")
    log(f"wrote build/map.json ({(out_build / 'map.json').stat().st_size / 1024:.0f} KB), "
        f"web/public/board.json ({len(bj) / 1024:.0f} KB), web/public/map.json")
    log(f"done in {time.time() - t0:.0f}s")
    return {"map": map_json, "board": board_json}


def _clean(g):
    if g is None or g.is_empty:
        return shapely.Polygon()
    g = shapely.make_valid(g)
    parts = [p for p in shapely.get_parts(_polys(g)) if not p.is_empty and p.area > 1e-4]
    out = []
    for p in parts:
        out += list(getattr(p, "geoms", [p]))
    if not out:
        return shapely.Polygon()
    return shapely.MultiPolygon(out) if len(out) > 1 else out[0]


def _group(spec: Spec, pid: str) -> str:
    if pid in spec.land:
        return spec.land[pid].group
    return "chokepoint" if spec.seas[pid].chokepoint else "sea"


def _route_opens(spec, r):
    years = [r.get("opens")] + [spec.seas[z].opens for z in r["path"]]
    years = [y for y in years if y]
    return max(years) if years else None


def _graticule(board: Board):
    lines = []
    for lat in range(-45, 90, 15):
        if board.lat0 < lat < board.lat1:
            g = shapely.LineString([(board.seam, lat), (board.seam + 360, lat)])
            lines.append(board.project(g, 2.0))
    for k in range(0, 360, 30):
        lon = (np.ceil(board.seam / 30) * 30) + k
        if lon >= board.seam + 360:
            continue
        g = shapely.LineString([(lon, board.lat0), (lon, board.lat1)])
        lines.append(board.project(g, 1.0))
    return to_svg_path(shapely.MultiLineString(lines).simplify(0.3))


def _map_json(spec: Spec, adj, geoms) -> dict:
    powers = {}
    for p, d in spec.powers.items():
        powers[p] = {"name": d["name"], "adjective": d.get("adjective"), "nuclear": bool(d.get("nuclear")),
                     "homes": [pid for pid, ls in spec.land.items() if ls.group == "home" and ls.owner == p]}
    provinces = {}
    for pid in spec.ids:
        if pid in spec.land:
            ls = spec.land[pid]
            provinces[pid] = {
                "name": ls.name, "kind": "land", "group": ls.group, "centre": ls.centre,
                **({"home_of": ls.owner} if ls.group == "home" else {}),
                "coastal": bool(adj.fleet.get(pid) and any(n in spec.seas for n in adj.fleet[pid])),
                "chokepoint": False, "impassable": ls.group == "impassable", "opens": ls.opens,
            }
        else:
            s = spec.seas[pid]
            provinces[pid] = {
                "name": s.name, "kind": "sea", "group": "chokepoint" if s.chokepoint else "sea", "centre": "none",
                "coastal": False, "chokepoint": s.chokepoint, "ocean": s.ocean, "impassable": False,
                "opens": s.opens,
            }
    impass = {p for p, v in provinces.items() if v["impassable"]}

    def lists(d):
        return {p: sorted(n for n in d.get(p, ()) if n not in impass) for p in spec.ids if p not in impass}

    units = []
    for power, us in (spec.start.get("units") or {}).items():
        for u in us:
            t, at = u.split()
            units.append({"power": power, "type": t, "at": at})
    return {
        "version": 1,
        "seam_lon": spec.seam,
        "powers": powers,
        "provinces": provinces,
        "adj": {"army": lists(adj.army), "fleet": lists(adj.fleet), "any": lists(adj.any)},
        "coasts": {},
        "border_km": {f"{a}-{b}": round(km, 1) for (a, b), km in sorted(adj.border_km.items())},
        "trade_routes": [{"id": r["id"], "name": r["name"], "ends": r["ends"], "path": r["path"],
                          "opens": _route_opens(spec, r)} for r in spec.routes],
        "start": {"year": spec.start.get("year", spec.start_year), "crisis": spec.start.get("crisis", 0),
                  "umbrellas": spec.start.get("umbrellas", []), "units": units},
    }


def validate(spec: Spec, m: dict, adj) -> tuple[list[str], list[str]]:
    problems, odd = [], []
    P = m["provinces"]
    A = m["adj"]
    # symmetry
    for kind, d in A.items():
        for a, bs in d.items():
            for b in bs:
                if a not in d.get(b, []):
                    problems.append(f"{kind} adjacency not symmetric: {a}-{b}")
    # connectivity ignoring impassable and not-yet-open
    nodes = [p for p, v in P.items() if not v["impassable"] and not v["opens"]]
    ns = set(nodes)
    start = "WAS" if "WAS" in ns else nodes[0]
    seen = {start}
    q = deque([start])
    while q:
        a = q.popleft()
        for b in A["any"].get(a, []):
            if b in ns and b not in seen:
                seen.add(b)
                q.append(b)
    missing = sorted(ns - seen)
    if missing:
        problems.append(f"graph not connected (from {start}); unreachable: {missing}")
    for p, v in P.items():
        if v["centre"] in ("home", "swing") and p not in seen and not v["opens"]:
            problems.append(f"centre {p} unreachable")
    # trade routes
    F = A["fleet"]
    for r in m["trade_routes"]:
        e0, e1 = r["ends"]
        path = r["path"]
        chain = [e0] + path + [e1]
        for a, b in zip(chain, chain[1:]):
            if b not in F.get(a, []):
                problems.append(f"trade route {r['id']}: {a} is not fleet-adjacent to {b}")
    # starting fleets coastal
    for u in m["start"]["units"]:
        if u["at"] not in P:
            problems.append(f"start unit at unknown province {u['at']}")
            continue
        if u["type"] == "F" and not P[u["at"]]["coastal"]:
            problems.append(f"starting fleet {u['power']} F {u['at']} is not coastal")
        if P[u["at"]]["kind"] != "land":
            problems.append(f"starting unit {u} not on land")
    # oddities
    for p, v in P.items():
        if v["kind"] == "land" and not v["impassable"] and not A["army"].get(p):
            odd.append(f"{p} ({v['name']}) has no army neighbours (island; reachable by convoy/fleet only)")
        if v["kind"] == "land" and not v["impassable"] and not v["coastal"]:
            odd.append(f"{p} ({v['name']}) is landlocked")
        if v["kind"] == "sea" and not any(P[n]["kind"] == "land" for n in A["fleet"].get(p, [])):
            odd.append(f"{p} ({v['name']}) touches no land province")
    for (a, b), km in sorted(adj.border_km.items()):
        both_land = a in spec.land and b in spec.land
        if both_land and 20 < km < 120 and not (P[a]["impassable"] or P[b]["impassable"]):
            odd.append(f"short land border {a}-{b}: {km:.0f} km (army-adjacent)")
        if both_land and 0 < km <= 20:
            odd.append(f"land border {a}-{b} ignored: only {km:.0f} km")
    for a, b, y0, y1, km in adj.seam_pairs:
        if a in spec.land or b in spec.land:
            odd.append(f"seam contact involving land: {a} (left edge) ~ {b} (right edge), {km:.0f} km")
    return problems, odd


def summary(spec, m, adj, lr, sea_notes, oddities, fits, board, quiet):
    P = m["provinces"]
    A = m["adj"]
    groups = {}
    for p, v in P.items():
        groups.setdefault(v["group"], []).append(p)
    log("")
    log("=" * 78)
    log("PROVINCES: " + ", ".join(f"{k} {len(v)}" for k, v in groups.items()) + f"  (total {len(P)})")
    nprov = sum(len(v) for v in A["army"].values()) // 2
    nfl = sum(len(v) for v in A["fleet"].values()) // 2
    nany = sum(len(v) for v in A["any"].values()) // 2
    log(f"EDGES: army {nprov}, fleet {nfl}, any {nany}")
    if not quiet:
        log("-" * 78)
        for p, v in P.items():
            if v["impassable"]:
                log(f"{p:4} {v['name']:<28} [impassable]")
                continue
            army = A["army"].get(p, [])
            fl = A["fleet"].get(p, [])
            seas = [n for n in fl if P[n]["kind"] == "sea"]
            fland = [n for n in fl if P[n]["kind"] == "land"]
            tag = v["group"] + (f"/{v['home_of']}" if v.get("home_of") else "") + (
                f" opens {v['opens']}" if v["opens"] else "")
            if v["kind"] == "land":
                log(f"{p:4} {v['name']:<28} {tag:<12} army: {' '.join(army) or '-'}")
                log(f"{'':46} seas: {' '.join(seas) or '-'}   fleet-land: {' '.join(fland) or '-'}")
            else:
                log(f"{p:4} {v['name']:<28} {tag:<12} seas: {' '.join(seas) or '-'}")
                log(f"{'':46} coasts: {' '.join(fland) or '-'}")
    log("-" * 78)
    log("NATURAL EARTH NOT ASSIGNED TO ANY PROVINCE (>= 2,500 km2; drawn as decoration):")
    tiny = 0
    for label, km2, _ in sorted(lr.unassigned, key=lambda t: -t[1]):
        if km2 >= 2500:
            log(f"  {label}: {km2:,.0f} km2")
        else:
            tiny += 1
    log(f"  (+{tiny} smaller unassigned islands/territories)")
    dropped = sum(board.km2(p) for p in lr.dropped)
    log(f"islands dropped by min_area: {len(lr.dropped)} parts, {dropped:,.0f} km2 total")
    log("-" * 78)
    log("SEA NOTES:")
    for n in sea_notes:
        log("  " + n)
    log("ADJACENCY NOTES:")
    for n in adj.notes:
        log("  " + n)
    log("SEAM CONTACTS (left edge ~ right edge):")
    for a, b, y0, y1, km in adj.seam_pairs:
        log(f"  {a} ~ {b}: {km:.0f} km")
    log("ODDITIES TO REVIEW:")
    for o in oddities:
        log("  " + o)
    cramped = [p for p, f in fits.items() if f < 0]
    if cramped:
        log("CRAMPED LABEL/SLOT BLOCKS (block doesn't fully fit inside the shape): " + ", ".join(sorted(cramped)))
    log("=" * 78)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m brink.geo.build")
    ap.add_argument("--quiet", action="store_true", help="don't print every province's neighbours")
    ap.add_argument("--no-png", action="store_true")
    a = ap.parse_args(argv)
    try:
        build(quiet=a.quiet, png=not a.no_png)
    except (SpecError, ValidationError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
