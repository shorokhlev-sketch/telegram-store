"""Shared FastAPI dependencies: DB session and Telegram WebApp initData auth.

`validate_init_data` implements Telegram's WebApp validation algorithm
(https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app):
parse the query string, pop `hash`, build the data-check-string from the
remaining fields sorted by key, derive a secret key from the bot token and
compare HMAC-SHA256 digests in constant time.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_session_factory

DEFAULT_MAX_AGE_S = 86400


class InitDataError(Exception):
    """Telegram WebApp initData is missing, malformed, tampered with, or expired."""


async def get_session() -> AsyncSession:  # type: ignore[misc]
    """FastAPI dependency yielding a request-scoped AsyncSession."""
    async with get_session_factory()() as session:
        yield session


def validate_init_data(init_data: str, bot_token: str, max_age_s: int = DEFAULT_MAX_AGE_S) -> dict:
    """Validate Telegram WebApp `initData` and return it as a dict.

    `user` (and `receiver`/`chat`, if present) are JSON-decoded. Raises
    `InitDataError` on any validation failure.
    """
    if not init_data:
        raise InitDataError("empty init data")
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise InitDataError("malformed init data") from exc

    data: dict[str, str] = {}
    for key, value in pairs:
        data[key] = value  # last occurrence wins, matches Telegram's own reference implementation

    received_hash = data.pop("hash", None)
    if not received_hash:
        raise InitDataError("missing hash")

    data_check_string = "\n".join(f"{key}={data[key]}" for key in sorted(data))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed_hash, received_hash):
        raise InitDataError("hash mismatch")

    auth_date_raw = data.get("auth_date")
    if auth_date_raw is None:
        raise InitDataError("missing auth_date")
    try:
        auth_date = int(auth_date_raw)
    except ValueError as exc:
        raise InitDataError("bad auth_date") from exc
    if time.time() - auth_date > max_age_s:
        raise InitDataError("init data expired")

    result: dict = dict(data)
    result["auth_date"] = auth_date
    for json_field in ("user", "receiver", "chat"):
        if json_field in result:
            try:
                result[json_field] = json.loads(result[json_field])
            except json.JSONDecodeError as exc:
                raise InitDataError(f"bad {json_field} json") from exc
    return result


async def optional_tg_user(
    x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
) -> dict | None:
    """None when the header is absent; the parsed initData when present and valid.

    Present but invalid -> 401.
    """
    if x_telegram_init_data is None:
        return None
    settings = get_settings()
    try:
        return validate_init_data(x_telegram_init_data, settings.bot_token)
    except InitDataError as exc:
        raise HTTPException(status_code=401, detail="invalid Telegram init data") from exc


async def require_admin(
    x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
) -> dict:
    """Valid initData for a user in ADMIN_IDS. Missing/invalid -> 401, valid non-admin -> 403."""
    if x_telegram_init_data is None:
        raise HTTPException(status_code=401, detail="missing Telegram init data")
    settings = get_settings()
    try:
        data = validate_init_data(x_telegram_init_data, settings.bot_token)
    except InitDataError as exc:
        raise HTTPException(status_code=401, detail="invalid Telegram init data") from exc
    user = data.get("user") or {}
    user_id = user.get("id")
    if user_id is None or not settings.is_admin(int(user_id)):
        raise HTTPException(status_code=403, detail="admin access required")
    return data
