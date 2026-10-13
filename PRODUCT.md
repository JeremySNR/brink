# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users
A playgroup: friends the designer invites to play. They know board games (many know Diplomacy) but not Brink, so the game has to teach itself and feel finished. Today a player usually plays one power against six bots; multiplayer between friends is the intended next step. Secondary user: the designer, who playtests balance and rules changes.

## Product Purpose
Brink is a seven-player game of great-power competition in a fictional 2030s, built on Diplomacy's engine: secret simultaneous orders, units of equal strength, no dice, agreements that aren't binding. Players win by aligning swing states, not by conquest. Success: a group of friends sits down, understands the board within a turn, and argues about it afterwards.

## Positioning
The only Diplomacy-family game where power has two layers (military and civil), where nuclear homes can't be attacked so competition runs through swing states and proxy wars, and where a shared Crisis track can end the game with everyone losing. Its rules are balanced in thousands of simulated games, and the balance results are part of the design record.

## Operating Context
- A game is a sequence of years: Spring and Fall movement phases (negotiate, write orders, resolve, retreat) and a Winter phase (alignment, Crisis, Assembly vote, builds, umbrellas). Phases are named like `S2031M`, `F2031M`, `W2031A`.
- A player spends most of a turn reading the board, then entering orders on it: click a unit, click a destination; support, convoy, disrupt, commons, sanction and close orders.
- After committing, they step through what every power did (a replay of resolved phases) before their next decision.
- Order notation is part of the game's vocabulary: `A TEX - MEX`, `C MEX S A TEX - MEX`, `F HOR CLOSE`, `I MOS DISRUPT TUR`.
- Rules live in a shared rulebook document; engine semantics in docs/ARCHITECTURE.md.

## Capabilities and Constraints
- Seven powers: United States, China, European Union, Russia, India, Japan, Gulf. Five nuclear, two not.
- Board: 61 land provinces from real borders (homes, 20 swing states, filler, 4 impassable), 28 sea zones, 7 chokepoints, 17 trade routes; the Pacific wraps off both edges.
- Units: Army, Fleet (military layer); Capital, Influence (civil layer). Each land province has a military slot and a civil slot.
- State the UI must show: ownership/alignment, contested states, both slots per province, umbrellas, closed chokepoints, sanctions, Commons, the Crisis track (0–10, thresholds at 6, 8, 10), Assembly proposals and votes, scores.
- Stack: Vite + TypeScript web client, d3-zoom; board geometry pre-projected SVG paths (web/public/board.json); Python engine and bots served locally by `brink serve`; replay mode reads game records (`?game=`), play mode at `?play`.
- Desktop/laptop first with mouse and keyboard; phones must still work for checking and entering a turn.
- Undecided: online multiplayer between friends; negotiation/press UI; final name (Brink is a working title).

## Brand Commitments
- Working title: Brink.
- Tone: a serious geopolitical game with board-game warmth. The politics are treated straight; the experience is inviting to sit down with.
- Fictional near-future framing; real states appear, but no real conflict is reduced to decoration. Taiwan is a chokepoint, not a prize; North Korea is impassable.

## Evidence on Hand
- Real game records from simulations: web/public/games/*.json, runs/*/games/.
- Balance results: runs/*/report.md; summarised in the rulebook doc.
- No testimonials, players, press or artwork exist; do not invent them.

## Product Principles
1. The board is the game: everything a player needs to decide should be readable on the map first, panels second.
2. Teach by playing: new players learn from the board and plain-language results, not from a manual.
3. Show consequences: after each phase the player can see exactly what happened and why.
4. Serious subject, warm table: credibility in content, generosity in the experience.
5. Every rule visible: state that changes outcomes (contested, closed, protected, sanctioned) is never hidden.

## Accessibility & Inclusion
Seven power colours must stay distinguishable for common colour-vision deficiencies, so ownership is never shown by colour alone. Keyboard play for order entry and replay.
