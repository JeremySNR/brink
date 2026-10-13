"""SearchBot: StrategistBot plans, checked against the real adjudicator.

Each movement phase it
1. samples a handful of candidate order sets from its own heuristic planner at higher noise,
2. predicts every other power's orders with a model of that power (a StrategistBot that knows the
   public alliances), a few samples each,
3. resolves every candidate against every prediction with the engine's adjudicator, and
4. keeps the candidate with the best average outcome: projected centres at the next Winter,
   units kept, enemies dislodged, allies' gains (a little) and position quality.

It is a one-ply "sampled best response", the approach no-press Diplomacy bots use. All other phases
(retreats, builds, votes, umbrellas) are inherited from StrategistBot.
"""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

from ..adjudicator import adjudicate
from ..orders import OrderError, parse_order
from ..state import CIVIL, MILITARY, prov_of
from .strategist import StrategistBot, Temperament

if TYPE_CHECKING:  # pragma: no cover
    from ..game import Game


class SearchBot(StrategistBot):
    name = "search"

    def __init__(self, seed=None, temperament: Temperament | None = None, allies=(), candidates: int = 16,
                 samples: int = 3, candidate_noise: float = 2.5, coalition: dict | None = None):
        super().__init__(seed=seed, temperament=temperament, allies=allies, coalition=coalition)
        self.candidates = candidates
        self.samples = samples
        self.candidate_noise = candidate_noise
        self._models: dict[str, StrategistBot] = {}

    def _model(self, power: str) -> StrategistBot:
        if power not in self._models:
            model = StrategistBot(seed=self.rng.randrange(1 << 30), allies=self._allies, coalition=self.coalition)
            model.post = False
            self._models[power] = model
        return self._models[power]

    @staticmethod
    def _parse(power: str, texts: list[str]) -> list[tuple[str, object]]:
        out = []
        for t in texts:
            try:
                out.append((power, parse_order(t)))
            except OrderError:
                pass
        return out

    def orders(self, game: "Game", power: str) -> list[str]:
        # candidate plans: the normal plan first, then noisier ones
        self.post = False
        cands: list[list[str]] = []
        seen = set()
        for i in range(self.candidates):
            self.noise = 0.6 if i == 0 else self.candidate_noise
            plan = StrategistBot.orders(self, game, power)
            key = tuple(sorted(plan))
            if key not in seen:
                seen.add(key)
                cands.append(plan)
        self.noise = 0.6
        for plan in self._operations(game, power, cands[0]):
            key = tuple(sorted(plan))
            if key not in seen:
                seen.add(key)
                cands.append(plan)
        self.post = True
        if len(cands) == 1:
            self.publish(game, power, cands[0])
            return cands[0]
        # predictions of everyone else
        others = [p for p in game.active_powers() if p != power]
        predictions = []
        for _ in range(self.samples):
            pred = []
            for q in others:
                pred += self._parse(q, self._model(q).orders(game, q))
            predictions.append(pred)
        ctx = game.context()
        best, best_score = cands[0], None
        for plan in cands:
            mine = self._parse(power, plan)
            score = 0.0
            for pred in predictions:
                res = adjudicate(game.map, game.units, mine + pred, ctx)
                score += self._evaluate(game, power, res)
            if best_score is None or score > best_score:
                best, best_score = plan, score
        self.publish(game, power, best)
        return best

    def _operations(self, game: "Game", power: str, base_plan: list[str], limit: int = 6) -> list[list[str]]:
        """Concentrated-force variants of the base plan: for each target slot held by a rival (a state
        to take, or an occupier to evict), one attacker plus every unit that can support it."""
        pos = game.position()
        m = game.map
        base = {}
        for text in base_plan:
            try:
                od = parse_order(text)
            except OrderError:
                continue
            if hasattr(od, "utype"):
                base[(od.utype, od.loc)] = text
        mine = [u for u in pos.units if u.power == power]
        allies = self.allies(game, power)
        targets = []
        for e in pos.units:
            if e.power == power or e.power in allies:
                continue
            p = m.provinces[e.prov]
            owner = game.ownership.get(e.prov)
            if p.centre != "swing" or owner in allies:
                continue
            worth = 3.0 if owner == power else 2.0 if owner == e.power else 1.0
            targets.append((worth, e))
        targets.sort(key=lambda x: -x[0])
        out = []
        for worth, e in targets:
            attackers = [u for u in mine if u.layer == e.layer and
                         any(prov_of(mv.to) == e.prov for mv in pos.legal_moves(u))]
            if not attackers:
                continue
            att = min(attackers, key=lambda u: self._stay(game, power, u))
            plan = dict(base)
            plan[(att.type, att.loc)] = f"{att.type} {att.loc} - {e.prov}"
            helpers = 0
            for s in sorted(mine, key=lambda u: self._stay(game, power, u)):
                if s == att:
                    continue
                if pos.can_support_into(s, e.prov, att.layer):
                    plan[(s.type, s.loc)] = f"{s.type} {s.loc} S {att.type} {att.loc} - {e.prov}"
                    helpers += 1
            if helpers:
                out.append(list(plan.values()) + [x for x in base_plan if "WITHDRAW" in x])
            if len(out) >= limit:
                break
        # joint operations: throw spare units behind an attack an ally has already committed to
        for au, mv in self._ally_moves(game, power, pos)[:limit]:
            dest = prov_of(mv.to)
            occ = pos.by_slot.get((dest, au.layer))
            if occ is None or occ.power == power or occ.power in allies:
                continue
            plan = dict(base)
            helpers = 0
            for s in sorted(mine, key=lambda u: self._stay(game, power, u)):
                if helpers >= 2:
                    break
                if pos.can_support_into(s, dest, au.layer):
                    plan[(s.type, s.loc)] = f"{s.type} {s.loc} S {au.type} {au.loc} - {dest}"
                    helpers += 1
            if helpers:
                out.append(list(plan.values()) + [x for x in base_plan if "WITHDRAW" in x])
        return out

    def _stay(self, game: "Game", power: str, u) -> float:
        base, _, threats = self._values(game, power)
        return base[u.layer].get(u.prov, 0.0) + (2.0 if threats.get(u.slot, 0) else 0.0)

    def _evaluate(self, game: "Game", power: str, res) -> float:
        m = game.map
        base, _, _ = self._values(game, power)
        slots: dict[tuple[str, str], str] = {(u.prov, u.layer): u.power for u in res.units}
        allies = self.allies(game, power)
        projected: dict[str, int] = {}
        for s in m.swing_states():
            mil, civ = slots.get((s, MILITARY)), slots.get((s, CIVIL))
            owner = game.ownership.get(s)
            if mil and civ and mil == civ:
                owner = mil
            elif mil and not civ:
                owner = mil
            elif civ and not mil:
                owner = civ
            projected[owner] = projected.get(owner, 0) + 1
            occupants = {x for x in (mil, civ) if x}
            if owner == power and occupants - {power}:
                projected[power] -= 1          # contested: it pays nothing this Winter
            elif owner and owner != power and power in occupants and owner not in allies:
                projected["_denied"] = projected.get("_denied", 0) + 1
        mine = projected.get(power, 0) + 0.3 * projected.get("_denied", 0)
        ally_gain = sum(projected.get(a, 0) for a in allies)
        fall = game.season == "fall"
        v = (10.0 if fall else 6.0) * mine + 3.0 * ally_gain
        for u in res.units:
            if u.power == power:
                v += 1.0 + 0.4 * base[u.layer].get(u.prov, 0.0)
        for d in res.dislodged:
            if d.unit.power == power:
                v -= 5.0
            elif d.unit.power in allies:
                v -= 2.0
            else:
                v += 1.5
        v -= 2.0 * res.sanctions.get(power, 0)
        # the Crisis: a Collapse scores everyone zero, so its weight grows as the track climbs
        danger = max(0, game.crisis - 4)
        v += (1.0 + self.t.civic) * (1.0 + danger) * res.commons.get(power, 0)
        if res.nuclear_clash:
            v -= 1.5 * danger
        v -= 1.0 * danger * sum(1 for c, who in res.closed.items() if who)
        return v
