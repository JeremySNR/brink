"""Who is calling: verify a Supabase Auth access token.

Supabase signs access tokens either with the project's asymmetric signing keys (ES256/RS256, published as a
JWKS at `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`) or, for older projects, with the shared HS256
secret (`SUPABASE_JWT_SECRET`). Both are accepted; `aud` must be "authenticated" and the token unexpired.

Dev mode (local only): with `BRINK_DEV_AUTH=1` and no `VERCEL` environment variable, a bearer token
`dev:<name>` signs in as a fake user. It cannot be switched on in a Vercel deployment.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

import jwt

AUDIENCE = "authenticated"
_jwks_clients: dict[str, jwt.PyJWKClient] = {}


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class User:
    id: str
    email: str | None = None
    dev: bool = False


def dev_auth_enabled() -> bool:
    return os.environ.get("BRINK_DEV_AUTH") == "1" and not os.environ.get("VERCEL")


def dev_user(name: str) -> User:
    name = "".join(c for c in name.lower() if c.isalnum() or c in "-_.")[:24] or "dev"
    return User(id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"brink-dev:{name}")), email=f"{name}@dev.local", dev=True)


def _jwks(url: str) -> jwt.PyJWKClient:
    if url not in _jwks_clients:
        _jwks_clients[url] = jwt.PyJWKClient(url, cache_keys=True, lifespan=600, timeout=5)
    return _jwks_clients[url]


def verify_token(token: str) -> User:
    """The user a Supabase access token belongs to, or AuthError."""
    if not token:
        raise AuthError("sign in first")
    if token.startswith("dev:"):
        if not dev_auth_enabled():
            raise AuthError("invalid access token")
        return dev_user(token[4:])
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise AuthError("invalid access token")
    alg = header.get("alg")
    try:
        if alg == "HS256":
            secret = os.environ.get("SUPABASE_JWT_SECRET")
            if not secret:
                raise AuthError("server is missing SUPABASE_JWT_SECRET")
            claims = jwt.decode(token, secret, algorithms=["HS256"], audience=AUDIENCE,
                                options={"require": ["exp", "sub"]})
        elif alg in ("ES256", "RS256"):
            base = (os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or "").rstrip("/")
            if not base:
                raise AuthError("server is missing SUPABASE_URL")
            key = _jwks(f"{base}/auth/v1/.well-known/jwks.json").get_signing_key_from_jwt(token)
            claims = jwt.decode(token, key.key, algorithms=[alg], audience=AUDIENCE,
                                options={"require": ["exp", "sub"]})
        else:
            raise AuthError("invalid access token")
    except jwt.ExpiredSignatureError:
        raise AuthError("your session has expired: sign in again")
    except jwt.PyJWTError:
        raise AuthError("invalid access token")
    try:
        uid = str(uuid.UUID(str(claims["sub"])))
    except ValueError:
        raise AuthError("invalid access token")
    return User(id=uid, email=claims.get("email"))


def bearer(header: str | None) -> str:
    if not header:
        return ""
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""
