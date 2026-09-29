import hashlib
import hmac
import json
import time
from urllib.parse import quote

import pytest
from fastapi import HTTPException

from app.api import deps
from app.config import get_settings

BOT_TOKEN = "123456:TEST"


def _sign(fields: dict) -> str:
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()


def make_init_data(user: dict, *, auth_date: int | None = None, extra: dict | None = None) -> str:
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAQ_test",
        "user": json.dumps(user, separators=(",", ":")),
    }
    if extra:
        fields.update(extra)
    fields["hash"] = _sign(fields)
    return "&".join(f"{k}={quote(v, safe='')}" for k, v in fields.items())


ADMIN_USER = {"id": 111, "first_name": "Lev", "username": "lev"}
NON_ADMIN_USER = {"id": 999, "first_name": "Stranger"}


def test_valid_init_data_round_trips_user():
    raw = make_init_data(ADMIN_USER)
    data = deps.validate_init_data(raw, BOT_TOKEN)
    assert data["user"] == ADMIN_USER
    assert data["auth_date"] > 0


def test_tampered_hash_is_rejected():
    raw = make_init_data(ADMIN_USER)
    tampered = raw.replace("query_id=AAQ_test", "query_id=AAQ_evil")
    with pytest.raises(deps.InitDataError):
        deps.validate_init_data(tampered, BOT_TOKEN)


def test_wrong_bot_token_is_rejected():
    raw = make_init_data(ADMIN_USER)
    with pytest.raises(deps.InitDataError):
        deps.validate_init_data(raw, "999999:OTHER")


def test_expired_init_data_is_rejected():
    old = int(time.time()) - 90000  # > 86400s default max age
    raw = make_init_data(ADMIN_USER, auth_date=old)
    with pytest.raises(deps.InitDataError):
        deps.validate_init_data(raw, BOT_TOKEN)


def test_missing_user_still_validates_when_hash_matches():
    fields = {"auth_date": str(int(time.time())), "query_id": "AAQ_no_user"}
    fields["hash"] = _sign(fields)
    raw = "&".join(f"{k}={quote(v, safe='')}" for k, v in fields.items())
    data = deps.validate_init_data(raw, BOT_TOKEN)
    assert "user" not in data


def test_missing_hash_is_rejected():
    with pytest.raises(deps.InitDataError):
        deps.validate_init_data("auth_date=123&user=%7B%7D", BOT_TOKEN)


def test_empty_init_data_is_rejected():
    with pytest.raises(deps.InitDataError):
        deps.validate_init_data("", BOT_TOKEN)


async def test_optional_tg_user_returns_none_when_header_absent():
    assert await deps.optional_tg_user(None) is None


async def test_optional_tg_user_raises_401_when_header_invalid():
    with pytest.raises(HTTPException) as exc:
        await deps.optional_tg_user("bad=data&hash=deadbeef")
    assert exc.value.status_code == 401


async def test_optional_tg_user_returns_data_when_valid():
    get_settings.cache_clear()
    raw = make_init_data(ADMIN_USER)
    data = await deps.optional_tg_user(raw)
    assert data["user"]["id"] == 111


async def test_require_admin_raises_401_when_missing():
    with pytest.raises(HTTPException) as exc:
        await deps.require_admin(None)
    assert exc.value.status_code == 401


async def test_require_admin_raises_401_when_invalid():
    with pytest.raises(HTTPException) as exc:
        await deps.require_admin("bad=data&hash=deadbeef")
    assert exc.value.status_code == 401


async def test_require_admin_raises_403_for_non_admin():
    get_settings.cache_clear()
    raw = make_init_data(NON_ADMIN_USER)
    with pytest.raises(HTTPException) as exc:
        await deps.require_admin(raw)
    assert exc.value.status_code == 403


async def test_require_admin_passes_for_admin():
    get_settings.cache_clear()
    raw = make_init_data(ADMIN_USER)
    data = await deps.require_admin(raw)
    assert data["user"]["id"] == 111
