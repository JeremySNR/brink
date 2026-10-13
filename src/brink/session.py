"""Hosted play: a game as persisted JSON state advanced one decision point at a time.

`brink serve` keeps a live `Game.run()` thread per game with a blocking HumanPlayer. That cannot work in a
stateless function (Vercel Python, Lambda...), so this module offers the same engine as pure functions over
a JSON-safe state dict (`Game.to_state()`), suitable for a database row:

    state = new_game(gmap, seed=7, humans={"USA"})
    state = run_bots_until_human(gmap, state)              # bots-only decision points, if any
    p = pending(gmap, state)                               # {"kind": "orders", "powers": [...], "options": ...}
    validate(gmap, state, "USA", ["A TEX - MEX"])          # draft checking, per order
    state = advance(gmap, state, {"USA": ["A TEX - MEX"]})  # resolves exactly one decision point
    state = run_bots_until_human(gmap, state)

Decision points (`pending()["kind"]`), each a simultaneous decision by `powers`:

    orders    Spring/Fall movement: every active power with a unit or an umbrella to withdraw
    retreats  powers with dislodged units
    propose   Winter: the Assembly chair alone ('PROPOSE SANCTIONS CHN', or nothing)
    winter    Winter, everyone at once: the vote on the proposal, builds/disbands, umbrellas (nuclear powers)

A Winter is two steps; the rules' order of operations is unchanged (alignment/drift/home capture -> Crisis
-> Assembly -> adjustments -> umbrellas -> end check): the decision-free prelude runs when the Fall ends,
the `propose` step records the proposal, and the `winter` step applies the votes first, then everyone's
adjustments, then umbrellas. Adjustment orders are submitted before the vote is known, so they are checked
against the position *after* the Assembly: only a passed RECOGNITION can change a power's adjustment
(`change_if_passed` in the options); excess builds are then void and missing disbands follow the default.

Missing decisions are civil disorder: units hold, dislodged units disband, default disbands, no proposal,
no vote (abstain), umbrellas kept.

Bots are rebuilt on every call (`make_bots`), seeded from (game seed, power, step), so the same state always
yields the same bot decisions. Their coalition board (allies posting this phase's orders) is shared within
the call, which is all they ever read. Alliances are static pairs from `state["meta"]["alliances"]`; the
sims' evolving Diplomat is not carried over.
"""
from __future__ import annotations

import json
import random
from typing import Iterable

from .bots.base import Player
from .bots.human import (_as_list, _power_info, adjust_payload, check_orders, movement_payload, normalise_proposal,
                         normalise_vote, propose_payload, retreat_payload, umbrella_payload, vote_payload)
from .game import SEASON_CODE, Game, Rules
from .map import BrinkMap
from .orders import OrderError, Umbrella, Vote, parse_order

KINDS = {"movement": "orders", "retreats": "retreats", "propose": "propose", "winter": "winter"}
BOT_KINDS = ("search", "strategist", "random", "hold")


class GameOver(Exception):
    """advance() on a finished game."""


class StaleState(Exception):
    """advance(expect_step=n) on a state that has already moved past step n."""


# ---------------------------------------------------------------------------------------------- creation
def new_game(gmap: BrinkMap, seed: int | None = None, humans: Iterable[str] = (), bot_kind: str = "search",
             rules: Rules | None = None, map_name: str = "brink-v0",
             alliances: Iterable[Iterable[str]] = ()) -> dict:
    """A fresh game state. `humans`: the powers decided from outside; everyone else is a `bot_kind` bot."""
    humans = sorted({h.upper() for h in humans})
    bad = [h for h in humans if h not in gmap.power_order]
    if bad:
        raise ValueError(f"unknown power(s) {', '.join(bad)}")
    if bot_kind not in BOT_KINDS:
        raise ValueError(f"bot_kind must be one of {', '.join(BOT_KINDS)}")
    seed = seed if seed is not None else random.randrange(1 << 30)
    g = Game(gmap, None, seed=seed, rules=rules, map_name=map_name)
    g.player_names = {p: ("human" if p in humans else bot_kind) for p in gmap.power_order}
    state = g.to_state()
    state["meta"] = {"humans": humans, "bot_kind": bot_kind,
                     "alliances": [sorted(a) for a in alliances]}
    return state


def _dump(g: Game, meta: dict | None) -> dict:
    st = g.to_state()
    st["meta"] = meta if meta is not None else {}
    # detach from the Game's live objects so later engine work can't alias into a returned state
    return json.loads(json.dumps(st))


