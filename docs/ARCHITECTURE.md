# Brink architecture

The rulebook (design intent) lives in a Claude Doc:
https://claude.ai/code/artifact/bdcf4914-5cf9-4725-b1fa-165dfddbdadf
This file is the engineering contract: file formats, module boundaries and the exact
engine semantics where the rulebook leaves room for interpretation. When the two disagree,
fix one of them; don't let the code silently decide.

## Layout

```
data/provinces.yaml        board design (hand-written, source of truth)
src/brink/geo/             geometry pipeline: provinces.yaml + Natural Earth -> build/map.json, web/public/board.json
src/brink/map.py           loads build/map.json into a BrinkMap (graph queries)
src/brink/orders.py        order dataclasses, text notation parse/format
src/brink/adjudicator.py   two-layer simultaneous resolution (movement phases)
src/brink/game.py          game state + phase loop: movement, retreats, winter
src/brink/bots/            random, strategist (heuristic), search (adjudicator lookahead), human (server)
src/brink/sim.py           batch simulation + metrics
src/brink/analysis.py      static graph analysis (reach, exposure)
src/brink/cli.py           `brink analyse | play | sim | serve | validate-orders`
src/brink/server.py        local HTTP API for human-vs-bots play (`brink serve`, web `?play`)
src/brink/session.py       hosted play: JSON game state advanced one decision point at a time (stateless)
tests/                     pytest; military layer is differential-tested against MILA `diplomacy`
web/                       Vite + TypeScript board viewer (reads board.json + game records)
build/                     generated artifacts (map.json is committed; cache/ is not)
```

