# Brink board viewer

Static Vite + TypeScript site that draws `public/board.json` and replays a game record.

```sh
# from the repo root: regenerate the board (writes build/map.json, web/public/board.json + map.json)
.venv/bin/python -m brink.geo.build
cd web
npm install
npm run sync-map   # only needed if build/map.json changed without re-running the pipeline
npm run dev        # or: npm run build && npm run preview
```

URL parameters: `?game=<url>` loads another game record (default `sample-game.json`),
`?theme=light|dark` forces a theme, `?focus=<province id>` zooms to a province, `#S2032M` opens a phase.
Keyboard: ←/→ phases, Home/End, +/− zoom, 0 fit, Esc clears a selection.
