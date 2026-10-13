"""Short hops cross marginal seas only; an ocean needs a trade route."""
import json
from pathlib import Path

from brink.map import BrinkMap

FIXTURE = Path(__file__).parent / "fixtures" / "mini_map.json"


def test_no_short_hop_across_an_ocean():
    data = json.loads(FIXTURE.read_text())
    assert "OSA" in BrinkMap(data).capital_moves("MEX", 2031)   # hop across the Pacific
    data["provinces"]["PAC"]["ocean"] = True
    m = BrinkMap(data)
    assert "OSA" not in m.capital_moves("MEX", 2031)
    assert "TEX" in m.capital_moves("MEX", 2031)               # land step unaffected


def test_influence_cannot_jump_an_ocean():
    data = json.loads(FIXTURE.read_text())
    before = BrinkMap(data).influence_moves("MEX", 2031)
    data["provinces"]["PAC"]["ocean"] = True
    after = BrinkMap(data).influence_moves("MEX", 2031)
    assert "OSA" in before and "OSA" not in after
    assert "PAC" in BrinkMap(data).within_two("MEX", 2031)     # the ocean itself is still in Disrupt range
