---
name: Brink
description: A boxed wargame printed on screen. An offset-printed map sheet, die-cut counters and player-aid cards for a seven-power Diplomacy-family game.
colors:
  paper: "#f2eee2"
  paper-2: "#e8e2d1"
  paper-hi: "#fbf9f3"
  ink: "#1d1d1b"
  ink-2: "#47433b"
  ink-3: "#635d52"
  rule-soft: "#a49e90"
  edge-ink: "#000000"
  red: "#b3261e"
  red-deep: "#6e1510"
  red-hover: "#9c2019"
  red-tint: "#ebc9c3"
  red-tint-2: "#f3e0dc"
  select: "#e9c9c2"
  olive: "#4b5d3a"
  sea: "#d9e2e4"
  ocean: "#c9d6d9"
  sea-hover: "#e3eaec"
  sea-ink: "#34525e"
  sea-line: "#8aa1a9"
  buff: "#e4dcc2"
  filler: "#ebe7d9"
  imp-bg: "#c9c7c0"
  imp-line: "#85817a"
  decor: "#dedacd"
  brown: "#5a4836"
  power-usa: "#8FAFC6"
  power-chn: "#D9776A"
  power-eu: "#E6CB5A"
  power-rus: "#B0A794"
  power-ind: "#E7A35A"
  power-jpn: "#F4F1E8"
  power-glf: "#84A56C"
  power-none: "#C9C4B6"
typography:
  logotype:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "22px"
    fontWeight: 800
    lineHeight: 1
    letterSpacing: "0.18em"
  display:
    fontFamily: "Barlow Condensed, Archivo, Arial Narrow, sans-serif"
    fontSize: "34px"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "0.03em"
  headline:
    fontFamily: "Barlow Condensed, Archivo, Arial Narrow, sans-serif"
    fontSize: "18px"
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "0.04em"
  notation:
    fontFamily: "Barlow Condensed, Archivo, Arial Narrow, sans-serif"
    fontSize: "16px"
    fontWeight: 600
    letterSpacing: "0.04em"
    fontFeature: "tnum"
  body:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.45
  body-small:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "12.5px"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "Barlow Condensed, Archivo, Arial Narrow, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    letterSpacing: "0.06em"
  sea-name:
    fontFamily: "Barlow Condensed, Archivo, Arial Narrow, sans-serif"
    fontWeight: 500
    letterSpacing: "0.2em"
rounded:
  none: "0px"
spacing:
  hair: "2px"
  xs: "4px"
  sm: "6px"
  md: "10px"
  lg: "14px"
  gutter: "20px"
  gutter-narrow: "16px"
components:
  button:
    backgroundColor: "{colors.paper-hi}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    height: "36px"
    padding: "0 14px"
  button-hover:
    backgroundColor: "{colors.paper-2}"
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper-hi}"
    rounded: "{rounded.none}"
    height: "36px"
    padding: "0 14px"
  button-primary-hover:
    backgroundColor: "{colors.ink-2}"
  button-commit:
    backgroundColor: "{colors.red}"
    textColor: "{colors.paper-hi}"
    rounded: "{rounded.none}"
    height: "52px"
    width: "100%"
  button-commit-hover:
    backgroundColor: "{colors.red-hover}"
  track-control:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    size: "30px"
  track-control-play:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    size: "30px"
  field:
    backgroundColor: "{colors.paper-hi}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    height: "34px"
    padding: "0 8px"
  aid-card:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    padding: "13px 20px 15px"
  aid-card-decision:
    backgroundColor: "{colors.paper-hi}"
  turn-record-box:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    height: "46px"
  turn-counter:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    width: "32px"
    height: "21px"
  turn-counter-you:
    backgroundColor: "{colors.red}"
  crisis-box:
    backgroundColor: "{colors.paper-hi}"
    height: "36px"
  tooltip:
    backgroundColor: "{colors.paper-hi}"
    textColor: "{colors.ink}"
    padding: "8px 10px 9px"
  legend-toggle:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    padding: "4px 9px 4px 10px"
---

# Design System: Brink

## Overview

**Creative North Star: "The Wargame Box"**

