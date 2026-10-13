"""Batch simulation: many bot games with randomly assigned alliances, summarised per power.

A Diplomat stands in for negotiation. Before each game it deals the seven powers into 0-3 alliances
of two or three; at the start of every later year each alliance pair breaks with probability
`betrayal` and, rarely, two unallied powers form a new pair. With `balance` on it also plays
realpolitik: when one power pulls two or more centres clear of the next, alliances with the leader
tend to break and the chasing pack tends to team up against it. Bots read their current allies from it.
Temperaments are drawn per power per game, so results average over play styles, not one script.
"""
from __future__ import annotations

import json
import random
import statistics
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .bots.search import SearchBot
from .bots.strategist import StrategistBot, Temperament
from .game import Game, Rules
from .map import BrinkMap


class Diplomat:
    def __init__(self, powers: list[str], rng: random.Random, betrayal: float = 0.12, form: float = 0.08,
                 blocs: int | None = None, balance: bool = True):
        self.powers = powers
        self.rng = rng
        self.betrayal = betrayal
        self.form = form
        self.balance = balance
        self.pairs: set[frozenset[str]] = set()
        self.year = None
        self.log: list[str] = []
        order = powers[:]
        rng.shuffle(order)
        n_blocs = rng.randint(0, 3) if blocs is None else blocs
        for _ in range(n_blocs):
            size = rng.choice((2, 2, 3))
            if len(order) < size:
                break
            bloc, order = order[:size], order[size:]
            for i, a in enumerate(bloc):
                for b in bloc[i + 1:]:
                    self.pairs.add(frozenset((a, b)))
        self.initial = sorted("-".join(sorted(p)) for p in self.pairs)

    def _tick(self, game: Game) -> None:
        year = game.year
        if self.year is None:
            self.year = year
            return
        while self.year < year:
            self.year += 1
            active = [p for p in game.active_powers() if p in self.powers]
            centres = {p: game.centres(p) for p in active}
            ranked = sorted(active, key=lambda p: -centres[p])
            leader = ranked[0]
            threat = self.balance and len(ranked) > 1 and centres[leader] >= centres[ranked[1]] + 2
            for pair in sorted(self.pairs, key=sorted):
                chance = max(self.betrayal, 0.5) if threat and leader in pair else self.betrayal
                if not pair <= set(active) or self.rng.random() < chance:
                    self.pairs.discard(pair)
                    self.log.append(f"{self.year}: {'-'.join(sorted(pair))} breaks")
            if threat:
                pack = ranked[1:4]
                for i, a in enumerate(pack):
                    for b in pack[i + 1:]:
                        if frozenset((a, b)) not in self.pairs and self.rng.random() < 0.35:
                            self.pairs.add(frozenset((a, b)))
                            self.log.append(f"{self.year}: {a}-{b} unite against {leader}")
            if self.rng.random() < self.form:
                a, b = self.rng.sample(active, 2)
                if frozenset((a, b)) not in self.pairs:
                    self.pairs.add(frozenset((a, b)))
                    self.log.append(f"{self.year}: {a}-{b} forms")

    def allies(self, game: Game, power: str) -> set[str]:
        self._tick(game)
        return {next(iter(p - {power})) for p in self.pairs if power in p}


def draw_temperament(rng: random.Random, civic: tuple[float, float] = (2.0, 2.0)) -> Temperament:
    return Temperament(civic=rng.betavariate(*civic), aggression=rng.betavariate(2, 2),
                       military=rng.uniform(0.25, 0.5))


@dataclass
class SimConfig:
    map_path: str = "build/map.json"
    games: int = 200
    seed: int = 1
    workers: int = 0
    betrayal: float = 0.12
    balance: bool = True
    bot: str = "strategist"          # strategist | search
    civic: tuple[float, float] = (2.0, 2.0)   # Beta(a, b) for each bot's share of the Crisis burden
    rules: dict = field(default_factory=dict)
    record_every: int = 0          # write every Nth game record to out/games/
    out: str | None = None


def play_one(args) -> dict:
    cfg, i = args
    gmap = _load_map(cfg.map_path)
    rng = random.Random(cfg.seed * 1_000_003 + i)
    powers = gmap.power_order
    diplomat = Diplomat(powers, rng, betrayal=cfg.betrayal, balance=cfg.balance)
    temps = {p: draw_temperament(rng, cfg.civic) for p in powers}
    board: dict = {}
    bot = {"strategist": StrategistBot, "search": SearchBot}[cfg.bot]
    players = {p: bot(seed=rng.randrange(1 << 30), temperament=temps[p], allies=diplomat.allies, coalition=board)
               for p in powers}
    game = Game(gmap, players, seed=i, rules=Rules(**cfg.rules))
    record = game.run()
    res = record["result"]
    swing_owner = {s: game.ownership.get(s) for s in gmap.swing_states()}
    centres_by_year = defaultdict(dict)
    for ph in record["phases"]:
        if ph["kind"] == "winter":
            for p in powers:
                centres_by_year[ph["year"]][p] = sum(1 for o in ph["ownership"].values() if o == p)
    out = {
        "i": i, "kind": res["kind"], "winner": res.get("winner"), "scores": res["scores"],
        "year": game.year, "crisis": game.crisis,
        "centres": {p: game.centres(p) for p in powers},
        "swing": {p: game.swing_count(p) for p in powers},
        "swing_owner": swing_owner,
        "eliminated": sorted(game.eliminated),
        "alliances": diplomat.initial, "alliance_log": diplomat.log,
        "temperaments": {p: asdict(t) for p, t in temps.items()},
        "centres_2035": centres_by_year.get(2034, {}),   # state after Winter 2034 = start of 2035
        "commons": _commons_totals(record),
    }
    if cfg.out and cfg.record_every and i % cfg.record_every == 0:
        path = Path(cfg.out) / "games" / f"game-{i:05d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        record["players"] = {p: f"{cfg.bot} (civic {temps[p].civic:.2f}, aggression {temps[p].aggression:.2f})"
                             for p in powers}
        record["alliances"] = {"initial": diplomat.initial, "log": diplomat.log}
        path.write_text(json.dumps(record))
    return out