# ---------------------------------------------------------------------------------------------- bots
def make_bots(state: dict, powers: Iterable[str], kind: str = "search",
              alliances: Iterable[Iterable[str]] | None = None) -> dict[str, Player]:
    """Fresh bots for this decision point. Temperament is fixed per (seed, power) for the whole game; the
    RNG seed is per (seed, power, step), so the same state always gives the same decisions."""
    from .bots.random_bot import RandomBot
    from .bots.search import SearchBot
    from .bots.strategist import StrategistBot
    from .sim import draw_temperament

    seed, step = state.get("seed") or 0, state.get("step", 0)
    pairs = alliances if alliances is not None else (state.get("meta") or {}).get("alliances", [])
    pairs = [tuple(x) for x in pairs]
    board: dict = {}
    out: dict[str, Player] = {}
    for p in powers:
        allies = sorted({x for pair in pairs if p in pair for x in pair} - {p})
        temperament = draw_temperament(random.Random(f"brink:{seed}:{p}:temperament"))
        bseed = random.Random(f"brink:{seed}:{p}:{step}").randrange(1 << 30)
        if kind == "search":
            out[p] = SearchBot(seed=bseed, temperament=temperament, allies=allies, coalition=board)
        elif kind == "strategist":
            out[p] = StrategistBot(seed=bseed, temperament=temperament, allies=allies, coalition=board)
        elif kind == "random":
            out[p] = RandomBot(seed=bseed)
        elif kind == "hold":
            out[p] = Player()
        else:
            raise ValueError(f"bot kind must be one of {', '.join(BOT_KINDS)}")
    return out


# ---------------------------------------------------------------------------------------------- players
class CivilDisorder(Player):
    """A seat nobody decided for: holds, disbands by default, proposes nothing, abstains, keeps umbrellas."""

    name = "civil disorder"

    def vote(self, game, power, proposal):
        return None


UMBRELLA_NONE = {"UMBRELLA NONE", "UMBRELLAS NONE", "NO UMBRELLAS"}


class Submitted(Player):
    """Replays the order texts a human submitted for the pending decision point.

    In the `winter` step one list carries everything, sorted out by kind: 'VOTE YES|NO|VETO' (or bare
    YES/NO/VETO), 'BUILD ...'/'DISBAND ...', '<P> UMBRELLA <Q>' (or a bare power code). Umbrellas are the full
    list for next year; none given keeps the current ones; 'UMBRELLA NONE' drops them all."""

    name = "human"

    def __init__(self, texts):
        self.texts = _as_list(texts)

    def orders(self, game, power):
        return list(self.texts)

    def retreats(self, game, power, options):
        return list(self.texts)

    def propose(self, game, power):
        return normalise_proposal(self.texts)

    def _split(self, game, power):
        votes, adjust, umbrellas, none = [], [], [], False
        for t in self.texts:
            kind, text = classify_winter(game, power, t)
            if kind == "vote":
                votes.append(text)
            elif kind == "umbrella":
                umbrellas.append(text)
            elif kind == "umbrella_none":
                none = True
            else:
                adjust.append(text)
        return votes, adjust, (umbrellas if umbrellas or none else None)

    def vote(self, game, power, proposal):
        votes = self._split(game, power)[0]
        return votes[0] if votes else None

    def adjust(self, game, power, change):
        return self._split(game, power)[1]

    def umbrellas(self, game, power):
        return self._split(game, power)[2]


def classify_winter(game: Game, power: str, text: str) -> tuple[str, str]:
    """('vote' | 'umbrella' | 'umbrella_none' | 'adjust', normalised text) for one Winter order text."""
    t = text.strip()
    u = " ".join(t.upper().split())
    if u in ("YES", "NO", "VETO"):
        return "vote", normalise_vote(u)
    if u in UMBRELLA_NONE or u in {f"{power} {x}" for x in UMBRELLA_NONE}:
        return "umbrella_none", t
    if u in game.map.powers:
        return "umbrella", Umbrella(power, u).text()
    try:
        od = parse_order(t)
    except OrderError:
        return "adjust", t
    if isinstance(od, Vote):
        return "vote", t
    if isinstance(od, Umbrella):
        return "umbrella", t
    return "adjust", t


# ---------------------------------------------------------------------------------------------- queries
def _phase_name(g: Game) -> str:
    suffix = {"movement": "M", "retreats": "R", "propose": "A", "winter": "A"}.get(g.stage, "")
    return f"{SEASON_CODE[g.season]}{g.year}{suffix}"


