"""Natural Earth download + cache (build/cache, gitignored)."""

from __future__ import annotations

import urllib.request
import warnings
from pathlib import Path

import geopandas as gpd

NE_BASE = "https://naciscdn.org/naturalearth/10m"
LAYERS = {
    "countries": "cultural/ne_10m_admin_0_countries",
    "admin1": "cultural/ne_10m_admin_1_states_provinces",
    "lakes": "physical/ne_10m_lakes",
}


def fetch(cache: Path, layer: str) -> Path:
    rel = LAYERS[layer]
    path = cache / (rel.split("/")[-1] + ".zip")
    if not path.exists():
        cache.mkdir(parents=True, exist_ok=True)
        url = f"{NE_BASE}/{rel}.zip"
        print(f"  downloading {url}")
        tmp = path.with_suffix(".part")
        req = urllib.request.Request(url, headers={"User-Agent": "brink-geo/0.1"})
        with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        tmp.rename(path)
    return path


def load(cache: Path, layer: str) -> gpd.GeoDataFrame:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return gpd.read_file(fetch(cache, layer))