def _commons_totals(record: dict) -> dict[str, int]:
    total: Counter = Counter()
    for ph in record["phases"]:
        if ph["kind"] == "movement":
            for o in ph["orders"]:
                if o.get("kind") == "commons" and o.get("result") == "ok":
                    total[o["power"]] += 1
    return dict(total)


_MAPS: dict[str, BrinkMap] = {}


def _load_map(path: str) -> BrinkMap:
    if path not in _MAPS:
        _MAPS[path] = BrinkMap.load(path)
    return _MAPS[path]


def run(cfg: SimConfig) -> dict:
    t0 = time.time()
    jobs = [(cfg, i) for i in range(cfg.games)]
    if cfg.workers == 1:
        games = [play_one(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=cfg.workers or None) as ex:
            games = list(ex.map(play_one, jobs, chunksize=4))
    summary = summarise(games, _load_map(cfg.map_path))
    summary["config"] = asdict(cfg)
    summary["seconds"] = round(time.time() - t0, 1)
    if cfg.out:
        out = Path(cfg.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "games.jsonl").write_text("".join(json.dumps(g) + "\n" for g in games))
        (out / "summary.json").write_text(json.dumps(summary, indent=2))
        (out / "report.md").write_text(report(summary))
    return summary


def summarise(games: list[dict], gmap: BrinkMap) -> dict:
    powers = gmap.power_order
    n = len(games)
    endings = Counter(g["kind"] for g in games)
    per_power = {}
    for p in powers:
        allied = [g for g in games if any(p in a.split("-") for a in g["alliances"])]
        alone = [g for g in games if g not in allied]
        per_power[p] = {
            "primacy_rate": sum(1 for g in games if g["winner"] == p) / n,
            "mean_score": statistics.mean(g["scores"].get(p, 0) for g in games),
            "mean_score_time_endings": _mean(g["scores"].get(p, 0) for g in games if g["kind"] == "time"),
            "mean_centres_2035": _mean(g["centres_2035"].get(p) for g in games if g["centres_2035"]),
            "mean_centres_end": statistics.mean(g["centres"][p] for g in games),
            "mean_swing_end": statistics.mean(g["swing"][p] for g in games),
            "elimination_rate": sum(1 for g in games if p in g["eliminated"]) / n,
            "mean_score_allied": _mean(g["scores"].get(p, 0) for g in allied),
            "mean_score_alone": _mean(g["scores"].get(p, 0) for g in alone),
            "mean_commons": statistics.mean(g["commons"].get(p, 0) for g in games),
        }
    holders: dict[str, Counter] = {s: Counter() for s in gmap.swing_states()}
    for g in games:
        for s, o in g["swing_owner"].items():
            holders[s][o or "none"] += 1
    return {
        "games": n,
        "endings": {k: v / n for k, v in endings.items()},
        "mean_final_crisis": statistics.mean(g["crisis"] for g in games),
        "collapse_years": dict(Counter(g["year"] for g in games if g["kind"] == "collapse")),
        "powers": per_power,
        "swing_holders": {s: {k: v / n for k, v in c.most_common()} for s, c in holders.items()},
    }


def _mean(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 2) if xs else None


def report(s: dict) -> str:
    lines = [f"# Simulation report: {s['games']} games", ""]
    e = s["endings"]
    lines.append("Endings: " + ", ".join(f"{k} {v:.0%}" for k, v in sorted(e.items())) +
                 f". Mean final Crisis {s['mean_final_crisis']:.1f}.")
    if s["collapse_years"]:
        lines.append("Collapse years: " + ", ".join(f"{y}: {c}" for y, c in sorted(s["collapse_years"].items())))
    lines += ["", "| Power | Primacy | Mean score | Score (time endings) | Centres 2035 | Centres end | Swing end "
              "| Eliminated | Score allied / alone | Commons orders |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for p, d in s["powers"].items():
        fmt = lambda x: "-" if x is None else f"{x:.1f}"
        lines.append(f"| {p} | {d['primacy_rate']:.1%} | {d['mean_score']:.1f} | {fmt(d['mean_score_time_endings'])} "
                     f"| {fmt(d['mean_centres_2035'])} | {d['mean_centres_end']:.1f} | {d['mean_swing_end']:.1f} "
                     f"| {d['elimination_rate']:.1%} | {fmt(d['mean_score_allied'])} / {fmt(d['mean_score_alone'])} "
                     f"| {d['mean_commons']:.1f} |")
    lines += ["", "## Who ends up holding each swing state", "", "| State | Most often | Share |", "| --- | --- | --- |"]
    for st, dist in s["swing_holders"].items():
        top = list(dist.items())[:3]
        lines.append(f"| {st} | " + ", ".join(k for k, _ in top) + " | " + ", ".join(f"{v:.0%}" for _, v in top) + " |")
    return "\n".join(lines) + "\n"