Python 3.11 (the MILA `diplomacy` oracle doesn't support newer). `uv sync --extra geo` for the pipeline.

## build/map.json (engine graph)

```jsonc
{
  "version": 1,
  "seam_lon": -168.6,
  "powers": { "USA": { "name": "United States", "nuclear": true, "homes": ["WAS","TEX","CAL","ALA"] }, ... },
  "provinces": {
    "WAS": { "name": "Washington", "kind": "land",        // land | sea
             "centre": "home",                             // home | swing | none
             "home_of": "USA",                             // homes only
             "coastal": true, "chokepoint": false,
             "impassable": false, "opens": null },         // opens: first year it can be entered, or null
    ...
  },
  "adj": {
    "army":  { "WAS": ["TEX", "CAN"], ... },   // land<->land sharing a border
    "fleet": { "WAS": ["NAO", "CAR", "TEX"], "NAO": [...], ... },
         // sea<->sea, sea<->coastal land, coastal land<->coastal land when they share a border AND a sea
    "any":   { ... }                           // union of army + fleet + land<->sea; used for 2-step range
  },
  "coasts": {},          // optional, multi-coast provinces: {"SPA": ["SPA/NC","SPA/SC"]}; fleet adj then uses coast ids
  "trade_routes": [ { "id": "TRA", "name": "Transatlantic", "ends": ["WAS","PAR"], "path": ["NAO"], "opens": null } ],
  "start": { "year": 2031, "crisis": 3, "umbrellas": [["USA","JPN"]],
             "units": [ { "power": "USA", "type": "F", "at": "WAS" }, ... ] }
}
```

All adjacency lists are symmetric. Impassable provinces appear in `provinces` (for drawing) and in no adjacency list.

## web/public/board.json (drawn board)

Geometry is pre-projected in Python (Equal Earth, central meridian seam+180, units = SVG user units,
width 2000). The web viewer never does projection math.

```jsonc
{
  "width": 2000, "height": 1000,
  "provinces": {
    "WAS": { "d": "M...Z",                 // SVG path (may be several subpaths)
             "label": [x, y],              // pole of inaccessibility of the largest part
             "slots": { "military": [x,y], "civil": [x,y] },   // unit anchor points (land); sea has military only
             "centre": [x, y] | null,      // supply-centre marker
             "label_size": 1.0 },          // font scale hint from area
    ...
  },
  "routes": { "TRA": { "d": "M...", "chokepoints": [] } },  // smooth curve through path sea zones
  "seam": { "left_x": 0, "right_x": 2000 },                  // for wrap indicators
  "wraps": [ ["CPA", "CPA"], ["BRS","BRS"] ]                  // zones that continue across the edge
}
```

## Orders (text notation)

Province ids are the 3-letter ids. Unit types A F C I. Case-insensitive on input; canonical upper case.

```
A TEX H                     hold
A TEX - MEX                 move (Army may add VIA to require convoy: A PAR - BRA VIA)
C ALA - TOK                 Capital move (land step, short hop, or a whole trade route; engine works out which)
C MEX S A TEX - MEX         support move (any layer may support any layer)
F NAO S A WAS H             support hold
F NAO C A WAS - PAR         convoy
F HOR CLOSE                 close chokepoint to everyone else
F HOR CLOSE CHN IND         close chokepoint to named powers
C BRA SANCTION CHN
I MOS DISRUPT TUR
A DEL COMMONS
USA WITHDRAW JPN            power-level, movement phases only
```

Retreats: `A UKR R BLR`, `A UKR D` (disband). Winter: `BUILD A WAS`, `BUILD C MEX`, `DISBAND I ROM`,
`USA UMBRELLA JPN`, `PROPOSE <resolution>`, `VOTE YES|NO|VETO`.

## Engine semantics (decisions the rulebook leaves open)

Layers. Land provinces have a military slot and a civil slot; sea zones have a military slot only.
A, F = military; C, I = civil.

Movement reach (own layer):
- Army: `adj.army`, or by convoy along a chain of Fleets in sea zones (classic rules; VIA follows DATC 4.A.3).
- Fleet: `adj.fleet`.
- Capital: (a) `adj.army` neighbour; (b) **short hop**: from a coastal province to another coastal
  province that borders the same sea zone, unless that zone is an `ocean`; (c) the far end of a trade route whose end it stands on.
  Hops and routes are blocked if any sea zone used is a chokepoint closed to the mover, or not yet open.
- Influence: any land province within range 2 in `adj.any`, where entering a sea costs 1 and an
  `ocean` costs 2 (so Influence never crosses an ocean). It jumps:
  units on the way don't block it. Closed chokepoints don't block it. Not-yet-open zones do.
- Nobody enters impassable provinces or zones before their `opens` year.

Entry restrictions (an order breaking these is void; the unit holds):
- Civil units never enter another power's home province.
- Military units never enter an unaligned swing state (filler provinces are open to all).
- Military units never enter a nuclear power's home, nor a home protected by an umbrella against them.
- A unit may support only into a province its own type could legally move to this turn
  (so Military can't support a move into an unaligned swing state).

Strength. Classic: 1 + valid supports. Supports may come from either layer.
- Support is cut by: a same-layer unit ordered to move into the supporter's province, unless it comes from
  the province the support is aimed at; a Disrupt targeting the supporter's province; the supporter being dislodged.
  A void move (illegal) does not cut. A convoyed army cuts per the 2000 rules (Szykman paradox rule).
- Dislodgement and standoffs only happen within a layer. A unit never dislodges its own power's unit,
  and a power's support doesn't count towards dislodging its own unit.
- Military layer must agree with classic Diplomacy on any position that uses only A/F (DATC; differential
  tests against MILA `diplomacy` on the standard map).

Special orders: CLOSE, SANCTION, DISRUPT, COMMONS, FORGE count as holds. Their effect applies unless
the unit is dislodged. DISRUPT range = the same range-2 rule (oceans cost 2), target any province.
SANCTION requires the Capital to stand in its own home or in a swing state aligned to its owner.
CLOSE requires a Fleet in a chokepoint; the chokepoint is closed for the *same* turn's Capital moves and
convoys only if the Fleet is not dislodged (resolve the dependency in the resolver; if circular, the Fleet
holds and the closure applies).

WITHDRAW takes effect before resolution of the turn in which it is ordered.

Retreats: within own layer, to an adjacent (own reach, no routes/hops/convoys) province with that layer's
slot empty, not the attacker's origin, not a province left empty by a standoff this turn. Two units retreating
to the same place are both disbanded. No legal retreat -> disband.

Winter, in order:
1. Alignment (swing states): both slots one power, or one slot + other empty -> that power;
   split -> no change; empty -> no change. Then Drift: a swing state whose owner has had no unit in either slot
   at the end of two consecutive Winters becomes unaligned (counter resets when a unit is present).
   Non-nuclear homes: a foreign Military unit present in Winter takes it (if not umbrella-protected).
2. Crisis: +1 baseline (+2 from 2036), +1 per chokepoint closed this year, +1 if a nuclear power's
   Military dislodged another nuclear power's Military this year, -floor(commons/4). Clamp >= 0.
   If >= 10: game ends, Collapse.
3. Assembly: chair = fewest centres (ties: USA, CHN, EU, RUS, IND, JPN, GLF). Proposal, votes
   (1 + floor(swing/3) each), one veto per power per game. Effects last next Spring + Fall + Winter builds.
4. Adjustments: centres = owned homes + aligned swing; supply = centres minus contested states (an aligned
   swing state with another power's unit in either slot; Rules.contested_supply). builds = max(0, supply - units - sanctions
   - (1 if crisis >= 6) - (1 if climate accord and no COMMONS this year)). Disband if units > centres.
   A, F: original home centre still owned, military slot empty (F coastal). One forward-base A per year:
   aligned swing state where the power has a civil unit, military slot empty.
   C, I: owned home or aligned swing state, civil slot empty.
5. Umbrellas declared for next year.
6. End: Primacy (>= 9 aligned swing states) or after Winter 2040.

## Game record (web replay)

`runs/<name>/game.json`:

```jsonc
{
  "map": "brink-v0", "seed": 1, "players": { "USA": "dumbbot", ... },
  "phases": [
    {
      "name": "S2031M", "year": 2031, "season": "spring", "kind": "movement",   // movement | retreat | winter
      "units": [ { "power": "USA", "type": "A", "at": "TEX" } ],                 // state at START of the phase
      "orders": [ { "power": "USA", "text": "A TEX - MEX",
                    "unit": { "type": "A", "at": "TEX" }, "kind": "move", "to": "MEX",
                    "target": null,         // support/convoy: { "type": "A", "at": "TEX" }, plus "to" (null for hold support)
                    "result": "ok" } ],     // ok | bounce | cut | void | dislodged | failed
      "ownership": { "MEX": null, "WAS": "USA", ... },
      "crisis": 3, "umbrellas": [["USA","JPN"]], "closed": { "HOR": ["CHN"] },
      "sanctions": { "CHN": 1 }, "commons": { "EU": 2 },
      "assembly": null | { "chair": "GLF", "proposal": "...", "votes": { "USA": "yes" }, "passed": true },
      "dislodged": [ { "power": "CHN", "type": "C", "at": "BRA", "from": "MEX" } ],
      "events": [ "Brazil aligns to United States" ]
    }
  ],
  "result": { "kind": "time" | "primacy" | "collapse", "winner": null, "scores": { "USA": 23.1, ... } }
}
```

## Hosted play: state and decision points

`brink serve` runs `Game.run()` in a thread and blocks it on a HumanPlayer; a stateless function
(Vercel Python, Lambda) can't do that. `brink.session` exposes the same engine as pure functions over a
JSON state that lives in a database row. Same rules, same records: a game advanced stepwise with the
same bot instances produces a byte-identical record to `Game.run()` (tested), because `run()` itself is
now `while result is None: step()`.

### Decision points

A game is a sequence of decision points, each a simultaneous decision by a set of powers.
`Game.stage` is the one pending; `Game.step()` resolves exactly one.

| kind (`pending`) | `Game.stage` | phase | who decides | missing decision (civil disorder) |
|---|---|---|---|---|
| `orders` | movement | `S2031M`, `F2031M` | active powers with a unit or an umbrella to withdraw | units hold |
| `retreats` | retreats | `S2031R`, `F2031R` | powers with a dislodged unit | dislodged units disband |
| `propose` | propose | `W2031A` | the Assembly chair only | proposes nothing |
| `winter` | winter | `W2031A` | everyone with a vote, an adjustment or an umbrella | abstain, default disbands, umbrellas kept |

Winter keeps the rules' order of operations. The decision-free prelude (alignment, drift, home
capture, Crisis, possibly Collapse) runs at the end of the Fall step, so the chair is known while
`propose` is pending. The `winter` step then applies the votes, then everyone's adjustments, then
umbrellas, then the end checks. Votes, builds/disbands and umbrellas arrive together, so adjustments
are written before the vote is known. They are checked against the position after the Assembly.
Only a passed RECOGNITION can change a power's adjustment, and the options say so (`change_if_passed`).
If it does change, excess builds are void and missing disbands follow the default rule.

Winter orders are one list per power, sorted out by kind: `VOTE YES|NO|VETO` (bare `YES` works too),
`BUILD ...`/`DISBAND ...`, and `USA UMBRELLA JPN` (a bare power code works too). The umbrella list is
the full set for next year. If a power sends no umbrella orders it keeps its current umbrellas;
`UMBRELLA NONE` drops them all.

### API (`brink.session`)

```python
state = new_game(gmap, seed=None, humans={"USA"}, bot_kind="search", rules=None, alliances=())
pending(gmap, state, powers=None) -> None | {"kind", "phase", "step", "powers", "position", "events",
                                             "options": {power: ...}, ["chair", "proposal"] (Winter)}
validate(gmap, state, power, ["A TEX - MEX"]) -> [{"text", "ok", "order", "reason"}]
advance(gmap, state, {"USA": [...]}, bots=None, expect_step=None) -> new state   # one decision point
run_bots_until_human(gmap, state, humans=None, bots=None) -> state             # skip bot-only points
make_bots(state, powers, kind="search")                                        # search|strategist|random|hold
record(state) -> game record
```

- The `options` payloads have the same shape as the `brink serve` decisions (`brink.bots.human.*_payload`).
  `orders`: `{units: [{unit, name, options}], withdraw, status}`. `retreats`: `{units: [{unit, name, to}],
  status}`. `propose`: `{resolutions, chair, status}`. `winter`: `{vote: {proposal, chair, can_veto, weight}
  | null, adjust: {change, builds, disband, change_if_passed?} | null, umbrellas: {candidates, current} |
  null, status}`. `pending(..., powers=[me])` computes options only for those powers.
- `advance` treats a power present in `decisions` as decided by its texts, even an empty list. Bots
  decide for the other non-human powers. Any seat left over is in civil disorder. The input state is
  never mutated. `expect_step` gives optimistic concurrency (`StaleState`); a finished game raises `GameOver`.
- Bots are rebuilt on every call. Temperament comes from (seed, power) and the RNG seed from (seed,
  power, step), so the same state always gives the same bot orders. The coalition board (allies reading
  this phase's posted orders) is only ever read within one phase, so a board shared by the bots of one
  call is exact. Alliances are static pairs from `state["meta"]["alliances"]`; the sims' evolving
  `Diplomat` is not carried into hosted games.

### State format (`Game.to_state()`, version `v: 1`)

```jsonc
{
  "v": 1, "map": "brink-v0", "seed": 7, "rules": { ...Rules fields... },
  "players": { "USA": "human", "CHN": "search", ... },        // names for the record
  "year": 2033, "season": "fall", "stage": "movement", "step": 9,
  "units": [ { "power": "USA", "type": "A", "at": "TEX" } ],    // order matters (bots iterate it)
  "ownership": { ... }, "crisis": 4, "umbrellas": [["USA","JPN"]], "vetoes_used": [], "drift": { "MEX": 1 },
  "eliminated": [], "resolution": ["sanctions","CHN"] | null, "pending": null,   // effects derive from resolution
  "year_acc": { "closed": [], "sanctions": {}, "commons": {}, "commons_issued": [], "clash": false },
  "retreat": null | { "options": [[unit, ["BLR", ...]], ...], "dislodged": [...] },
  "winter":  null | { "rec": {phase so far}, "events": [...], "orders": [...], "chair": "GLF", "proposal": [kind, arg] | null },
  "phases": [ ...game record phases... ], "result": null | {...},
  "meta": { "humans": ["USA"], "bot_kind": "search", "alliances": [] }    // session-level, opaque to Game
}
```

Size: about 7 KB at the start, ~80-100 KB in 2035, ~175-230 KB by 2040 (gzip: 7-10 KB). `phases`
is ~97% of it and only ever grows by appending. Bots read `len(phases)` and recent phases, so the engine
needs the whole list. A backend can store it in a separate append-only table and reassemble the state.

Cost per `advance` (full map, Apple silicon, one core): an `orders` step with 6-7 SearchBots takes
~65 ms (max ~85 ms). Retreats, propose and winter take 3-7 ms. `pending` for every power takes 2-4 ms;
`from_state` takes ~2 ms. A game is ~42-60 decision points.