Brink is the 1970s Avalon Hill / SPI boxed wargame it descends from, printed and played on screen. The board is an offset-printed map sheet: flat blue-grey sea, buff and cream land stock, power homes as flat printed tints, black coastlines and dark-brown borders, italic letter-spaced sea names, condensed-caps lettering. Units are square die-cut counters in each power's printed stock, carrying a black NATO-style symbol and the power code, with a sliver of counter thickness showing at the bottom-right. Everything around the map is a player-aid card: black rules, numbered printed tracks, rulebook section numbers.

The density is a wargame's: a lot of state at once, held together by rules and type rather than by cards and whitespace. There is one theme, the light printed sheet (`color-scheme: light`; no dark mode). Corners are square everywhere. Depth is physical only where a physical object exists (a counter on the sheet); everything else is flat ink on paper.

The world explicitly rejects the flat SaaS dashboard that online Diplomacy boards default to: white cards, pill chips, Inter, big-number tiles.

**Key Characteristics:**
- Printed map sheet with a double-rule neatline and a title cartouche; real Natural Earth geometry, never redrawn.
- Die-cut counters (14-unit square, 1.3-unit edge) used identically on the map, in the player aid, the key and the start screen.
- Every counter and control marker prints its power code; ownership never relies on colour alone.
- Turn record track across the top: the game's clock and the replay scrubber.
- Player-aid column of numbered sections keyed to the rulebook.
- Two families only: Archivo (UI, body, logotype) and Barlow Condensed (all lettering, labels, numbers, notation).
- Red is a spot colour with a job, never decoration.

## Colors

Paper and ink, a muted printed sea, seven power stocks, and one spot red.

### Primary
- **Spot Red** (`red`): the player's own hand and Crisis danger. The Commit orders button, the player's own order notation and drafts, the player's turn counter, the player's own unit selection outline, the "your move" tag, the player's power name in supply centres, the selected power on the start screen, legal-destination overprint for the player's own unit, Crisis boxes 6 to 10 and the danger marker. Also printed, as a spot colour, on rulebook section numbers in aid headings and the logotype's B box. `red-deep` is its die-cut edge and the Commit button's bottom lip; `red-hover` is Commit's hover; `red-tint-2` and `red-tint` print Crisis boxes 6-7 and 8-9.

### Secondary
- **Olive** (`olive`): the Assembly's passing side (PASS stamp, yes votes), unaligned swing states in supply centres, and the accord leaf badge. A quiet second spot colour for agreement, never for danger.

### Neutral
- **Sheet Paper** (`paper`): page ground, panels, controls at rest. **Bright Stock** (`paper-hi`): raised paper: tooltips, decision cards, the current turn-record half, hover fills, Crisis boxes, centre rings. **Shaded Stock** (`paper-2`): button hover, scrollbar track.
- **Press Black** (`ink`): text, every rule and border, coastlines, unit symbols, primary buttons, the turn counter and Crisis marker faces. `ink-2` secondary text and keys; `ink-3` tertiary text and the scrollbar thumb; `rule-soft` dotted and dashed row dividers and disabled controls. `edge-ink` is the die-cut thickness under ink counters.
- **Map inks**: `sea` (marginal seas and chokepoints, lakes), `ocean` (open oceans and the sheet ground inside the neatline), `sea-hover`, `sea-ink` (sea names, sea lanes, wrap marks), `sea-line` (dashed sea borders, graticule), `buff` (swing-state land, and the turn-record band and box-lid panels), `filler` (non-scoring land), `imp-bg`/`imp-line` (impassable hatch), `decor` (off-board land), `brown` (land borders).

