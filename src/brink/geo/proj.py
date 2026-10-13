"""Coordinate handling.

Two spaces are used:

* **lon/lat (shifted)**: degrees, longitudes shifted into ``[seam, seam + 360)``.
* **board**: Equal Earth (central meridian seam + 180) scaled to SVG user units, width 2000,
  y down, top edge = ``lat_range[1]``. All geometry work after assembly happens here.
"""

from __future__ import annotations

import numpy as np
import shapely
from pyproj import Geod, Transformer
from shapely.geometry import box

GEOD = Geod(ellps="WGS84")


class Board:
    """Equal Earth projection of the shifted domain onto the SVG board."""

    def __init__(self, seam: float, lat_range: tuple[float, float], width: float = 2000.0):
        self.seam = seam
        self.lon0 = seam + 180.0
        self.lat0, self.lat1 = lat_range
        self._fwd = Transformer.from_crs(
            "+proj=longlat +datum=WGS84 +no_defs", "+proj=eqearth +lon_0=0 +datum=WGS84 +over +no_defs",
            always_xy=True)
        self._inv = Transformer.from_crs(
            "+proj=eqearth +lon_0=0 +datum=WGS84 +over +no_defs", "+proj=longlat +datum=WGS84 +no_defs",
            always_xy=True)
        self.xmax, _ = self._fwd.transform(180.0, 0.0)
        _, self.ytop = self._fwd.transform(0.0, self.lat1)
        _, ybot = self._fwd.transform(0.0, self.lat0)
        self.s = width / (2 * self.xmax)          # board units per metre
        self.width = width
        self.height = (self.ytop - ybot) * self.s
        self.km_per_unit = 1 / self.s / 1000      # nominal (exact for areas via km2())

    # -- points
    def fwd(self, lon, lat):
        rel = np.asarray(lon, dtype=float) - self.lon0
        X, Y = self._fwd.transform(rel, np.asarray(lat, dtype=float))
        return (np.asarray(X) + self.xmax) * self.s, (self.ytop - np.asarray(Y)) * self.s

    def inv(self, x, y):
        X = np.asarray(x, dtype=float) / self.s - self.xmax
        Y = self.ytop - np.asarray(y, dtype=float) / self.s
        rel, lat = self._inv.transform(X, Y)
        return np.asarray(rel) + self.lon0, np.asarray(lat)

    # -- geometries
    def project(self, g, densify_deg: float = 0.25):
        if g is None or g.is_empty:
            return g
        g = shapely.segmentize(g, densify_deg)
        return shapely.transform(g, lambda c: np.column_stack(self.fwd(c[:, 0], c[:, 1])))

    def unproject(self, g):
        return shapely.transform(g, lambda c: np.column_stack(self.inv(c[:, 0], c[:, 1])))

    def km2(self, g) -> float:
        """Area in km2 of a board-space geometry (Equal Earth is equal-area)."""
        return g.area / (self.s ** 2) / 1e6

    def length_km(self, g) -> float:
        """Geodesic length in km of a board-space (multi)line."""
        if g is None or g.is_empty:
            return 0.0
        return GEOD.geometry_length(self.unproject(g)) / 1000

    def domain_lonlat(self):
        return box(self.seam, self.lat0, self.seam + 360, self.lat1)

    def domain(self):
        return shapely.make_valid(self.project(self.domain_lonlat(), 0.1))

    def edge(self, side: str):
        """The seam edge as a board-space line (left = lon seam, right = seam+360)."""
        lon = self.seam if side == "left" else self.seam + 360
        lats = np.linspace(self.lat0, self.lat1, 600)
        x, y = self.fwd(np.full_like(lats, lon), lats)
        return shapely.LineString(np.column_stack([x, y]))


def shift_lonlat(g, seam: float):
    """Move everything west of the seam by +360 so the domain is [seam, seam+360)."""
    if g is None or g.is_empty:
        return g
    minx, _, maxx, _ = g.bounds
    if minx >= seam:
        return g
    west = g.intersection(box(-180.5, -90, seam, 90))
    east = g.intersection(box(seam, -90, 180.5, 90))
    west = shapely.transform(west, lambda c: c + np.array([360.0, 0.0]))
    out = shapely.union_all([p for p in (west, east) if not p.is_empty], grid_size=None)
    return shapely.make_valid(out)


def geodesic_circle(lon: float, lat: float, radius_km: float, n: int = 128):
    az = np.linspace(0, 360, n, endpoint=False)
    lons, lats, _ = GEOD.fwd(np.full(n, lon), np.full(n, lat), az, np.full(n, radius_km * 1000))
    lons = np.asarray(lons)
    # keep the ring continuous in longitude (it may cross +-180 or the seam)
    lons = np.where(lons - lon > 180, lons - 360, lons)
    lons = np.where(lons - lon < -180, lons + 360, lons)
    return shapely.Polygon(np.column_stack([lons, lats]))


def wrap_lonlat_shape(g, seam: float):
    """For a shape given in continuous longitudes possibly outside [seam, seam+360): fold it in."""
    pieces = []
    for k in (-1, 0, 1):
        lo = seam + 360 * k
        part = g.intersection(box(lo, -90, lo + 360, 90))
        if not part.is_empty:
            pieces.append(shapely.transform(part, lambda c, k=k: c - np.array([360.0 * k, 0.0])))
    return shapely.union_all(pieces)


def fmt(v: float) -> str:
    s = f"{v:.1f}"
    if s.endswith(".0"):
        s = s[:-2]
    if s == "-0":
        s = "0"
    return s


def to_svg_path(g) -> str:
    """Polygon/MultiPolygon/LineString -> compact SVG path string (1 decimal)."""
    out: list[str] = []

    def ring(coords, close=True):
        pts = [(fmt(x), fmt(y)) for x, y in coords]
        dedup = [pts[0]]
        for p in pts[1:]:
            if p != dedup[-1]:
                dedup.append(p)
        if close and len(dedup) > 1 and dedup[-1] == dedup[0]:
            dedup = dedup[:-1]
        if len(dedup) < (3 if close else 2):
            return
        s = "M" + dedup[0][0] + " " + dedup[0][1]
        for p in dedup[1:]:
            s += "L" + p[0] + " " + p[1]
        out.append(s + ("Z" if close else ""))

    def walk(geom):
        t = geom.geom_type
        if t == "Polygon":
            ring(geom.exterior.coords)
            for i in geom.interiors:
                ring(i.coords)
        elif t in ("MultiPolygon", "GeometryCollection", "MultiLineString"):
            for gg in geom.geoms:
                walk(gg)
        elif t in ("LineString", "LinearRing"):
            ring(geom.coords, close=False)

    if g is not None and not g.is_empty:
        walk(g)
    return "".join(out)
