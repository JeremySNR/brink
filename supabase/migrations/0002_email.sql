-- Brink hosted multiplayer: turn emails ("your orders are due", deadline reminders).
--
-- Apply once after 0001 (idempotent): Supabase SQL editor, or
--   psql "$POSTGRES_URL_NON_POOLING" -f supabase/migrations/0002_email.sql
--
-- Like every table, email_log has row level security ENABLED and NO policies: only the API (the postgres
-- role) reads or writes it. Players' addresses are read from auth.users.email by the API; nothing is copied.

-- ------------------------------------------------------------------------------------------- preference
-- On by default; the "Stop turn emails" link in every email and the account page turn it off.
alter table public.profiles add column if not exists email_notifications boolean not null default true;

-- ------------------------------------------------------------------------------------------- email_log
-- One row per (player, game, decision point, kind), inserted and committed BEFORE the email is sent, with
-- on conflict do nothing: concurrent ticks or requests can't send the same email twice.
create table if not exists public.email_log (
  id          bigint generated always as identity primary key,
  user_id     uuid not null references public.profiles (id) on delete cascade,
  game_id     uuid not null references public.games (id) on delete cascade,
  step        int  not null,                                   -- games.step: the decision point
  kind        text not null check (kind in ('due', 'reminder')),
  status      text not null default 'queued' check (status in ('queued', 'sent', 'failed')),
  error       text,
  provider_id text,                                            -- Resend's email id
  created_at  timestamptz not null default now(),
  sent_at     timestamptz,
  unique (user_id, game_id, step, kind)
);
create index if not exists email_log_game_idx on public.email_log (game_id, step);

alter table public.email_log enable row level security;
