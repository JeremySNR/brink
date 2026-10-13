"""brink command line. Subcommands: play, validate-orders, sim, analyse."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_MAP = "build/map.json"


def _load_map(path: str):
    from .map import BrinkMap
    return BrinkMap.load(path)


def cmd_play(args) -> int:
    from .bots.random_bot import RandomBot
    from .bots.strategist import StrategistBot
    from .game import Game, Rules
    gmap = _load_map(args.map)
    bot = {"random": RandomBot, "strategist": StrategistBot}[args.bot]
    players = {p: bot(seed=(args.seed * 1000 + i) if args.seed is not None else None)
               for i, p in enumerate(gmap.power_order)}
    rules = Rules()
    if args.last_year:
        rules.last_year = args.last_year
    game = Game(gmap, players, seed=args.seed, rules=rules, map_name=args.map_name or Path(args.map).stem)
    rec = game.run()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, indent=None if args.compact else 1))
    r = rec["result"]
    print(f"{r['kind']} in {r['year']}; winner {r['winner']}; "
          + ", ".join(f"{p} {s:.1f}" for p, s in sorted(r["scores"].items(), key=lambda kv: -kv[1]))
          + f"  -> {out}")
    return 0


def cmd_validate_orders(args) -> int:
    """Validate order text against the start position (or a phase of a game record)."""
    from .orders import OrderError, Position, parse_order
    from .state import Effects, TurnContext, Unit
    gmap = _load_map(args.map)
    if args.record:
        rec = json.loads(Path(args.record).read_text())
        ph = next(p for p in rec["phases"] if p["name"] == args.phase) if args.phase else rec["phases"][-1]
        units = [Unit(u["power"], u["type"], u["at"]) for u in ph["units"]]
        ctx = TurnContext(ph["year"], ph["ownership"], {tuple(x) for x in ph["umbrellas"]}, Effects())
    else:
        units = gmap.start_units()
        ctx = TurnContext(int(gmap.start.get("year", 2031)), gmap.start_ownership(),
                          {tuple(x) for x in gmap.start.get("umbrellas", [])}, Effects())
    pos = Position(gmap, units, ctx)
    lines = args.orders or [ln for ln in sys.stdin.read().splitlines() if ln.strip()]
    bad = 0
    for line in lines:
        power, _, text = line.partition(":")
        if not text:
            power, text = args.power, line
        power = (power or "").strip().upper()
        try:
            od = parse_order(text.strip())
        except OrderError as e:
            print(f"INVALID  {line.strip()}: {e}")
            bad += 1
            continue
        norm, why = pos.validate(power, od)
        if why:
            bad += 1
            print(f"VOID     {power}: {norm.text()}: {why}")
        else:
            print(f"OK       {power}: {norm.text()}")
    return 1 if bad else 0


def cmd_sim(args) -> int:
    from .sim import SimConfig, run
    rules = {}
    for kv in args.rule:
        k, _, v = kv.partition("=")
        rules[k] = v.lower() == "true" if v.lower() in ("true", "false") else int(v)
    cfg = SimConfig(map_path=args.map, games=args.games, seed=args.seed, workers=args.workers,
                    betrayal=args.betrayal, rules=rules, record_every=args.record_every, out=args.out)
    summary = run(cfg)
    from .sim import report
    print(report(summary))
    print(f"{summary['games']} games in {summary['seconds']}s -> {args.out}")
    return 0


def cmd_analyse(args) -> int:
    from .analysis import report
    text = report(args.map)
    if args.out:
        Path(args.out).write_text(text)
    print(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="brink", description="Brink rules engine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("play", help="play one game with random bots and write the game record")
    p.add_argument("--map", default=DEFAULT_MAP)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", default="runs/game/game.json")
    p.add_argument("--map-name", default=None)
    p.add_argument("--last-year", type=int, default=None)
    p.add_argument("--compact", action="store_true")
    p.add_argument("--bot", choices=["random", "strategist"], default="strategist")
    p.set_defaults(func=cmd_play)
    s = sub.add_parser("sim", help="run many bot games with random alliances and summarise balance")
    s.add_argument("--map", default=DEFAULT_MAP)
    s.add_argument("--games", type=int, default=200)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--workers", type=int, default=0, help="processes (0 = all cores, 1 = in-process)")
    s.add_argument("--betrayal", type=float, default=0.12, help="yearly chance each alliance pair breaks")
    s.add_argument("--rule", action="append", default=[], help="override a Rules value, e.g. primacy=10")
    s.add_argument("--record-every", type=int, default=0, help="also save every Nth game record")
    s.add_argument("--out", default="runs/sim")
    s.set_defaults(func=cmd_sim)
    a = sub.add_parser("analyse", help="static reach and exposure analysis of the map")
    a.add_argument("--map", default=DEFAULT_MAP)
    a.add_argument("--out", default=None)
    a.set_defaults(func=cmd_analyse)
    v = sub.add_parser("validate-orders", help="check orders ('USA: A TEX - MEX') against a position")
    v.add_argument("orders", nargs="*", help="orders as 'POWER: ORDER' (or read lines from stdin)")
    v.add_argument("--map", default=DEFAULT_MAP)
    v.add_argument("--power", default=None, help="power for orders without a 'POWER:' prefix")
    v.add_argument("--record", default=None, help="game.json to take the position from")
    v.add_argument("--phase", default=None, help="phase name in the record (default: last)")
    v.set_defaults(func=cmd_validate_orders)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
