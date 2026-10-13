disposition: fix

Inputs not provided or not readable: no QUALITY BAR card (the packet says none exists); the rulebook is not in the repo, so the section numbers in the player-aid column (5, 6, 9, 10, 11, 12, 13 in web/src/panel.ts RULE) were not checked against it; the installed `impeccable` CLI has no `--check-seed` (running it started a new roll instead), so the seed could not be confirmed. Source files were sampled, not read in full: play.ts, board.ts and overlay.ts were only searched.

## persistence

pass. PRODUCT.md exists. This is a code-led build (config.json `buildPath: code`), so no state.json, spec.json or diff directories are expected. `.impeccable/mocks/decision/` is the direction round and needs no approval record. DESIGN.md does not exist yet; for a new world that is not a finding at this stage. All 11 captures are present and valid: the five required ones (desktop 1440x900, mobile 390x844 @2x, play-desktop, start, winter) plus the six additional ones. Each shows the state its filename names, with no blank regions.

## fidelity

No approved comp. model-pick.html is a critique reference, so it is not scored element by element. Rows below are judged against the direction contract (OWN-WORLD, FIRST VIEWPORT, SIGNATURE).

| element | state | evidence |
|---|---|---|
| TYPE | match | Uppercase Barlow Condensed for section heads, track and buttons; italic letter-spaced sea names; Archivo for body text. Both fonts are self-hosted via @fontsource. The mixed-case heavy Archivo panel title ("Fall 2035", "Spring 2031 · your move") is a small step away from "condensed caps lettering". Acceptable, but it is the most SaaS-looking type on the page. |
| MATERIAL | match | Flat printed tints, black coastlines, hatching on swing states and on the turn record's past halves, and die-cut counters with a 1.3-unit edge showing their thickness (counter.ts). The edge appears only on actual counters (units, game-turn marker, Crisis marker), so this is real physicality, not CSS bevelling. No textures pretend to be something the page doesn't render. |
| GROUND (map sheet) | contradicted | OWN-WORLD says the map is a printed sheet with flat blue-grey sea. In the build, the projection's oval floats on the cream page colour (#f2eee2) inside the map pane. Desktop at fit (desktop.png, winter.png) has cream corners and a ~100px cream band above the oval. Play mode at its default zoom (play-desktop.png) has a ~190px empty cream band above the Arctic. On mobile (mobile.png) about 260px of cream sits above the map and 180px below it, roughly half the phone screen. The reference prints sea to the board edge (`<rect width=1050 height=796 fill=#D9E2E4>` plus a grid). OWN-WORLD names no colour for the page around the map, so there is no authority to judge that. |
| Real map geometry | match | Real borders from board.json; Pacific wrap is marked at both edges. This meets the user's "I dont want a fake map". |
| Top bar: logotype, power, phase | match | play-desktop.png shows BRINK, EU with centre count, "Spring 2031 · your move". In replay the power slot shows "REPLAY · SIM-1000" instead, which fits replay mode. |
| Turn record 2031–2040, half boxes, game-turn counter, doubles as scrubber | match | Past halves hatched, a counter on the current half, step controls. In play mode the track shortens to the chosen game length. That adaptation is cited by the LENGTH option on the start screen. |
| Terrain & unit key in a corner | adaptation (uncited on size) | Present and collapsible. When open in replay it is about 675x200px and covers the map (zoom-crop.png: it hides Maghreb to the Red Sea coast). The reference's key is 256px wide. Nothing in the packet justifies the larger size. |
| Zoom controls | match | |
| Player-aid column: orders §5, Crisis track with 6/8/10 printed, supply centres, security | match | Commit orders sits at the column's foot (play-desktop.png). |
| Counters: two slots per province, power code on every counter, control markers on aligned swing states | match | For example, Turkey shows an EU army and a Gulf capital together, and code tabs mark aligned swing states. Colour is never the only signal, as PRODUCT.md's accessibility section requires. |
| Start screen | match, with defects | The buff "lid" panel plus a list of powers with their counter trios. Two problems are in material_fixes: the copy is wrong for some powers, and the mobile order is wrong. |
| Mobile start order | contradicted | start-mobile.png: "Start game as United States" comes before "Choose your power" on a phone, so the button commits a choice the player hasn't seen yet. No cited reason. |

## ceiling

There is no QUALITY BAR card, so these are measured against the world named in OWN-WORLD (1970s Avalon Hill/SPI box). Devices that world has and the build leaves out:
- The sheet's neatline: a printed border around the map, a title cartouche, and a printed edge where the Pacific wrap is marked.
- Printed area IDs or province codes on the map. The order notation (`A ROM - BER`) uses codes the board never prints.
- A terrain-effects chart in the key, i.e. what chokepoints and impassable areas do, not only what they look like.
- A force pool or counter tray for builds and losses in Winter.

## material_fixes

