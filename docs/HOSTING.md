# Hosted multiplayer

Brink's hosted game: user accounts, persistent games with deadlines, a lobby and invite links, bots in
empty seats, press between players, and every phase kept for replay. It runs on **Vercel** (the static
Vite client plus one Python function) and **Supabase** (Postgres and Auth).

```
browser ──── static client (web/dist) ───────────────── Vercel CDN
   │  Supabase Auth (email magic link) ──────────────── Supabase Auth
   │
   └─ /api/*  Authorization: Bearer <access token> ──── Vercel Python function (api/index.py, FastAPI)
                                                            │  brink.hosted.service + the engine (brink.session)
                                                            └── Postgres (Supabase, transaction pooler)
Supabase pg_cron (every minute) ── POST /api/cron/tick ───┘   + Vercel cron (daily backstop)
```

- **The browser never reads tables.** It signs in with Supabase Auth and sends the access token to the
  API. Every table has row level security on and no policies, so the anon and authenticated roles see
  nothing through Supabase's Data API; the API connects as the postgres role, which bypasses RLS.
- **The API** (`src/brink/hosted/`) is small: `db.py` (psycopg 3, one short-lived connection per request,
  no server-side prepared statements because of the transaction pooler), `auth.py` (token verification),
  `service.py` (everything the endpoints do), `app.py` (FastAPI routes). `api/index.py` puts `src/` on the
  path and exposes the app; `vercel.json` rewrites `/api/*` to it and bundles `src/brink/**` and
  `build/map.json` with the function.
- **The engine** is `brink.session` (docs/ARCHITECTURE.md, "Hosted play"): a game is a JSON state advanced
  one decision point at a time. `games.state` holds the state without its phases; the phases live in the
  append-only `game_phases` table and are reassembled when the engine needs them.

## Data model (`supabase/migrations/0001_init.sql`, `0002_email.sql`)

