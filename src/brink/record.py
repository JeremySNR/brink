"""Checks a game record against the documented schema (docs/ARCHITECTURE.md, "Game record")."""
from __future__ import annotations

RESULTS = {"ok", "bounce", "cut", "void", "dislodged", "failed"}
KINDS = {"movement", "retreat", "winter"}
SEASONS = {"spring", "fall", "winter"}
END_KINDS = {"time", "primacy", "collapse"}


def _unit_ok(u) -> bool:
    return (isinstance(u, dict) and isinstance(u.get("power"), str) and u.get("type") in ("A", "F", "C", "I")
            and isinstance(u.get("at"), str))


def validate_record(rec: dict) -> list[str]:
    """Return a list of schema problems (empty if the record is valid)."""
    errs: list[str] = []
    for key, typ in (("map", str), ("players", dict), ("phases", list), ("result", dict)):
        if not isinstance(rec.get(key), typ):
            errs.append(f"top-level '{key}' missing or not {typ.__name__}")
    if "seed" not in rec:
        errs.append("top-level 'seed' missing")
    if errs:
        return errs
    for i, ph in enumerate(rec["phases"]):
        where = f"phases[{i}] ({ph.get('name')})"
        for key, typ in (("name", str), ("year", int), ("season", str), ("kind", str), ("units", list),
                         ("orders", list), ("ownership", dict), ("crisis", int), ("umbrellas", list),
                         ("closed", dict), ("sanctions", dict), ("commons", dict), ("dislodged", list),
                         ("events", list)):
            if not isinstance(ph.get(key), typ):
                errs.append(f"{where}: '{key}' missing or not {typ.__name__}")
        if "assembly" not in ph:
            errs.append(f"{where}: 'assembly' missing")
        if ph.get("kind") not in KINDS:
            errs.append(f"{where}: bad kind {ph.get('kind')}")
        if ph.get("season") not in SEASONS:
            errs.append(f"{where}: bad season {ph.get('season')}")
        for u in ph.get("units", []):
            if not _unit_ok(u):
                errs.append(f"{where}: bad unit {u}")
        for o in ph.get("orders", []):
            for key in ("power", "text", "unit", "kind", "to", "target", "result"):
                if key not in o:
                    errs.append(f"{where}: order missing '{key}': {o}")
            if o.get("result") not in RESULTS:
                errs.append(f"{where}: bad result {o.get('result')}")
            if o.get("unit") is not None and not (isinstance(o["unit"], dict) and {"type", "at"} <= set(o["unit"])):
                errs.append(f"{where}: bad order unit {o}")
            if o.get("target") is not None and not (isinstance(o["target"], dict) and {"type", "at"} <= set(o["target"])):
                errs.append(f"{where}: bad order target {o}")
        for d in ph.get("dislodged", []):
            if not (_unit_ok(d) and isinstance(d.get("from"), str)):
                errs.append(f"{where}: bad dislodged entry {d}")
        for pair in ph.get("umbrellas", []):
            if not (isinstance(pair, list) and len(pair) == 2):
                errs.append(f"{where}: bad umbrella {pair}")
        for z, ps in ph.get("closed", {}).items():
            if not isinstance(ps, list):
                errs.append(f"{where}: closed[{z}] not a list")
        a = ph.get("assembly")
        if a is not None:
            if not (isinstance(a, dict) and {"chair", "proposal", "votes", "passed"} <= set(a)):
                errs.append(f"{where}: bad assembly {a}")
        if any(not isinstance(e, str) for e in ph.get("events", [])):
            errs.append(f"{where}: non-string event")
    res = rec["result"]
    if res.get("kind") not in END_KINDS:
        errs.append(f"result: bad kind {res.get('kind')}")
    if "winner" not in res or not isinstance(res.get("scores"), dict):
        errs.append("result: missing winner/scores")
    return errs