def _winter_powers(g: Game) -> tuple[list[str], dict[str, tuple[int, int | None]]]:
    """Powers involved in the winter step, and each power's (change, change_if_passed)."""
    proposal = g._winter["proposal"] if g._winter else None
    changes: dict[str, tuple[int, int | None]] = {}
    recog = proposal.arg if proposal is not None and proposal.kind == "recognition" else None
    for p in g.active_powers():
        now = g.adjustment(p)
        alt = None
        if recog and g.ownership.get(recog) is not None:
            prev = g.ownership[recog]
            g.ownership[recog] = None
            alt = g.adjustment(p)
            g.ownership[recog] = prev
        changes[p] = (now, alt if alt != now else None)
    powers = [p for p in g.active_powers()
              if proposal is not None or g.map.nuclear(p) or changes[p][0] or changes[p][1]]
    return powers, changes


def _involved(g: Game) -> list[str]:
    if g.result is not None:
        return []
    if g.stage == "movement":
        protectors = {a for a, _ in g.umbrellas}
        return [p for p in g.active_powers() if g.units_of(p) or p in protectors]
    if g.stage == "retreats":
        return g.retreat_powers()
    if g.stage == "propose":
        return [g.assembly_chair()]
    if g.stage == "winter":
        return _winter_powers(g)[0]
    return []


def pending(gmap: BrinkMap, state: dict, powers: Iterable[str] | None = None) -> dict | None:
    """The open decision point, or None when the game is over.

    {"kind", "phase" ('S2031M'), "step", "powers" (who decides), "position" (live board in game-record
    phase format), "events" (Winter prelude events so far), "options": {power: ...}}. `powers` limits the
    options computed (they are the costly part), e.g. to the one human asking."""
    g = Game.from_state(gmap, state)
    if g.result is not None:
        return None
    involved = _involved(g)
    want = [p for p in involved if powers is None or p in set(powers)]
    kind = KINDS[g.stage]
    options: dict[str, dict] = {}
    if g.stage == "movement":
        options = {p: movement_payload(g, p)[0] for p in want}
    elif g.stage == "retreats":
        options = {p: retreat_payload(g, p, g._retreat["options"])[0] for p in want}
    elif g.stage == "propose":
        options = {p: propose_payload(g, p)[0] for p in want}
    elif g.stage == "winter":
        _, changes = _winter_powers(g)
        proposal = g._winter["proposal"]
        for p in want:
            vote = adj = umb = None
            if proposal is not None:
                vote, _ = vote_payload(g, p, proposal.text())
                vote.pop("status")
            change, alt = changes[p]
            if change or alt:
                adj, _ = adjust_payload(g, p, change)
                adj.pop("status")
                if alt is not None:
                    adj["change_if_passed"] = alt
            if g.map.nuclear(p):
                umb, _ = umbrella_payload(g, p)
                umb.pop("status")
            options[p] = {"vote": vote, "adjust": adj, "umbrellas": umb, "status": _power_info(g, p)["status"]}
    rec_kind = {"movement": "movement", "retreats": "retreat"}.get(g.stage, "winter")
    position = g._winter["rec"] if g._winter else g._phase(rec_kind, g.units)
    position = dict(position, units=[u.to_json() for u in g.units],
                    ownership=dict(sorted(g.ownership.items())), crisis=g.crisis)
    if g.stage == "retreats":
        position["dislodged"] = list(g._retreat["dislodged"])
    out = {"kind": kind, "phase": _phase_name(g), "step": g.steps, "powers": involved, "position": position,
           "events": list(g._winter["events"]) if g._winter else [], "options": options}
    if g._winter:
        out["chair"] = g._winter["chair"] or g.assembly_chair()
        out["proposal"] = g._winter["proposal"].text() if g._winter["proposal"] else None
    return out


