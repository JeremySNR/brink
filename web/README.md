# Brink board viewer

Static Vite + TypeScript site that draws `public/board.json` and replays a game record, and (with
`brink serve`) lets you play a game against six bots.

```sh
# from the repo root: regenerate the board (writes build/map.json, web/public/board.json + map.json)
.venv/bin/python -m brink.geo.build
cd web
npm install
npm run sync-map   # only needed if build/map.json changed without re-running the pipeline
npm run dev        # or: npm run build && npm run preview
```

## Replay

URL parameters: `?game=<url>` loads another game record (default `sample-game.json`),
`?theme=light|dark` forces a theme, `?focus=<province id>` zooms to a province, `#S2032M` opens a phase.
Keyboard: ←/→ phases, Home/End, +/− zoom, 0 fit, Esc clears a selection.

## Play

`.venv/bin/brink serve` serves the built site (`web/dist`) and the play API on http://127.0.0.1:8765.
Open `/?play` to choose a power, or `/?play=<game id>` to resume a game the server still holds.
For UI work, run `brink serve` and `npm run dev` side by side: the Vite dev server proxies `/api` to
port 8765 (`BRINK_API=http://host:port npm run dev` to change it), so `http://localhost:5173/?play` plays.

The board shows the game's phases followed by a live phase, **Now**: the current position with your
drafted orders drawn as arrows. `src/play.ts` (PlayController) owns that phase; `main.ts` delegates unit
and province clicks, keys and the panel to it while the live phase is shown.

| Decision | On the board | Keys |
| --- | --- | --- |
| Movement | click a unit, then a highlighted province (its own: hold); bar buttons for Support, Convoy, Commons, Sanction…, Disrupt, Close… | S, C, D, H, ⌫ clears, Esc |
| Retreat | click the dislodged unit, then a highlighted province; Disband | X |
| Build | click a highlighted province, then the unit type | A F C I |
| Disband | click your units to mark them | |
| Assembly | proposal (as chair) and vote in the panel | Y N V |
| Umbrellas | WITHDRAW (movement) and next year's umbrellas in the panel | |

Enter commits the open decision (or, while reviewing results, jumps to it). Drafts are validated by the
server as you go (`/validate`); void orders show their reason in the panel. Unsent drafts survive a reload
(localStorage). `window.__brink` exposes the controller for automated UI checks (`texts()`, `pending`,
`host.view`).
