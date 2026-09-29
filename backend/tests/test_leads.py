from __future__ import annotations

import json

import pytest
from aiogram import Bot
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import get_settings
from app.db.base import Base
from app.db.models import Item, Lead
from app.db.session import get_engine, get_session_factory
from app.main import app
from app.services import leads as leads_service
from tests.test_init_data import make_init_data

PUBLIC_ID = "LEADIT01"


class FakeLeadBot(Bot):
    """Records send_message calls instead of hitting Telegram; can simulate failure."""

    def __init__(self) -> None:
        super().__init__(token="123456:TEST")
        self.sent: list[dict] = []
        self.fail = False

    async def send_message(self, chat_id, text, parse_mode=None, **kwargs):  # type: ignore[override]
        if self.fail:
            raise RuntimeError("telegram is down")
        self.sent.append({"chat_id": chat_id, "text": text, "parse_mode": parse_mode})
        return None


@pytest.fixture(autouse=True)
def _no_webhook_and_fresh_rate_limits(monkeypatch):
    monkeypatch.setenv("SET_WEBHOOK_ON_START", "0")
    get_settings.cache_clear()
    leads_service.reset_rate_limits()
    yield
    leads_service.reset_rate_limits()


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        bot = FakeLeadBot()
        app.state.bot = bot
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c, bot


async def _add_active_item(public_id: str = PUBLIC_ID, *, status: str = "active", price: int = 75_000) -> Item:
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
        )
        session.add(item)
        await session.commit()
        return item


async def _leads_for(public_id: str = PUBLIC_ID) -> list[Lead]:
    factory = get_session_factory()
    async with factory() as session:
        item = await session.scalar(select(Item).where(Item.public_id == public_id))
        return list((await session.scalars(select(Lead).where(Lead.item_id == item.id))).all())


# ---------------------------------------------------------------------------
# Site leads (no initData)
# ---------------------------------------------------------------------------


async def test_site_lead_happy_path_notifies_and_escapes_html(client):
    c, bot = client
    await _add_active_item()

    r = await c.post(
        f"/api/items/{PUBLIC_ID}/lead",
        json={"name": "<script>alert(1)</script>", "contact": "+79991234567"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {"ok": True, "message": "Заявка отправлена, продавец свяжется с вами"}

    assert len(bot.sent) == 1
    text = bot.sent[0]["text"]
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "iPhone 15 Pro" in text
    assert PUBLIC_ID in text
    assert bot.sent[0]["parse_mode"] == "HTML"

    leads = await _leads_for()
    assert len(leads) == 1
    assert leads[0].source == "site"
    assert leads[0].buyer_contact == "+79991234567"
    assert leads[0].notified_at is not None


async def test_site_lead_honeypot_is_silently_accepted(client):
    c, bot = client
    await _add_active_item()

    r = await c.post(
        f"/api/items/{PUBLIC_ID}/lead",
        json={"name": "Bot", "contact": "+79991234567", "website": "https://spam.example"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert bot.sent == []
    assert await _leads_for() == []


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "", "contact": "+79991234567"},
        {"name": "A", "contact": "hi"},
        {"name": "A" * 200, "contact": "+79991234567"},
        {"name": "A", "contact": ""},
    ],
)
async def test_site_lead_validation_422(client, payload):
    c, _bot = client
    await _add_active_item()
    r = await c.post(f"/api/items/{PUBLIC_ID}/lead", json=payload)
    assert r.status_code == 422


async def test_site_lead_rate_limited_after_five_per_hour(client, monkeypatch):
    from app.api import leads as leads_api

    c, _bot = client
    await _add_active_item()
    ip = {"value": "203.0.113.5"}
    monkeypatch.setattr(leads_api, "_client_ip", lambda request: ip["value"])
    for i in range(5):
        r = await c.post(f"/api/items/{PUBLIC_ID}/lead", json={"name": "Buyer", "contact": f"+7999000000{i}"})
        assert r.status_code == 200
    # a spoofed X-Forwarded-For must not reset the limit
    r = await c.post(
        f"/api/items/{PUBLIC_ID}/lead",
        json={"name": "Buyer", "contact": "+79990000009"},
        headers={"X-Forwarded-For": "198.51.100.1"},
    )
    assert r.status_code == 429

    # a different real client IP is unaffected
    ip["value"] = "203.0.113.6"
    r = await c.post(f"/api/items/{PUBLIC_ID}/lead", json={"name": "Buyer", "contact": "+79990000009"})
    assert r.status_code == 200


def test_client_ip_ignores_forwarded_header():
    from starlette.requests import Request

    from app.api.leads import _client_ip

    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"198.51.100.1")],
        "client": ("203.0.113.5", 1234),
    }
    assert _client_ip(Request(scope)) == "203.0.113.5"


