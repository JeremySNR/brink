"""Assemble land provinces from Natural Earth admin-0 countries and admin-1 regions."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import shapely
from shapely.geometry import box

from .proj import Board, shift_lonlat
from .spec import Spec, SpecError

NAME_FIELDS = ("name", "name_en", "gn_name", "woe_name", "name_alt", "gns_name")


def norm(s) -> str:
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace("-", " ").replace("_", " ")
    s = re.sub(r"['`’.]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r" (prefecture|province|region|state|sheng|shi|ken|fu|to)$", "", s)
    return s


@dataclass
class LandResult:
    geoms: dict[str, object]                     # pid -> board-space geometry (before lakes)
    dropped: list = field(default_factory=list)  # board-space small islands removed by min_area
    unassigned: list = field(default_factory=list)  # (label, km2, board geometry)
    notes: list[str] = field(default_factory=list)


def _clip_box(clip, seam):
    x0, y0, x1, y1 = clip
    if x0 < seam:
        x0 += 360
    if x1 < seam:
        x1 += 360
    return box(x0, y0, x1, y1)


def assemble_land(spec: Spec, countries, admin1, board: Board) -> LandResult:
    seam = spec.seam
    domain_ll = board.domain_lonlat()
    countries = countries[countries.ADM0_A3 != "ATA"].copy()

    code_index: dict[str, list[int]] = {}
    for i, row in countries.iterrows():
        for key in (row.ADM0_A3, row.ISO_A3):
            if isinstance(key, str) and key != "-99":
                code_index.setdefault(key, [])
                if i not in code_index[key]:
                    code_index[key].append(i)

    shifted_cache: dict = {}

    def ll(kind, i, geom):
        k = (kind, i)
        if k not in shifted_cache:
            g = shapely.make_valid(geom)
            g = shift_lonlat(g, seam)
            shifted_cache[k] = g.intersection(domain_ll)
        return shifted_cache[k]

    used_countries: dict[int, str] = {}
    split_countries: dict[str, dict[int, str]] = {}
    pieces: dict[str, list] = {pid: [] for pid in spec.land}

    for pid, ls in spec.land.items():
        for code in ls.countries:
            rows = code_index.get(code)
            if not rows:
                raise SpecError(f"{pid}: country code {code} matches nothing in ne_10m_admin_0_countries")
            for i in rows:
                if i in used_countries and used_countries[i] != pid:
                    raise SpecError(f"{pid}: country {code} already assigned to {used_countries[i]}")
                used_countries[i] = pid
                pieces[pid].append(ll("c", i, countries.geometry[i]))
        if ls.admin1:
            sel = ls.admin1
            cc = sel["country"]
            regions = admin1[admin1.adm0_a3 == cc]
            if regions.empty:
                raise SpecError(f"{pid}: admin1 country {cc} has no regions")
            split_countries.setdefault(cc, {})
            chosen: list[int] = []
            if "iso" in sel:
                for code in sel["iso"]:
                    m = regions.index[regions.iso_3166_2 == code].tolist()
                    if not m:
                        raise SpecError(f"{pid}: admin1 iso {code} matches nothing in {cc}")
                    chosen += m
            if "names" in sel:
                keys = {}
                for i, row in regions.iterrows():
                    for f in NAME_FIELDS:
                        val = row.get(f)
                        for v in (val.split("|") if isinstance(val, str) else []):
                            keys.setdefault(norm(v), set()).add(i)
                for nm in sel["names"]:
                    m = keys.get(norm(nm))
                    if not m:
                        raise SpecError(f"{pid}: admin1 name {nm!r} matches nothing in {cc}")
                    chosen += sorted(m)
            if "lon_between" in sel:
                lo, hi = sel["lon_between"]
                n0 = len(chosen)
                skip = set(sel.get("exclude_iso", []))
                for i, row in regions.iterrows():
                    if row.iso_3166_2 in skip:
                        continue
                    g = ll("a", i, row.geometry)
                    if g.is_empty:
                        continue
                    cx = g.centroid.x
                    if lo <= cx < hi:
                        chosen.append(i)
                if len(chosen) == n0:
                    raise SpecError(f"{pid}: admin1 lon_between {lo}..{hi} matches nothing in {cc}")
            for i in dict.fromkeys(chosen):
                prev = split_countries[cc].get(i)
                if prev and prev != pid:
                    raise SpecError(f"{pid}: admin1 region {admin1.name[i]} already in {prev}")
                split_countries[cc][i] = pid
                pieces[pid].append(ll("a", i, admin1.geometry[i]))

    res = LandResult(geoms={})
    # the split countries' admin-0 rows count as used (their regions are tracked separately)
    for cc in split_countries:
        for i in code_index.get(cc, []):
            if i in used_countries:
                raise SpecError(f"country {cc} is used both whole and split by admin1")

    for pid, ls in spec.land.items():
        g = shapely.union_all([p for p in pieces[pid] if not p.is_empty])
        if ls.clip:
            g = g.intersection(_clip_box(ls.clip, seam))
        g = shapely.make_valid(board.project(g))
        g = _polys(g)
        keep, drop = [], []
        for part in getattr(g, "geoms", [g]):
            (keep if board.km2(part) >= ls.min_area_km2 else drop).append(part)
        if not keep:
            raise SpecError(f"{pid}: nothing left after min_area/clip")
        res.dropped += drop
        res.geoms[pid] = shapely.MultiPolygon(keep) if len(keep) > 1 else keep[0]

    # anything in Natural Earth that is not part of a province
    for i, row in countries.iterrows():
        if i in used_countries or row.ADM0_A3 in split_countries:
            continue
        g = ll("c", i, row.geometry)
        if g.is_empty:
            continue
        pg = _polys(shapely.make_valid(board.project(g)))
        res.unassigned.append((f"{row.NAME} ({row.ADM0_A3})", board.km2(pg), pg))
    for cc, assigned in split_countries.items():
        for i, row in admin1[admin1.adm0_a3 == cc].iterrows():
            if i in assigned:
                continue
            g = ll("a", i, row.geometry)
            if g.is_empty:
                continue
            pg = _polys(shapely.make_valid(board.project(g)))
            nm = row["name"] if isinstance(row["name"], str) else row.adm1_code
            res.unassigned.append((f"{nm} ({row.iso_3166_2}, admin1 of {cc})", board.km2(pg), pg))
    return res


def _polys(g):
    """Keep only polygonal parts."""
    if g.is_empty:
        return g
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    parts = [p for p in shapely.get_parts(g) if p.geom_type in ("Polygon", "MultiPolygon")]
    out = []
    for p in parts:
        out += list(getattr(p, "geoms", [p]))
    return shapely.MultiPolygon(out) if len(out) != 1 else out[0]
