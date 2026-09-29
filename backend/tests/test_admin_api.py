from __future__ import annotations

import json

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import get_settings
from app.db.base import Base
from app.db.models import ChannelMessage, Item, ItemTag, MessageKind, Tag
from app.db.session import get_engine, get_session_factory
from app.main import app
from app.services import tags as tags_service
from app.services.drafts import now_utc
from tests.test_init_data import ADMIN_USER, NON_ADMIN_USER, make_init_data

PUBLIC_ID = "ADMIT001"
CHAT_ID = -1001234567890


class FakeAdminBot(Bot):
    """Records edit/delete calls; can be told to fail a specific message id
    with an arbitrary Telegram error (not just "message is not modified")."""

    def __init__(self) -> None:
        super().__init__(token="123456:TEST")
        self.edited_captions: list[dict] = []
        self.edited_media: list[dict] = []
        self.deleted_ids: list[int] = []
        self.not_modified_ids: set[int] = set()
        self.broken_ids: set[int] = set()

    async def edit_message_caption(self, *, chat_id=None, message_id=None, caption=None, parse_mode=None, reply_markup=None, **kwargs):  # type: ignore[override]
        if message_id in self.not_modified_ids:
            raise TelegramBadRequest(method=None, message="Bad Request: message is not modified")
        if message_id in self.broken_ids:
            raise TelegramBadRequest(method=None, message="Bad Request: chat not found")
        self.edited_captions.append(
            {"chat_id": chat_id, "message_id": message_id, "caption": caption, "parse_mode": parse_mode, "reply_markup": reply_markup}
        )
        return True

    async def edit_message_media(self, media, chat_id=None, message_id=None, reply_markup=None, **kwargs):  # type: ignore[override]
        if message_id in self.not_modified_ids:
            raise TelegramBadRequest(method=None, message="Bad Request: message is not modified")
        if message_id in self.broken_ids:
            raise TelegramBadRequest(method=None, message="Bad Request: chat not found")
        self.edited_media.append({"chat_id": chat_id, "message_id": message_id, "media": media, "reply_markup": reply_markup})
        return True

    async def delete_message(self, chat_id, message_id, **kwargs):  # type: ignore[override]
        self.deleted_ids.append(message_id)
        return True

    async def send_message(self, chat_id, text, parse_mode=None, **kwargs):  # type: ignore[override]
        return None


@pytest.fixture(autouse=True)
def _no_webhook(monkeypatch):
    monkeypatch.setenv("SET_WEBHOOK_ON_START", "0")
    get_settings.cache_clear()
    yield


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        bot = FakeAdminBot()
        app.state.bot = bot
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c, bot


async def _add_item(
    public_id: str = PUBLIC_ID,
    *,
    status: str = "active",
    price: int = 75_000,
    with_channel_message: bool = True,
) -> Item:
    factory = get_session_factory()
    async with factory() as session:
        item = Item(
            public_id=public_id,
            status=status,
            created_by=111,
            category="iphone",
            device_type="phone",
            brand="Apple",
            model="iPhone 15 Pro",
            storage_gb=256,
            color="Black Titanium",
            battery_health=92,
            grade="A",
            kit_json=json.dumps(["box"]),
            price=price,
            note="",
            published_at=now_utc() if status != "draft" else None,
        )
        session.add(item)
        await session.flush()
        await tags_service.sync_item_tags(session, item)
        if with_channel_message:
            session.add(ChannelMessage(item_id=item.id, chat_id=CHAT_ID, message_id=5001, kind=MessageKind.media.value))
        await session.commit()
        return item


async def _tags_for(public_id: str = PUBLIC_ID) -> set[str]:
    factory = get_session_factory()
    async with factory() as session:
        item = await session.scalar(select(Item).where(Item.public_id == public_id))
        rows = (
            await session.scalars(
                select(Tag.slug).join(ItemTag, ItemTag.tag_id == Tag.id).where(ItemTag.item_id == item.id)
            )
        ).all()
        return set(rows)


def _admin_headers() -> dict:
    return {"X-Telegram-Init-Data": make_init_data(ADMIN_USER)}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


async def test_me_without_init_data_is_401(client):
    c, _bot = client
    r = await c.get("/api/admin/me")
    assert r.status_code == 401


async def test_me_for_non_admin_is_403(client):
    c, _bot = client
    r = await c.get("/api/admin/me", headers={"X-Telegram-Init-Data": make_init_data(NON_ADMIN_USER)})
    assert r.status_code == 403


async def test_me_for_admin_is_200(client):
    c, _bot = client
    r = await c.get("/api/admin/me", headers=_admin_headers())
    assert r.status_code == 200
    assert r.json() == {"ok": True, "user_id": ADMIN_USER["id"]}


async def test_items_list_requires_admin(client):
    c, _bot = client
    r = await c.get("/api/admin/items")
    assert r.status_code == 401
    r = await c.get("/api/admin/items", headers={"X-Telegram-Init-Data": make_init_data(NON_ADMIN_USER)})
    assert r.status_code == 403


async def test_sold_requires_admin(client):
    c, _bot = client
    await _add_item()
    r = await c.post(f"/api/admin/items/{PUBLIC_ID}/sold")
    assert r.status_code == 401