| Table | What it holds |
| --- | --- |
| `profiles` | one per Supabase user: unique `display_name` (a trigger on `auth.users` makes it from the email's local part) |
| `games` | name, creator, `status` forming / active / finished / cancelled, `visibility`, `invite_code`, `deadline_hours` (12/24/48/72), `press` full / none, `last_year`, `bot_kind`, `seed`, `state` (no phases), `step`, `pending` (the open decision: kind, phase, powers, live position), `phase_name`, `deadline`, `result` |
| `game_phases` | `(game_id, idx)` → game-record phase; append-only |
| `game_seats` | `(game_id, power)` → `user_id` (null: open while forming, a bot once started), `missed` deadlines |
| `submissions` | `(game_id, power, step)` → `orders`, `ready`; drafts are saved here as the player edits |
| `messages` | press: `sender_power`, `recipients` (sorted, includes the sender), `body` ≤ 2000, `phase_name` |
| `notifications` | in-app notices (`turn`, `joined`, `started`, `message`, `finished`, `cancelled`) |
| `email_log` | one row per turn email `(user_id, game_id, step, kind)` (unique; kind `due` / `reminder`), `status` queued / sent / failed, `error`, Resend `provider_id`, `sent_at` |

`profiles.email_notifications` (default true) is the turn-email preference (0002).

## How a game moves

1. **Forming.** The creator makes a game and takes a seat; others join from the lobby (public games) or
   the invite link (`#/join/<code>`, required for private games). Seats can be switched until the start.
2. **Start** (creator only, at least one player): empty seats become bots (`bot_kind`, "search"), the
   engine makes a new game, bots play any decision points that involve no human, and the first deadline
   is set to now + `deadline_hours`.
3. **Each decision point** (orders, retreats, the Assembly proposal, the Winter): involved players draft
   orders on the board; drafts save to `submissions` as they go. **Ready** marks a submission final; when
   every involved human is ready, the phase resolves immediately.
4. **Deadlines.** At the deadline the phase resolves with whatever is saved, ready or not. A player with
   nothing saved is in civil disorder (units hold, dislodged units disband, no proposal, abstain, umbrellas
   kept) and their seat's `missed` count goes up. Three things trigger a due game:
   - `/api/cron/tick`, every minute from Supabase pg_cron (below), and daily from the Vercel cron;
   - any `GET /api/games/<id>` of a game past its deadline (lazy);
   - `submit` with `ready` (the all-ready case).
5. **Advancing** is one function (`service.advance_game`) shared by all three. It reads the state and the
   submissions, runs `session.advance(..., expect_step=step)` and `run_bots_until_human` outside any
   transaction, then writes in one transaction: `UPDATE games ... WHERE id = $1 AND step = $old` (the
   optimistic lock), the new `game_phases` rows, `missed` counts and notifications. If the update matches
   no row, someone else advanced first and nothing is written. Every call is idempotent.
6. **Finished** games keep their result and every phase; the board replays them.

## API

All JSON; errors are `{"error": "..."}` with a 4xx/5xx status. Signed-in endpoints need
`Authorization: Bearer <Supabase access token>`.

| Endpoint | Auth | |
| --- | --- | --- |
| `GET /api/health` | – | `{ok, db}` |
| `GET /api/config` | – | public client config: Supabase URL and anon key (fallback when the build has none) |
| `GET /api/me`, `PUT /api/me {display_name}` | yes | profile |
| `GET /api/lobby` | – | public forming games |
| `GET /api/games/mine` | yes | my games, whose-turn first (`my_turn`: my power is in the pending decision and not ready) |
| `POST /api/games {name, visibility, deadline_hours, press, last_year, power\|null}` | yes | create a forming game, creator seated |
| `GET /api/invite/{code}` | – | game summary for an invite link |
| `POST /api/games/{id}/join {power\|null, code}` | yes | take (or switch to) a seat while forming |
| `POST /api/games/{id}/leave` · `/cancel` · `/start` | yes | forming only; cancel and start are the creator's |
| `GET /api/games/{id}?since=<phase idx>` | optional | meta, seats (names, ready ticks, never orders), pending summary, my decision options, my submission, deadline, phases since idx, result; advances first if past the deadline |
| `POST /api/games/{id}/validate {orders}` | yes | per-order `{text, ok, reason}` |
| `POST /api/games/{id}/submit {step, orders, ready, since?}` | yes | upsert my submission for that step (409 if the step has moved on); advances when everyone is ready |
| `GET /api/games/{id}/messages?after=<id>` · `POST {recipients, body}` | yes | press (full-press games): only messages where my power is a recipient; recipients must be other human players |
| `GET /api/notifications` · `POST /api/notifications/read {ids?}` | yes | notices, mark read (all when no ids) |
| `GET\|POST /api/cron/tick` | – | advance every game past its deadline, then send deadline reminders; safe to call any time |
| `GET /api/email/unsubscribe?t=<token>` | token | the "Stop turn emails" link: turns them off, shows a confirmation page (with a button to turn them back on) |
| `POST /api/email/unsubscribe?t=<token>` | token | one-click unsubscribe (RFC 8058, `List-Unsubscribe=One-Click`); `action=resubscribe` turns them back on |

`GET /api/me` includes `email_notifications`; `PUT /api/me` takes `display_name` and/or `email_notifications`.

## Turn emails

Two emails, nothing else (no game-started, game-over or press emails; sign-in links come from Supabase):

- **"Your orders are due"**: when a decision point opens that involves the player's power (the first
  Spring at the start, each new movement phase, retreats, the Assembly proposal when they chair, the
  Winter). One per player per decision point; not sent to a player already Ready for that step, and
  never to bots. Sent by `service.start` and `service.advance_game` *after* the advance commits (a
  rolled-back advance sends nothing).
- **Deadline reminder**: from the per-minute tick, to each human in the open decision who hasn't marked
  Ready once the deadline is within 6 hours (3 hours for 12-hour games). Once per player per step. It
  says whether they have orders saved (they count at the deadline anyway).

