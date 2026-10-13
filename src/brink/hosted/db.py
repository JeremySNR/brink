"""Postgres access for the hosted API.

One short-lived connection per request. On Vercel `POSTGRES_URL` is Supabase's transaction pooler
(Supavisor, port 6543), which can't keep server-side prepared statements between transactions, so they
are turned off (`prepare_threshold=None`).
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg
from psycopg.rows import dict_row

# libpq rejects unknown URI parameters; the Vercel/Supabase integration adds some (e.g. `supa=...`,
# `pgbouncer=true`), so only these are kept.
_LIBPQ_PARAMS = {
    "sslmode", "sslrootcert", "sslcert", "sslkey", "connect_timeout", "application_name", "options",
    "target_session_attrs", "keepalives", "keepalives_idle", "host", "port", "user", "password", "dbname",
}


def clean_dsn(url: str) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k in _LIBPQ_PARAMS]
    return urlunsplit(parts._replace(query=urlencode(query)))


def database_url() -> str:
    url = os.environ.get("BRINK_DATABASE_URL") or os.environ.get("POSTGRES_URL")
    if not url:
        raise RuntimeError("no database configured: set POSTGRES_URL")
    return clean_dsn(url)


@contextmanager
def connect(url: str | None = None) -> Iterator[psycopg.Connection]:
    """A connection with dict rows; the block runs in one transaction (committed on success)."""
    conn = psycopg.connect(url or database_url(), prepare_threshold=None, row_factory=dict_row,
                           connect_timeout=10, application_name="brink-api")
    try:
        with conn:          # commit on success, roll back on error, then close
            yield conn
    finally:
        if not conn.closed:
            conn.close()
