-- Brink hosted multiplayer: initial schema.
--
-- Apply once (idempotent): Supabase SQL editor, or
--   psql "$POSTGRES_URL_NON_POOLING" -f supabase/migrations/0001_init.sql
--
-- Every table has row level security ENABLED and NO policies: the anon and authenticated roles (the
-- browser, via the Data API) see nothing. All access goes through the Python API (api/index.py), which
-- connects as the postgres role and bypasses RLS. See docs/HOSTING.md.


-- ------------------------------------------------------------------------------------------- profiles
create table if not exists public.profiles (
  id           uuid primary key references auth.users (id) on delete cascade,
  display_name text not null unique check (char_length(display_name) between 2 and 32),
  created_at   timestamptz not null default now()
);

-- A unique display name from an email's local part: "ada", then "ada-2", "ada-3"...
create or replace function public.brink_unique_name(base text)
returns text language plpgsql as $$
declare
  stem text := left(coalesce(nullif(regexp_replace(lower(coalesce(base, '')), '[^a-z0-9_.-]+', '', 'g'), ''), 'player'), 24);
  candidate text;
  n int := 1;
begin
  if char_length(stem) < 2 then stem := stem || '-player'; end if;
  candidate := stem;
  while exists (select 1 from public.profiles where lower(display_name) = lower(candidate)) loop
    n := n + 1;
    candidate := stem || '-' || n;
  end loop;
  return candidate;
end $$;

create or replace function public.brink_handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, public.brink_unique_name(split_part(coalesce(new.email, ''), '@', 1)))
  on conflict (id) do nothing;
  return new;
end $$;

drop trigger if exists brink_on_auth_user_created on auth.users;
create trigger brink_on_auth_user_created
  after insert on auth.users
  for each row execute function public.brink_handle_new_user();

-- ------------------------------------------------------------------------------------------- games
create table if not exists public.games (
  id             uuid primary key default gen_random_uuid(),
  name           text not null check (char_length(name) between 1 and 60),
  created_by     uuid not null references public.profiles (id) on delete cascade,
  status         text not null default 'forming'
                 check (status in ('forming', 'active', 'finished', 'cancelled')),
  visibility     text not null default 'public' check (visibility in ('public', 'private')),
  invite_code    text not null unique,
  deadline_hours int  not null default 24 check (deadline_hours in (12, 24, 48, 72)),
  press          text not null default 'full' check (press in ('full', 'none')),
  last_year      int  not null default 2040 check (last_year between 2032 and 2040),
  bot_kind       text not null default 'search',
  seed           bigint not null,
  state          jsonb,          -- Game.to_state() WITHOUT "phases" (they live in game_phases)
  step           int  not null default 0,
  pending        jsonb,          -- open decision point: {kind, phase, step, powers, position, events, chair?, proposal?}
  phase_name     text,
  deadline       timestamptz,
  result         jsonb,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  started_at     timestamptz,
  finished_at    timestamptz
);

create index if not exists games_lobby_idx on public.games (created_at desc)
  where status = 'forming' and visibility = 'public';
create index if not exists games_deadline_idx on public.games (deadline) where status = 'active';

-- Append-only: state["phases"] is reassembled from these rows, ordered by idx.
create table if not exists public.game_phases (
  game_id uuid not null references public.games (id) on delete cascade,
  idx     int  not null,
  record  jsonb not null,
  primary key (game_id, idx)
);

create table if not exists public.game_seats (
  game_id   uuid not null references public.games (id) on delete cascade,
  power     text not null,
  user_id   uuid references public.profiles (id) on delete set null,   -- null: open, then a bot once started
  joined_at timestamptz,
  missed    int  not null default 0,                                   -- deadlines passed without orders
  primary key (game_id, power),
  unique (game_id, user_id)
);
create index if not exists game_seats_user_idx on public.game_seats (user_id) where user_id is not null;

-- One row per (power, decision point). Saved drafts count at the deadline; `ready` resolves early.
create table if not exists public.submissions (
  game_id    uuid not null references public.games (id) on delete cascade,
  power      text not null,
  step       int  not null,
  orders     jsonb not null default '[]'::jsonb,
  ready      boolean not null default false,
  updated_at timestamptz not null default now(),
  primary key (game_id, power, step)
);

create table if not exists public.messages (
  id          bigint generated always as identity primary key,
  game_id     uuid not null references public.games (id) on delete cascade,
  sender_power text not null,
  recipients  text[] not null,     -- sorted, includes the sender
  body        text not null check (char_length(body) between 1 and 2000),
  phase_name  text,
  created_at  timestamptz not null default now()
);
create index if not exists messages_game_idx on public.messages (game_id, id);

create table if not exists public.notifications (
  id         bigint generated always as identity primary key,
  user_id    uuid not null references public.profiles (id) on delete cascade,
  game_id    uuid references public.games (id) on delete cascade,
  kind       text not null,
  body       text not null,
  read_at    timestamptz,
  created_at timestamptz not null default now()
);
create index if not exists notifications_user_idx on public.notifications (user_id, id desc);

-- ------------------------------------------------------------------------------------------- lock down
alter table public.profiles      enable row level security;
alter table public.games         enable row level security;
alter table public.game_phases   enable row level security;
alter table public.game_seats    enable row level security;
alter table public.submissions   enable row level security;
alter table public.messages      enable row level security;
alter table public.notifications enable row level security;

-- The helper functions are for the trigger only: nobody calls them through the Data API.
revoke execute on function public.brink_unique_name(text) from public;
revoke execute on function public.brink_handle_new_user() from public;
do $$ begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke execute on function public.brink_unique_name(text) from anon, authenticated;
    revoke execute on function public.brink_handle_new_user() from anon, authenticated;
  end if;
end $$;

-- ------------------------------------------------------------------------------------------- deadlines
-- Every minute, Supabase calls the API's idempotent tick (it advances every game past its deadline).
-- Not applied automatically: enable the pg_cron and pg_net extensions (Database > Extensions), then
-- run the statements below once in the SQL editor. Replace the host for a different deployment.
--
-- create extension if not exists pg_cron;
-- create extension if not exists pg_net;
-- select cron.schedule(
--   'brink-tick',
--   '* * * * *',
--   $$ select net.http_post(url := 'https://www.brinkgame.app/api/cron/tick',
--                           headers := '{"Content-Type": "application/json"}'::jsonb,
--                           body := '{}'::jsonb,
--                           timeout_milliseconds := 55000) $$
-- );
--
-- To stop it: select cron.unschedule('brink-tick');