1. GROUND / FIRST VIEWPORT ("the map sheet fills the rest"): make the map a printed sheet. Fill sea tint to a rectangular neatline across the whole map pane, and stop pan and zoom before they show unprinted cream. On mobile, fit to the inhabited latitudes so the map fills the screen instead of sitting in about 440px of empty space. This is what the reference got right and the build didn't adopt.
2. Truth (the coordinator's note is correct): web/src/play.ts:77 says "Two fleets on two oceans and an umbrella over Japan". The start is actually F WAS, A TEX, C CAL with umbrellas over Japan and Gulf (data/provinces.yaml:302–304). play.ts:83 says the Gulf has "a Fleet on Hormuz" (the start is F DUB; in the order notation that reads like F HOR) and calls it "exposed" although the US umbrella covers it. Rewrite both. The CHN, EU, RUS, IND and JPN lines match the data. Better still, build the counts and umbrellas from the `start:` data so the text can't go stale again.
3. Contract FORM: `seed: 2795e5a2` is 8 hex characters, the same format as the CLI's raw `key:` and not a 16-hex reference. It is also unconfirmed. Replace it with the reference the roll printed, or with `seed: none (unavailable)` plus evidence that the roll really did print that.
4. Mobile start (start-mobile.png): put "Choose your power" above "Start game as …" on narrow screens, or pin the Start button to the bottom of the screen below the list.
5. Copy, against Principle 2 (plain-language results): "What happened" shows raw engine strings, for example "Crisis 6 -> 6 (baseline +1, nuclear powers clashed +1, commons -2)" and "loses 1 build(s) (sanctions 0, recession 1, accord 0)" (winter.png, play-over.png). Rewrite them as sentences in the game's own language: no ASCII arrows, no "(s)", and leave out zero-value terms.
6. Key size: make the open key compact, around the reference's 256px width, or start it collapsed after first use, so it stops covering the map at working zoom levels (zoom-crop.png).
7. Floor (Unicode glyphs used as icons): web/src/board.ts:214 adds "←" or "→" to "continues", right next to chevrons that are already drawn. Remove the arrow characters and keep the drawn chevrons.
8. "Seven powers · five nuclear" (start.png, wraps badly in start-mobile.png) repeats information each row already shows ("Nuclear" / "Not nuclear"). Delete it. Audit the other dot-joined captions ("Movement · phase 21 of 48", "Fall 2035 · Crisis 6"). Keep each only where it reads as a printed label, not as a template habit.

## keep

The die-cut counters (power code on every one, military and civil counters side by side) on the real board.json map, and the turn record serving as both the game clock and the replay scrubber.

---

# Verdict pass (post-fix recaptures)

## verdict

1. GROUND / map sheet: **resolved.** desktop.png, winter.png and play-desktop.png show sea tint printed out to a rectangular double-rule neatline, with a cartouche ("BRINK / The world, 2031 to 2040"). No cream shows inside the map pane anywhere. mobile.png now fills the phone screen with Europe and Africa; the ~440px of empty cream is gone.
2. Start-screen truth: **resolved.** start.png: USA reads "Starts with Fleet in Washington, Army in Texas and Capital in California & Alaska. Umbrella over Japan and Gulf." This matches provinces.yaml start. Gulf reads "beside the Strait of Hormuz … Starts with Fleet in Dubai … Under the United States umbrella". The other five match the data.
3. Contract FORM seed: **partial.** The line is now `seed: none (unavailable: …)`. The installed CLI's own output (seen in the review round) has the same `key:` header and no SEED REFERENCE line, which supports the claim. But the raw key `2795e5a2` still appears in FORM inside the explanation, and the procedure forbids committing the key. The line should read only `seed: none (unavailable)`, with the evidence kept outside the contract.
4. Mobile start order: **resolved.** start-mobile.png: "Choose your power" comes straight after the intro, and "Start game as United States" is pinned to the bottom of the screen.
5. Engine copy: **resolved.** winter.png: "The Crisis track rises from 5 to 6: +1 for the year, +1 because nuclear powers fought, −1 for 5 Commons points.", "United States loses 1 build to the recession.", "Ukraine is contested, so Russia gets no supply from it this Winter." No ASCII arrows, no "(s)", no zero-value terms.
6. Key size: **resolved.** The open key is now ~292px wide in two columns and sits on open sea in the South Pacific (desktop.png). When collapsed in play it is a single bar (play-desktop.png).
7. Unicode arrows: **resolved.** No ← or → characters remain in shipped strings (the only one left is in a code comment, main.ts:249). The key label now reads "Wraps around" next to the drawn chevrons.
8. Middle-dot captions: **resolved.** "Seven powers · five nuclear" is gone (start.png). The others now read "REPLAY: SIM-1000", "Movement, phase 21 of 48" and "FALL 2035, CRISIS 6" (mobile.png). The tab title "Brink · Board" is the normal title convention, so leaving it is fine.

Regressions introduced by the fix batch:
- Mobile overlays cover map content. In mobile.png the zoom box hides the MOSCOW label and the cartouche covers the Arctic Shelf and Britain. Both float over the sheet instead of sitting in a margin. On a 390px screen, the cartouche should collapse into the sheet border, or the controls should move off the map.
- Province codes added from the ceiling list are illegible at fit zoom. On desktop at fit (desktop.png), the codes next to each name ("ber", "mos", "sah") are grey at roughly 5px and read as speckle, not print. Hide them below a zoom threshold, or set them in ink at a size that can be read.
- The "YOUR MOVE" marker is now a red reversed chip in both the top bar and the panel header (play-desktop.png), so the same state is stamped twice side by side, as a label-chip that OWN-WORLD never names. Keep one, preferably the panel header.

## remaining

- Fix 3 is partial: remove the raw key from the FORM line.
- Three regressions: mobile overlays hiding map labels; province codes illegible at fit zoom; the duplicated YOUR MOVE chip.

disposition: fix