`src/brink/hosted/email.py` builds them from real game data (the open decision, the last phase's events and
the player's order results, the Crisis track, their centres) and sends them through Resend's batch
endpoint (`POST https://api.resend.com/emails/batch`, up to 100 per call, 8 s timeout).
`email_templates.py` prints them in the Wargame Box design (table layout, inline styles, 600px, no images;
HTML and plain text). Dedupe: the `email_log` row is inserted and committed *before* sending with
`on conflict do nothing`, so concurrent ticks or requests send once. A row left `queued` (the function died
mid-send) or `failed` is not retried. Without `RESEND_API_KEY` (or a signing secret) nothing is sent and
nothing is logged to the table; sending failures are recorded on the row and never break advancing.

Every email carries a "Stop turn emails" link (an HMAC-signed token, no expiry) and `List-Unsubscribe` /
`List-Unsubscribe-Post` headers; the account page (`#/me`) has the same toggle.

Preview: `brink email-preview --out runs/email-preview/` renders every email (and the Supabase templates
and the unsubscribe page) from a sample engine game to HTML, text and PNGs at 700 and 375px (headless
Chrome), plus a `-fallback` variant without the web fonts. `--review .impeccable/review` copies the key
screenshots there.

## Environment variables (names only)

Set by the Vercel ↔ Supabase integration. The function reads:

- `POSTGRES_URL`: the transaction pooler (port 6543). Unknown URI parameters the integration adds are
  stripped before connecting.
- `SUPABASE_URL`: for the JWKS at `{SUPABASE_URL}/auth/v1/.well-known/jwks.json` (ES256/RS256 tokens).
- `SUPABASE_JWT_SECRET`: only for HS256 tokens (projects still on the legacy shared secret).
- `SUPABASE_ANON_KEY` (or `NEXT_PUBLIC_SUPABASE_ANON_KEY`): served by `/api/config`.

The client build (Vite, `envPrefix: ["VITE_", "NEXT_PUBLIC_"]`) inlines only:

- `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` (or `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`).

Turn emails (set in Vercel → Settings → Environment Variables, Production):

- `RESEND_API_KEY`: Resend API key. Unset: no turn emails (everything else works).
- `BRINK_EMAIL_FROM`: the sender, e.g. `Brink <turns@your-domain>`. Default `Brink <onboarding@resend.dev>`,
  Resend's test sender, which **only delivers to the Resend account owner's own address** until a domain
  is verified in Resend (Domains → Add, then the DNS records). Friends get no email before that.
- `BRINK_SITE_URL`: where links point. Default `https://brink-sepia.vercel.app`.
- `BRINK_EMAIL_SECRET`: signs the stop links (any long random string). Falls back to
  `SUPABASE_JWT_SECRET`; with neither, no turn emails are sent. Changing it breaks old stop links.

Not used anywhere: `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_SECRET_KEY` (the API needs no Supabase admin
access). Local development only: `BRINK_DATABASE_URL` (overrides `POSTGRES_URL`), `BRINK_DEV_AUTH=1`.

`BRINK_DEV_AUTH=1` lets a bearer token `dev:<name>` sign in as a fake user. It only works when the `VERCEL`
environment variable is absent, so it cannot be switched on in a deployment.

## Going live

1. **Apply the migrations** in order (each is idempotent), in the Supabase SQL editor (paste the file) or:

   ```sh
   psql "$POSTGRES_URL_NON_POOLING" -f supabase/migrations/0001_init.sql
   psql "$POSTGRES_URL_NON_POOLING" -f supabase/migrations/0002_email.sql
   ```

2. **Auth URL configuration** (Supabase dashboard → Authentication → URL Configuration):
   - Site URL: `https://brink-sepia.vercel.app`
   - Redirect URLs:
     - `https://brink-sepia.vercel.app/**`
     - `https://brink-*-jeremys-projects-8dbe961d.vercel.app/**` (preview deployments)
     - `http://localhost:5173/**` and `http://127.0.0.1:5173/**` (local dev)

   Email provider on, magic links allowed (the default). The client uses the implicit flow, so a link opened
   in another browser or device signs that browser in. Supabase's built-in mailer is rate-limited (a few
   emails an hour); for a real playgroup, set a custom SMTP sender under Authentication → Emails.

   **Email templates** (Authentication → Emails → Templates): paste the files from `supabase/templates/`
   (they use Supabase's Go-template variables `{{ .ConfirmationURL }}`, `{{ .Email }}`, `{{ .SiteURL }}`):

   | Template | File | Subject |
   | --- | --- | --- |
   | Magic link | `supabase/templates/magic_link.html` | `Your Brink sign-in link` |
   | Confirm signup | `supabase/templates/confirm_signup.html` | `Confirm your email for Brink` |

   They are generated from `email_templates.auth_email()`; a test fails if the files fall behind it.

   **SMTP through Resend** (once a domain is verified there): Authentication → Emails → SMTP settings, enable
   custom SMTP: host `smtp.resend.com`, port `465`, username `resend`, password = the Resend API key,
   sender address on the verified domain, sender name `Brink`. This lifts the built-in mailer's limits.

3. **Deadline tick every minute.** Enable the `pg_cron` and `pg_net` extensions (Database → Extensions),
   then run once in the SQL editor:

   ```sql
   select cron.schedule(
     'brink-tick',
     '* * * * *',
     $$ select net.http_post(url := 'https://brink-sepia.vercel.app/api/cron/tick',
                             headers := '{"Content-Type": "application/json"}'::jsonb,
                             body := '{}'::jsonb,
                             timeout_milliseconds := 55000) $$
   );
   -- stop it: select cron.unschedule('brink-tick');
   ```

   The same SQL is at the end of the migration, commented out. The daily Vercel cron in `vercel.json` is
   a backstop (Vercel crons run on production deployments only), and any visit to a game past its
   deadline advances it too.

4. **Turn emails**: set `RESEND_API_KEY`, `BRINK_EMAIL_SECRET` (and `BRINK_EMAIL_FROM` once a domain is
   verified) in Vercel, then redeploy. Until the domain is verified, only the Resend account owner gets them.

5. **Merge into `main`.** Vercel deploys production from `main`.

## Packaging notes

Vercel's Python builder reads the dependency and Python-version files nearest the entrypoint first. The
repository root has `uv.lock` and `.python-version` (3.11, which the MILA test oracle needs, but Vercel's
runtime doesn't offer), so the function has its own `api/requirements.txt` and `api/.python-version`
(3.12). Keep `api/requirements.txt` in step with the `hosted` extra in `pyproject.toml`. The engine's tests
run on 3.12 too.

## Local development

Without Supabase (an embedded Postgres and fake users):

```sh
uv sync --extra geo --extra hosted                       # once (dev group: pgserver, uvicorn, httpx)
.venv/bin/python -m brink.hosted.localdb                 # starts Postgres in build/cache/pgdata, applies the
                                                         # migration, prints its URL; leave it running
BRINK_DEV_AUTH=1 BRINK_DATABASE_URL='<that URL>' \
  .venv/bin/uvicorn brink.hosted.app:app --app-dir src --port 8799
cd web && BRINK_API=http://127.0.0.1:8799 npm run dev    # http://localhost:5173/
```

Sign in from `#/signin` ("Dev sign-in"), or open `http://localhost:5173/?dev_user=ada#/`; a second
browser profile (or `?dev_user=bob`) is the second player. `POST /api/cron/tick` advances games whose
deadline has passed; to force one, set its `deadline` in the past in SQL.

Against the real Supabase project:

```sh
vercel link && vercel env pull .env.local                # never commit it (.gitignore has .env*)
set -a; source .env.local; set +a
.venv/bin/uvicorn brink.hosted.app:app --app-dir src --port 8799
cd web && BRINK_API=http://127.0.0.1:8799 npm run dev    # the client gets the Supabase URL/key from /api/config
```

Or `vercel dev`, which runs the function and the client together with the project's environment.

Tests: `.venv/bin/pytest -q tests/test_hosted.py tests/test_email.py` runs the service against an embedded Postgres
(`pgserver`) with HS256 tokens signed by a test secret: create, join, invite, start, submit, ready-advance,
deadline tick, civil disorder and `missed`, the optimistic-lock race, press visibility, notifications,
auth rejections, a game run to its end, and RLS on every table.

## Known gaps

- Email covers turns only (due and reminder); no push notifications. `service.notify()` records in-app
  notices; other senders can be appended to `service.NOTIFIERS` (they run inside the transaction).
- Polling, not Supabase Realtime: the game page every 15 s (5 s for two minutes after you act), press every
  15 s, My games every 30 s.
- No kicking or replacing absent players; a seat in civil disorder keeps its `missed` count only.
- No rate limiting beyond Supabase Auth's own; the tick endpoint is public by design (it only advances
  games that are due).
- Bots are rebuilt every decision point (`brink.session.make_bots`), so the sims' evolving alliances
  aren't carried into hosted games.