### Power stocks
Each power's counter stock is a full-strength printed colour (`power-usa` … `power-glf`; `power-none` for unknown). Every other power colour is derived from the stock in `src/theme.ts`, never picked independently:
- **Counter edge**: stock mixed 50% toward ink (Japan's white stock uses `#3A3833`).
- **Order arrow**: stock mixed 20% toward ink, cased in ink (Japan's arrow stays white on its black casing). The player's own arrows are spot red.
- **Home tint**: stock mixed 30% toward paper (Japan `#FBFAF5`).
- **Aligned swing tint**: stock mixed 58% toward buff (Japan `#F6F3EA`).

### Named Rules
**The Spot Red Rule.** Red marks the player's own action and Crisis danger. Beyond that it appears only as printed spot colour on the logotype box and rulebook section numbers. Other powers, failures and errors never get red.

**The Code-on-Every-Counter Rule.** No power is ever shown by colour alone. Counters, control markers and swatches all print the three-letter power code.

**The Derived Tint Rule.** Homes, swing states, edges and arrows are computed from the seven stocks by `mix()`. Add a power by adding one stock, never a hand-picked tint.

## Typography

**Display/Lettering Font:** Barlow Condensed (500, 600, 700, italic 500/600), falling back to Archivo, Arial Narrow
**Body Font:** Archivo (400 to 800), falling back to Helvetica Neue, Arial
Both self-hosted via @fontsource.

**Character:** Condensed caps do the printing (headings, tracks, buttons, map lettering, order notation); Archivo does the reading (sentences, help, tooltips) and the heavy wide-spaced logotype.

### Hierarchy
- **Logotype** (Archivo 800, 22px top bar / 15px cartouche / 88px box lid (56px on phones), 0.18em tracking; 0.14em on the lid): BRINK, with the B reversed out of a red box.
- **Display** (Barlow Condensed 700, 34px (28px on phones), line-height 1, uppercase): the phase title at the head of the player aid. Modal titles 26px.
- **Headline** (Barlow Condensed 700, 18px, uppercase, 0.04em): player-aid section heads, preceded by the red section number. Phase label in the top bar 18px with tabular figures.
- **Notation** (Barlow Condensed 600, 16px, 0.04em, tabular figures, no-wrap): order notation (`A TEX - MEX`). Drafts at 17px.
- **Body** (Archivo 400, 14px, line-height 1.45): base text. Rows 13px; help and notes 12.5px; small 12px. Prose capped at 60–68ch.
- **Label** (Barlow Condensed 600–700, 10–15px, uppercase, 0.04–0.08em): row keys, track labels, buttons (15px), result words, legend heads, kbd.
- **Map lettering** (Barlow Condensed): land names 600 caps-spaced 0.05em in ink with a knock-out halo in the province's own fill; home names 700; impassable names italic 500. Sea names italic 500, 0.2em tracking, in `sea-ink`. Province codes 0.62em, shown only when zoomed in.

### Named Rules
**The Two-Family Rule.** Archivo and Barlow Condensed only. Numbers that count (supply centres, scores, tally, Crisis) set in condensed 700 with tabular figures.

**The Spaced Sea Rule.** Sea names are italic and widely spaced (0.2em); land names are upright. The difference reads sea from land without colour.

## Layout

A CSS grid over the full dynamic viewport (`100dvh`, page never scrolls):
- **Desktop**: columns `1fr | 392px`; rows `52px` top bar, `62px` turn record track, then the map. The player-aid column spans the track and map rows on the right.
- **≤1100px**: aid column narrows to 340px.
- **≤860px (phones)**: single column; rows 48px / 58px / map / 56px. The player aid becomes a fixed bottom sheet (56px closed, `min(80dvh, 680px)` open) with a grip handle; its foot stops being sticky. The cartouche hides, the key and zoom dock in a strip above the neatline so nothing floats over provinces, the selection slip spans the width, and Commit becomes a floating button at the stage's bottom right. The box lid stacks cover, powers, options, then a sticky Start bar.
- **≤420px**: supply centres go to one column; votes to three.

Gutters are 20px in the aid column and top bar (16px / 12px on phones). Section padding 13px 20px 15px. Map furniture sits 20px in from the stage edge. The map pane is inset 8px inside the stage; the neatline sits at 5px with an inner rule 7px further in.

### The turn record track
A buff band of joined year boxes (1.5px ink borders overlapping by −1.5px so rules never double). Each year is a 46px box: year number top-left, then halves for Spring and Fall split by a dashed soft rule, plus a narrow Winter cell behind a solid ink rule. The final year has a 4px right rule and an END mark. Past halves are hatched with a 20% ink diagonal; the current half is `paper-hi`. The game-turn counter (ink, 32×21px, 2px black die-cut edge) sits on the current half and is red when it is the player's move; a red NOW flag and a 3px red underline mark the live half in play. The track is the replay scrubber: clicking a half jumps to it, with prev / play / next controls at its head. It scrolls horizontally without a visible scrollbar.

### The player-aid column
Numbered sections, each with a black rule below and a condensed-caps head whose first element is the rulebook section number in red. Section numbers in use: Orders §5, Resolution §6, Chokepoints §8 (as a cross-reference on Closed and Sanctions rows), Security §9, Crisis §10, Assembly §11, Winter / supply centres §12, Winning §13. Rows are a 96px key column plus value, divided by 1px dotted soft rules. Replay order: phase head, What happened, Assembly, Crisis track, Supply centres, Security, Orders. In play, decision cards (`paper-hi`) sit at the top and the Commit orders button anchors a sticky foot behind a 2px rule.

## Elevation & Depth

Flat printed ink on paper. Depth exists only where a physical object sits on the sheet: counters show a solid offset edge (the die-cut thickness), drawn as a second filled shape, not a shadow. A small set of soft shadows marks things that lift off the table entirely.

### Shadow Vocabulary
- **Tooltip** (`box-shadow: 0 6px 16px rgba(29, 29, 27, 0.16)`): the hover card over the map.
- **Bottom sheet** (`box-shadow: 0 -6px 18px rgba(29, 29, 27, 0.14)`): the phone player aid.
- **Modal** (`box-shadow: 0 18px 44px rgba(29, 29, 27, 0.3)`, over a `rgba(29, 29, 27, 0.5)` backdrop): dialogs and the box lid.

### Named Rules
**The Thickness-Is-Physical Rule.** A solid offset edge appears only on things that are counters: units, control markers, the game-turn counter, the Crisis marker. Don't use an offset edge to make a button or card look raised (the Commit button's 4px bottom lip, which presses to 2px, is the only pressable exception).

## Shapes

Square-cornered throughout (radius 0, including fields and selects). Form comes from rules: 3px for the frame (top bar foot, aid column edge, box lid divisions), 2px for map furniture and section foot rules, 1.5px for controls, tracks and tables, 1px dotted or dashed soft rules between rows. Adjacent bordered controls overlap by their border width so rules join. The map has a double neatline (2px plus an inner 1px). Hatching (fine diagonals) is the printed texture: impassable land, unopened seas, past turn-record halves, legal-destination overprint.

## Components

### Die-cut counter (signature)
- **Anatomy:** a 14-unit square face in the power stock with a 0.9 non-scaling ink stroke (1.3 for Japan's white stock); an edge square of the same size offset 1.3 units down and right in the darkened stock; a black unit symbol at one stroke weight (0.95): Army a NATO infantry box with an X, Fleet a hull with superstructure, Capital stacked ingots, Influence broadcast arcs over a mast; the power code in condensed 700 at 4.5 units below the symbol.
- **Variants:** code-only swatch (power code at 6.6 units, no symbol) for supply-centre rows, umbrellas and keys; ghost (35% face, dashed stroke, no edge) for proposed builds.
- **On the map:** two slots per land province, military and civil, so two counters can stand side by side. Held supply centres show a small control marker (a die-cut tab with the holder's code); unheld swing centres a paper ring; home centres a ring filled in the owner's stock.
- **States:** hover thickens the stroke to 1.6; selected lifts the face, symbol and code 1.2 units up-left off its edge (0.18s, `cubic-bezier(0.16, 1, 0.3, 1)`) with a 2.2 stroke, red 2.4 when it is the player's unit; keyboard focus a dashed 2.6 stroke; dislodged a dashed stroke.

### Map sheet printing
- Sea to a rectangular neatline in `ocean`; marginal seas and chokepoints in `sea`, dashed `sea-line` sea borders, a faint graticule.
- Land: filler cream, swing states buff, homes in their tint, aligned swing states in the aligner's tint, impassable hatched grey. Land borders `brown` 0.75; coastlines ink 1.15; all strokes non-scaling.
- Sea lanes dashed `sea-ink` (6 / 3.5) with chevrons; dotted and faded when not yet open or blocked. Chokepoints carry a gate icon that inverts to ink with a bar when closed; closures print a dashed ink outline over a 55% fill. Unopened seas are hatched.
- The Pacific wrap is marked at both edges with drawn double chevrons and the italic word "continues".
- Order arrows are cased in ink; supports and convoys are dashed or thinner; failed orders drop to 42% opacity and print a fail-X tab.

### Buttons
- **Shape:** square (0px), 1.5px ink border, condensed 700 caps 15px at 0.06em, 36px tall.
- **Default:** `paper-hi`, hover `paper-2`. **Primary:** ink with paper text, hover `ink-2`. Disabled 50% opacity.
- **Commit orders:** red with a 2px `red-deep` border and 4px bottom lip; hover `red-hover`; active presses the lip to 2px and translates 2px down. Full-width 52px in the aid foot, letter-spacing 0.1em.
- **Map bar buttons:** 28px, condensed 600 14px caps; active is ink-filled.
- **Link button:** underlined Archivo 600 12.5px.

### Track and zoom controls
30px square ink-bordered controls joined edge to edge; the play control is ink-filled with paper glyphs. Zoom is a joined 2px-bordered group of 36×32px cells (+, −, FIT).

### Crisis track
Eleven joined boxes (0 to 10) in a 1.5px ink frame, 36px tall, condensed 600 numerals. Boxes 6–7 print `red-tint-2`, 8–9 `red-tint`, 10 solid red; 3px rules before each threshold. The marker is a die-cut counter inset in the current box: ink at 0–5, red with a `red-deep` edge at 6 or more (ink again on 10). A key below names Recession 6 (builds −1), State failure 8, Collapse 10 (all lose); reached thresholds turn red.

### Inputs / Fields
34px, 1.5px ink border, radius 0, `paper-hi`. Checkboxes 16px with red accent colour. Placeholder `ink-3`.

### Navigation and map furniture
- **Top bar:** logotype, the game name (condensed caps, `ink-2`) and, in play, the player's power with centre count; the phase label at right. 3px ink rule below.
- **Terrain & unit key:** bottom-left, 292px, 2px ink frame; an ink title bar that toggles a two-column key.
- **Selection slip:** a paper bar with a 2px ink frame, top-centre of the map, naming the selected counter and its order options.
- **Tooltip:** `paper-hi`, 1.5px ink border, 200–290px; condensed 17px title with the province code, then type, notes in `ink-2`, and a slot list.
- **Toast:** ink bar with paper text; hint variant on paper with a 2px ink frame.

### Focus, selection and scrollbars
- Focus: a 2px ink outline offset 2px (inset on track halves, zoom and the key toggle; paper-coloured on the ink key bar). The selected power on the start screen uses a red outline, doubled when focused.
- Text selection: `select` background with ink text. Caret red.
- Scrollbars: thin, `ink-3` thumb on a `paper-2` track (10px, square), ink on hover.

### Motion
- Counter lift on selection: 0.18s `cubic-bezier(0.16, 1, 0.3, 1)`.
- Turn counter set: 0.42s, dropping in from 3px left, 7px up and −6° rotation, same curve.
- Modal sheet-in: 0.32s from 10px below. Bottom sheet height: 0.3s, same curve.
- Land fill on phase change: 0.3s ease-out. Map zoom transitions 300–600ms (d3).
- `prefers-reduced-motion: reduce` removes all animation and transition.

## Do's and Don'ts

### Do:
- **Do** draw every unit, swatch and marker with the shared counter drawing in `src/counter.ts`, printing the power code.
- **Do** derive every power tint from the seven stocks in `src/theme.ts`.
- **Do** head every player-aid section with its rulebook section number in red and condensed caps.
- **Do** set lettering, labels, buttons, tracks and order notation in Barlow Condensed caps, with tabular figures for counts.
- **Do** separate with ink rules (3 / 2 / 1.5 / 1px dotted) and keep corners square.
- **Do** keep the map's projected geometry exactly as supplied in `board.json`, printed to the neatline.
- **Do** keep the light printed sheet as the only theme.

### Don't:
- **Don't** use white cards, pill chips, Inter or big-number tiles; this is the SaaS dashboard the world refuses.
- **Don't** round corners on anything.
- **Don't** use red for another power, a failed order, or general emphasis.
- **Don't** show ownership by colour alone.
- **Don't** give a non-counter element a die-cut edge to fake depth.
- **Don't** use Unicode glyphs as icons; draw them as SVG at the controls' stroke weight.
