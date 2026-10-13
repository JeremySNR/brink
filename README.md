# Brink

A modern great-power variant of Diplomacy: rules engine, bots, simulations and board viewer.

## Play in the browser

You against six bots:

```sh
uv sync                                   # or: .venv already set up
(cd web && npm install && npm run build)  # once, and after web changes
.venv/bin/brink serve                     # -> http://127.0.0.1:8765/?play
```

Open the URL, pick a power and start. `brink serve --power USA --seed 7` starts a game right away
and prints its link; `--last-year 2034` makes a short game; `--alliances` lets the bots ally with each
other (you never have allies); records are written to `runs/play/<id>.json`.

Ordering works like Backstabbr: click one of your units, then click a highlighted province to move there
(its own province: hold). With a unit selected, **S** then the unit to support then its destination (its own
province for a hold support), **C** for a convoy (Army, then destination), **D** to disrupt, **H** to hold;
COMMONS, SANCTION and CLOSE are buttons in the bar over the map; Esc cancels; **Enter** commits. After each
commit the board steps through what happened (the bots' phases included) before your next decision.
Retreats, builds/disbands, the Assembly (proposal when you chair, vote YES/NO/VETO) and nuclear umbrellas
are decided the same way, on the board or in the panel.

The API (`src/brink/server.py`) is small JSON over HTTP: `POST /api/games {power, seed?, alliances?, last_year?}`,
`GET /api/games/{id}` (record in the game.json format + the pending decision with every legal option),
`POST /api/games/{id}/validate {orders}` and `POST /api/games/{id}/orders {orders, decision}`.