def test_buyer_link_only_for_valid_usernames():
    from app.services.leads import _buyer_link

    assert _buyer_link("site", "@good_name", None) == '<a href="https://t.me/good_name">Открыть профиль</a>'
    assert _buyer_link("site", '@x"><b>', None) is None
    assert _buyer_link("site", "+79990000000", None) is None


async def test_duplicate_contact_and_item_is_idempotent_without_second_notification(client):
    c, bot = client
    await _add_active_item()

    for _ in range(2):
        r = await c.post(
            f"/api/items/{PUBLIC_ID}/lead",
            json={"name": "Buyer", "contact": "+79995550000"},
        )
        assert r.status_code == 200

    assert len(bot.sent) == 1  # second submission suppressed the notification
    leads = await _leads_for()
    assert len(leads) == 2  # but both submissions are recorded


# ---------------------------------------------------------------------------
# App leads (Telegram WebApp initData)
# ---------------------------------------------------------------------------


async def test_app_lead_with_username_uses_at_handle_as_contact(client):
    c, bot = client
    await _add_active_item()
    init_data = make_init_data({"id": 555, "first_name": "Buyer", "last_name": "Ivanov", "username": "buyer555"})

    r = await c.post(
        f"/api/items/{PUBLIC_ID}/lead",
        json={},
        headers={"X-Telegram-Init-Data": init_data},
    )
    assert r.status_code == 200
    leads = await _leads_for()
    assert leads[0].source == "app"
    assert leads[0].buyer_name == "Buyer Ivanov"
    assert leads[0].buyer_contact == "@buyer555"
    assert leads[0].buyer_tg_id == 555
    assert leads[0].buyer_username == "buyer555"
    # app buyers always get the tg://user?id= deep link, per spec, even with a username
    assert '<a href="tg://user?id=555">' in bot.sent[0]["text"]


async def test_app_lead_without_username_falls_back_to_tg_deeplink(client):
    c, bot = client
    await _add_active_item()
    init_data = make_init_data({"id": 777, "first_name": "NoHandle"})

    r = await c.post(
        f"/api/items/{PUBLIC_ID}/lead",
        json={},
        headers={"X-Telegram-Init-Data": init_data},
    )
    assert r.status_code == 200
    leads = await _leads_for()
    assert leads[0].buyer_contact == "tg://user?id=777"
    assert leads[0].buyer_username is None
    assert '<a href="tg://user?id=777">' in bot.sent[0]["text"]


async def test_app_lead_with_phone_prefers_phone_as_contact(client):
    c, _bot = client
    await _add_active_item()
    init_data = make_init_data({"id": 888, "first_name": "Phoned", "username": "phoned"})

    r = await c.post(
        f"/api/items/{PUBLIC_ID}/lead",
        json={"phone": "+79997654321"},
        headers={"X-Telegram-Init-Data": init_data},
    )
    assert r.status_code == 200
    leads = await _leads_for()
    assert leads[0].buyer_contact == "+79997654321"


async def test_invalid_init_data_is_401():
    async with app.router.lifespan_context(app):
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        app.state.bot = FakeLeadBot()
        await _add_active_item()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                f"/api/items/{PUBLIC_ID}/lead",
                json={},
                headers={"X-Telegram-Init-Data": "bad=data&hash=deadbeef"},
            )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Item status
# ---------------------------------------------------------------------------


async def test_lead_on_sold_item_is_410(client):
    c, _bot = client
    await _add_active_item(status="sold")
    r = await c.post(f"/api/items/{PUBLIC_ID}/lead", json={"name": "Buyer", "contact": "+79990000000"})
    assert r.status_code == 410


async def test_lead_on_draft_item_is_404(client):
    c, _bot = client
    await _add_active_item(status="draft")
    r = await c.post(f"/api/items/{PUBLIC_ID}/lead", json={"name": "Buyer", "contact": "+79990000000"})
    assert r.status_code == 404


async def test_lead_on_unknown_item_is_404(client):
    c, _bot = client
    r = await c.post("/api/items/NOSUCH01/lead", json={"name": "Buyer", "contact": "+79990000000"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Notification failure
# ---------------------------------------------------------------------------


async def test_notification_failure_still_returns_200_and_saves_lead(client):
    c, bot = client
    await _add_active_item()
    bot.fail = True

    r = await c.post(f"/api/items/{PUBLIC_ID}/lead", json={"name": "Buyer", "contact": "+79990001111"})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    leads = await _leads_for()
    assert len(leads) == 1
    assert leads[0].notified_at is None
