# Brink

A seven-player game of great-power competition in a fictional 2030s, built on Diplomacy's engine: secret simultaneous orders, units of equal strength, no dice. The United States, China, the European Union, Russia, India, Japan and the Gulf compete to align twenty swing states, and a shared Crisis track can end the game with everyone losing.

What makes it different from classic Diplomacy:

- **Two layers of power.** Every land province has a military slot (Armies, Fleets) and a civil slot (Capital, Influence). A swing state aligns to whoever holds both.
- **Nuclear deterrence.** Nuclear powers' homes can't be attacked, so competition runs through swing states and proxy wars. Japan and the Gulf rely on US umbrellas, which can be withdrawn in secret.
- **The Crisis track.** Closing chokepoints and wars between nuclear powers push it up; Commons orders pull it down. At 10 the game ends and everyone scores zero.
- **Balanced by simulation.** Rules and starting positions were tuned over tens of thousands of bot games; every power ends within about one centre of the others.

## Play with friends

https://brink-sepia.vercel.app hosts multiplayer games: sign in with your email, start a game, send the
invite link. Each friend takes a power and bots play the empty seats; orders are due by a deadline (12 to
72 hours a phase), and a phase resolves as soon as everyone is ready. Private press between players, and
every phase replays on the board. How it's built and deployed: [docs/HOSTING.md](docs/HOSTING.md).

## Play in the browser, locally

You against six bots:

```sh
uv sync --extra geo                       # Python 3.11 environment
(cd web && npm install && npm run build)  # once, and after web changes
.venv/bin/brink serve --port 8790         # then open http://127.0.0.1:8790/?play
```

Pick a power and start. `brink serve --power USA --seed 7` starts a game right away and prints its link; `--last-year 2034` makes a short game; `--alliances` lets the bots ally with each other (you never have allies); records are written to `runs/play/<id>.json`.

Click one of your counters, then a highlighted province to move there (its own province to hold). With a unit selected, **S** then the unit to support then its destination, **C** for a convoy, **D** to disrupt, **H** to hold; Commons, Sanction and Close are buttons in the bar over the map; Esc cancels; **Enter** commits. After each commit the turn record steps through what happened before your next decision. Retreats, builds, the Assembly and nuclear umbrellas are decided on the board or in the side column.

## What's here

| Path | What it is |
| --- | --- |
| `data/provinces.yaml` | The board: provinces, sea zones, chokepoints, trade routes, starting position |
| `src/brink/geo/` | Builds the board from Natural Earth data: geometry, sea zones, adjacency |
| `src/brink/` | Rules engine (two-layer adjudicator, Winter, Assembly, scoring), bots, simulator, local server |
| `src/brink/hosted/`, `api/`, `supabase/` | Hosted multiplayer: the API (FastAPI on Vercel), its Postgres schema |
| `tests/` | Engine tests, including differential tests against MILA's `diplomacy` adjudicator and the DATC cases |
| `web/` | The board and play client (Vite + TypeScript) |
| `docs/ARCHITECTURE.md` | Engine semantics and file formats |
| `docs/HOSTING.md` | Hosted multiplayer: architecture, API, deployment, local development |
| `DESIGN.md`, `PRODUCT.md` | The visual design system and product brief |

```sh
.venv/bin/pytest -q                      # engine tests
.venv/bin/brink sim --games 2000         # balance simulation with search bots
.venv/bin/brink analyse                  # static reach and exposure analysis of the map
.venv/bin/python -m brink.geo.build      # rebuild the board from data/provinces.yaml
```

Map data: [Natural Earth](https://www.naturalearthdata.com/) (public domain).
