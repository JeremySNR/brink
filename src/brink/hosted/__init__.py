"""Hosted multiplayer: accounts, persistent games with deadlines, press. See docs/HOSTING.md.

    db.py       Postgres connections (psycopg 3, transaction-pooler safe)
    auth.py     Supabase access-token verification (JWKS or HS256), dev-only bypass
    service.py  the game service: lobby, seats, submissions, advancing, messages, notifications
    email.py    turn emails (due, deadline reminder) through Resend, deduped in email_log
    email_templates.py  the emails (and Supabase Auth templates) in the Wargame Box design
    app.py      the FastAPI app served by api/index.py on Vercel
"""
