"""A local Postgres for tests and development (never used on Vercel).

Starts an embedded server with `pgserver` (dev dependency), stands in for Supabase's `auth.users` table
and applies the migrations in supabase/migrations/:

    python -m brink.hosted.localdb [data dir]     # prints the database URL and keeps running
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "supabase" / "migrations"

# Just enough of Supabase's auth schema for the migration's foreign key and trigger.
AUTH_STUB = """
create schema if not exists auth;
create table if not exists auth.users (
  id uuid primary key,
  email text,
  raw_user_meta_data jsonb default '{}'::jsonb,
  created_at timestamptz default now()
);
"""


def apply_migrations(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(AUTH_STUB)
        for f in sorted(MIGRATIONS.glob("*.sql")):
            conn.execute(f.read_text())


def start(data_dir: str | Path, cleanup_mode: str | None = "stop"):
    """(server, url): a running embedded Postgres with the schema applied."""
    import pgserver

    srv = pgserver.get_server(str(data_dir), cleanup_mode=cleanup_mode)
    url = srv.get_uri()
    apply_migrations(url)
    return srv, url


if __name__ == "__main__":
    import time

    d = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "build" / "cache" / "pgdata")
    d.mkdir(parents=True, exist_ok=True)
    srv, url = start(d, cleanup_mode=None)
    print(url, flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
