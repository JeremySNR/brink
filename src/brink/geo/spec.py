"""Parse data/provinces.yaml into plain objects the pipeline uses."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class SpecError(Exception):
    pass


@dataclass
class LandSpec:
    id: str
    name: str
    group: str                      # home | swing | filler | impassable
    owner: str | None = None
    countries: list[str] = field(default_factory=list)
    admin1: dict | None = None
    clip: list[float] | None = None
    min_area_km2: float = 2500.0
    opens: int | None = None
    label_at: tuple[float, float] | None = None

    @property
    def centre(self) -> str:
        return {"home": "home", "swing": "swing"}.get(self.group, "none")


@dataclass
class SeaSpec:
    id: str
    name: str
    seeds: list[tuple[float, float]] = field(default_factory=list)
    chokepoint: bool = False
    point: tuple[float, float] | None = None
    radius_km: float | None = None
    bounds: list[float] | None = None
    opens: int | None = None
    label_at: tuple[float, float] | None = None
    ocean: bool = False


@dataclass
class Spec:
    raw: dict
    seam: float
    lat_range: tuple[float, float]
    start_year: int
    powers: dict
    land: dict[str, LandSpec]
    seas: dict[str, SeaSpec]
    overrides_add: list
    overrides_remove: list
    routes: list[dict]
    start: dict

    @property
    def ids(self):
        return list(self.land) + list(self.seas)


def load_spec(path: Path) -> Spec:
    raw = yaml.safe_load(Path(path).read_text())
    b = raw["board"]
    land: dict[str, LandSpec] = {}
    seas: dict[str, SeaSpec] = {}

    def add_land(pid, d, group):
        if pid in land or pid in seas:
            raise SpecError(f"duplicate province id {pid}")
        land[pid] = LandSpec(
            id=pid, name=d["name"], group=group, owner=d.get("owner"),
            countries=list(d.get("countries") or []), admin1=d.get("admin1"), clip=d.get("clip"),
            min_area_km2=float(d.get("min_area_km2", 2500)), opens=d.get("opens"),
            label_at=tuple(d["label_at"]) if d.get("label_at") else None)
        if not land[pid].countries and not land[pid].admin1:
            raise SpecError(f"{pid}: no countries or admin1 selector")

    for group, key in (("home", "homes"), ("swing", "swing"), ("filler", "filler"), ("impassable", "impassable")):
        for pid, d in (raw.get(key) or {}).items():
            add_land(pid, d, group)
    for pid, d in (raw.get("seas") or {}).items():
        if pid in land or pid in seas:
            raise SpecError(f"duplicate province id {pid}")
        seas[pid] = SeaSpec(id=pid, name=d["name"], seeds=[tuple(s) for s in d["seeds"]], opens=d.get("opens"),
                           ocean=bool(d.get("ocean", False)),
                           label_at=tuple(d["label_at"]) if d.get("label_at") else None)
    for pid, d in (raw.get("chokepoints") or {}).items():
        if pid in land or pid in seas:
            raise SpecError(f"duplicate province id {pid}")
        seas[pid] = SeaSpec(
            id=pid, name=d["name"], chokepoint=True,
            seeds=[tuple(s) for s in d.get("seeds") or []],
            point=tuple(d["point"]) if d.get("point") else None,
            radius_km=d.get("radius_km"), bounds=d.get("bounds"), opens=d.get("opens"),
            label_at=tuple(d["label_at"]) if d.get("label_at") else None)
        s = seas[pid]
        if not ((s.point and s.radius_km) or (s.seeds and s.bounds)):
            raise SpecError(f"chokepoint {pid} needs point+radius_km or seeds+bounds")

    for pid, ls in land.items():
        if ls.group == "home" and ls.owner not in raw["powers"]:
            raise SpecError(f"{pid}: unknown owner {ls.owner}")

    ov = raw.get("adjacency_overrides") or {}
    return Spec(
        raw=raw, seam=float(b["seam_lon"]), lat_range=tuple(b["lat_range"]), start_year=int(b["start_year"]),
        powers=raw["powers"], land=land, seas=seas,
        overrides_add=ov.get("add") or [], overrides_remove=ov.get("remove") or [],
        routes=raw.get("trade_routes") or [], start=raw.get("start") or {})