async def test_patch_requires_admin(client):
    c, _bot = client
    await _add_item()
    r = await c.patch(f"/api/admin/items/{PUBLIC_ID}", json={"price": 1000})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


async def test_list_filters_by_status(client):
    c, _bot = client
    await _add_item("ACTIVE01", status="active")
    await _add_item("SOLDIT01", status="sold")
    await _add_item("DRAFTIT1", status="draft", with_channel_message=False)

    r = await c.get("/api/admin/items", headers=_admin_headers())
    assert r.status_code == 200
    ids = {it["public_id"] for it in r.json()}
    assert ids == {"ACTIVE01"}

    r = await c.get("/api/admin/items?status=sold", headers=_admin_headers())
    assert {it["public_id"] for it in r.json()} == {"SOLDIT01"}

    r = await c.get("/api/admin/items?status=draft", headers=_admin_headers())
    assert {it["public_id"] for it in r.json()} == {"DRAFTIT1"}

    item = r.json()[0]
    assert set(["public_id", "title", "price", "note", "status", "published_at", "sold_at", "cover", "lead_count"]) <= set(item)


async def test_list_unknown_status_is_422(client):
    c, _bot = client
    r = await c.get("/api/admin/items?status=bogus", headers=_admin_headers())
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Sold
# ---------------------------------------------------------------------------


async def test_mark_sold_happy_path_and_idempotent(client):
    c, bot = client
    await _add_item()

    r = await c.post(f"/api/admin/items/{PUBLIC_ID}/sold", headers=_admin_headers())
    assert r.status_code == 200
    body = r.json()
    assert body == {"public_id": PUBLIC_ID, "already_sold": False, "failed_message_ids": []}
    assert bot.deleted_ids == [5001]

    factory = get_session_factory()
    async with factory() as session:
        item = await session.scalar(select(Item).where(Item.public_id == PUBLIC_ID))
        assert item.status == "sold"

    r = await c.post(f"/api/admin/items/{PUBLIC_ID}/sold", headers=_admin_headers())
    assert r.status_code == 200
    assert r.json()["already_sold"] is True


async def test_mark_sold_on_draft_is_409(client):
    c, _bot = client
    await _add_item(status="draft", with_channel_message=False)
    r = await c.post(f"/api/admin/items/{PUBLIC_ID}/sold", headers=_admin_headers())
    assert r.status_code == 409


async def test_mark_sold_on_unknown_item_is_404(client):
    c, _bot = client
    r = await c.post("/api/admin/items/NOSUCH01/sold", headers=_admin_headers())
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Patch
# ---------------------------------------------------------------------------


async def test_patch_price_resyncs_tags_and_edits_channel_caption(client):
    c, bot = client
    await _add_item(price=75_000)  # price band 60-100k
    assert "price:60-100k" in await _tags_for()

    r = await c.patch(f"/api/admin/items/{PUBLIC_ID}", json={"price": 25_000, "note": "Торг уместен"}, headers=_admin_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["price"] == 25_000
    assert body["note"] == "Торг уместен"
    assert body["channel_updated"] is True

    tags = await _tags_for()
    assert "price:0-30k" in tags
    assert "price:60-100k" not in tags

    assert len(bot.edited_captions) == 1
    call = bot.edited_captions[0]
    assert call["chat_id"] == CHAT_ID
    assert call["message_id"] == 5001
    assert call["parse_mode"] == "HTML"
    assert call["reply_markup"] is not None
    assert "25" in call["caption"]


async def test_patch_on_sold_item_is_409(client):
    c, _bot = client
    await _add_item(status="sold")
    r = await c.patch(f"/api/admin/items/{PUBLIC_ID}", json={"price": 1000}, headers=_admin_headers())
    assert r.status_code == 409


async def test_patch_validation_422(client):
    c, _bot = client
    await _add_item()
    r = await c.patch(f"/api/admin/items/{PUBLIC_ID}", json={"price": 0}, headers=_admin_headers())
    assert r.status_code == 422
    r = await c.patch(f"/api/admin/items/{PUBLIC_ID}", json={"price": 100_000_001}, headers=_admin_headers())
    assert r.status_code == 422
    r = await c.patch(f"/api/admin/items/{PUBLIC_ID}", json={"note": "x" * 501}, headers=_admin_headers())
    assert r.status_code == 422


async def test_patch_message_not_modified_is_not_an_error(client):
    c, bot = client
    await _add_item(price=75_000)
    bot.not_modified_ids.add(5001)

    r = await c.patch(f"/api/admin/items/{PUBLIC_ID}", json={"price": 75_000}, headers=_admin_headers())
    assert r.status_code == 200
    assert r.json()["channel_updated"] is True
    assert bot.edited_captions == []


async def test_patch_telegram_edit_failure_still_returns_200_with_db_change(client):
    c, bot = client
    await _add_item(price=75_000)
    bot.broken_ids.add(5001)

    r = await c.patch(f"/api/admin/items/{PUBLIC_ID}", json={"price": 40_000}, headers=_admin_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["price"] == 40_000
    assert body["channel_updated"] is False

    factory = get_session_factory()
    async with factory() as session:
        item = await session.scalar(select(Item).where(Item.public_id == PUBLIC_ID))
        assert item.price == 40_000
