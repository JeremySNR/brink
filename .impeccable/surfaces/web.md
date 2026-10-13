---
version: 1
slug: "web"
primary_target: "web"
related_targets: []
---

# Brink board (web app: replay + play)

Scope: the whole web client in web/ (board, side panel, turn record, play mode, start screen). Mode: Operate.
Audience and job: a playgroup of friends reading the board and entering orders for one power against six bots, then stepping through what resolved. Desktop first; phones must still work.
Constraints: the real Natural Earth geometry in web/public/board.json stays exactly as projected (no redrawn or stylised shapes); every engine state stays visible (both slots, contested, closed, umbrellas, sanctions, Commons, Crisis, Assembly); replay (?game=) and play (?play) both keep working.
Memorable moment: units are printed die-cut counters stacked on a printed map sheet, and the turn record track runs the game's ten years across the top.
Unresolved: online multiplayer and negotiation UI are future surfaces.

## Direction contract

THESIS: Brink is the boxed wargame it descends from, printed and played on screen; it refuses the flat SaaS dashboard (white cards, pill chips, Inter, big-number tiles) that online Diplomacy boards default to.

OWN-WORLD: An offset-printed map sheet: flat sea blue-grey, buff land, power homes as flat printed tints, black and dark-brown linework, italic spaced sea names, condensed caps lettering. Units are square die-cut counters in each power's printed colour with a black unit symbol and the province code, a sliver of counter thickness showing. Panels are player-aid cards: rule-section references from the real rulebook, printed tracks with numbered boxes, black rules, red reserved for the player's own action and Crisis danger.

STORY: The player sees the whole board, knows whose counters are where and which states are contested within a glance, enters orders by clicking counters, commits, and watches the turn record advance as the phase resolves.

FIRST VIEWPORT: Top bar with the BRINK logotype, the power being played and the phase. Under it, the turn record track: 2031 to 2040, Spring and Fall halves, a game-turn counter on the current half; it doubles as the replay timeline. The map sheet fills the rest at left with a terrain and unit key in a corner and zoom controls. At right, a player-aid column: orders (section 5), Crisis track 0 to 10 with Recession 6, State failure 8 and Collapse 10 printed on the track (section 10), supply centres by power (section 12), security (umbrellas, closures). The Commit orders button anchors the column's foot.

FORM: Wargame Box (1970s Avalon Hill and SPI boxed wargames), position 1 on the ordered list; the user chose IMPECCABLE'S PICK over the assigned Intaglio. seed: none (unavailable)

SIGNATURE: die-cut counters on the real map (military and civil slots as two counters per province, control markers on aligned swing states, power code printed on every counter so ownership never relies on colour), plus the turn record track as the game's clock and replay scrubber.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