def validate(gmap: BrinkMap, state: dict, power: str, orders: list[str]) -> list[dict]:
    """Check draft orders for `power` at the open decision point: [{text, ok, order, reason}]."""
    g = Game.from_state(gmap, state)
    texts = [t for t in (orders or []) if str(t).strip()]

    def void(why: str) -> list[dict]:
        return [{"text": t, "ok": False, "order": None, "reason": why} for t in texts]

    if g.result is not None:
        return void("the game is over")
    if power not in g.active_powers():
        return void(f"{power} is not in the game")
    if g.stage == "movement":
        return check_orders(g, "orders", power, texts, {"position": g.position()})
    if g.stage == "retreats":
        if power not in g.retreat_powers():
            return void("no dislodged units")
        return check_orders(g, "retreats", power, texts, retreat_payload(g, power, g._retreat["options"])[1])
    if g.stage == "propose":
        if power != g.assembly_chair():
            return void("only the chair proposes")
        return check_orders(g, "propose", power, [normalise_proposal(t) for t in texts],
                            propose_payload(g, power)[1])
    # winter: route each text to its check, keeping the input order
    _, changes = _winter_powers(g)
    proposal = g._winter["proposal"]
    groups: dict[str, list[tuple[int, str]]] = {"vote": [], "umbrella": [], "adjust": []}
    out: list[dict | None] = [None] * len(texts)
    for i, t in enumerate(texts):
        kind, norm = classify_winter(g, power, t)
        if kind == "umbrella_none":
            ok = g.map.nuclear(power)
            out[i] = {"text": t, "ok": ok, "order": None, "reason": None if ok else "not a nuclear power"}
        else:
            groups[kind].append((i, norm))
    if groups["vote"]:
        if proposal is None:
            res = [{"text": t, "ok": False, "order": None, "reason": "there is no proposal to vote on"}
                   for _, t in groups["vote"]]
        else:
            res = check_orders(g, "vote", power, [t for _, t in groups["vote"]], {})
            for r in res[1:]:
                if r["ok"]:
                    r.update(ok=False, reason="one vote only")
        for (i, _), r in zip(groups["vote"], res):
            out[i] = r
    if groups["umbrella"]:
        if g.map.nuclear(power):
            res = check_orders(g, "umbrellas", power, [t for _, t in groups["umbrella"]],
                               umbrella_payload(g, power)[1])
        else:
            res = [{"text": t, "ok": False, "order": None, "reason": "only nuclear powers extend umbrellas"}
                   for _, t in groups["umbrella"]]
        for (i, _), r in zip(groups["umbrella"], res):
            out[i] = r
    if groups["adjust"]:
        res = check_orders(g, "adjust", power, [t for _, t in groups["adjust"]], {"change": changes[power][0]})
        for (i, _), r in zip(groups["adjust"], res):
            out[i] = r
    return out  # type: ignore[return-value]


# ---------------------------------------------------------------------------------------------- advancing
def advance(gmap: BrinkMap, state: dict, decisions: dict[str, list[str] | str | None] | None = None,
            bots=None, expect_step: int | None = None) -> dict:
    """Resolve exactly one decision point and return the new state (the input is not modified).

    decisions: {power: [order text]} for the humans who submitted (a power present here is decided by
    its texts, even an empty list). bots: {power: Player}, or a callable(state) -> that dict, or None for
    `make_bots` over every non-human power (state["meta"]). Anyone else is in civil disorder.
    expect_step: optimistic concurrency, raises StaleState unless state["step"] == expect_step."""
    if state.get("result") is not None:
        raise GameOver("the game is over")
    if expect_step is not None and state.get("step") != expect_step:
        raise StaleState(f"state is at step {state.get('step')}, not {expect_step}")
    decisions = {k.upper(): v for k, v in (decisions or {}).items()}
    if bots is None:
        meta = state.get("meta") or {}
        humans = set(meta.get("humans", []))
        bots = make_bots(state, [p for p in gmap.power_order if p not in humans], kind=meta.get("bot_kind", "search"))
    elif callable(bots):
        bots = bots(state)
    players: dict[str, Player] = {}
    for p in gmap.power_order:
        if p in decisions and decisions[p] is not None:
            players[p] = Submitted(decisions[p])
        elif p in bots:
            players[p] = bots[p]
        else:
            players[p] = CivilDisorder()
    g = Game.from_state(gmap, state, players)
    g.step()
    return _dump(g, state.get("meta"))


def run_bots_until_human(gmap: BrinkMap, state: dict, humans: Iterable[str] | None = None, bots=None,
                         max_steps: int = 10_000) -> dict:
    """Advance while the open decision point involves none of `humans` (default: state["meta"]["humans"]).
    `bots` as for advance (a fixed dict is reused across steps; None rebuilds them per step)."""
    humans = set(humans if humans is not None else (state.get("meta") or {}).get("humans", []))
    for _ in range(max_steps):
        if state.get("result") is not None:
            return state
        g = Game.from_state(gmap, state)
        if set(_involved(g)) & humans:
            return state
        state = advance(gmap, state, {}, bots)
    return state


def record(state: dict) -> dict:
    """The game record (docs/ARCHITECTURE.md, "Game record") for a state, finished or not."""
    return {"map": state["map"], "seed": state["seed"], "players": state["players"], "phases": state["phases"],
            "result": state["result"]}
