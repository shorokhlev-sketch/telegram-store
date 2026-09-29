"""New-listing broadcast to subscribers (services/notify.py + publisher hook)."""

import json

import pytest
from aiogram.methods import SendPhoto
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db.models import Event, Item, Photo, Subscriber
from app.services import notify, post_render, publisher
from app.services.drafts import now_utc
from tests.bot_utils import FakeBot
from tests.db_utils import make_session_factory

PUBLIC_ID = "NOTIFY01"


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(notify, "_sleep", fake_sleep)
    monkeypatch.setattr(publisher, "SEND_PAUSE_SECONDS", 0)
    return sleeps


@pytest.fixture
async def factory(tmp_path):
    f = await make_session_factory(tmp_path / "notify.db")
    yield f
    await f.kw["bind"].dispose()


async def _add_subscribers(factory, rows: list[tuple[int, bool, bool]]) -> None:
    """(tg_id, subscribed, blocked)"""
    async with factory() as s:
        for tg_id, subscribed, blocked in rows:
            s.add(
                Subscriber(
                    tg_id=tg_id,
                    username=f"u{tg_id}",
                    first_name="X",
                    subscribed=subscribed,
                    blocked_at=now_utc() if blocked else None,
                )
            )
        await s.commit()


async def _draft(session, *, note: str = "") -> Item:
    item = Item(
        public_id=PUBLIC_ID,
        status="draft",
        created_by=111,
        category="iphone",
        device_type="phone",
        brand="Apple",
        model="iPhone 15 Pro",
        generation="15",
        storage_gb=256,
        color="Black Titanium",
        battery_health=92,
        grade="A",
        kit_json=json.dumps(["box"]),
        price=75000,
        note=note,
    )
    session.add(item)
    await session.flush()
    session.add(Photo(item_id=item.id, kind="photo", tg_file_id="cover0", file_unique_id="u0", position=0))
    await session.commit()
    return await session.scalar(
        select(Item).where(Item.id == item.id).options(selectinload(Item.photos), selectinload(Item.channel_messages))
    )


def _to_subscribers(bot: FakeBot, channel_id: int) -> list[SendPhoto]:
    return [p for p in bot.photos() if p.chat_id != channel_id]


async def _events(factory, type_: str) -> list[dict]:
    async with factory() as s:
        rows = (await s.scalars(select(Event).where(Event.type == type_))).all()
    return [json.loads(r.payload_json) for r in rows]


async def test_publish_broadcasts_to_subscribed_only(factory):
    settings = get_settings()
    await _add_subscribers(
        factory,
        [
            (501, True, False),
            (502, False, False),  # opted out
            (503, True, True),  # blocked earlier
            (504, True, False),
            (111, True, False),  # an admin who once was a customer: no copy
        ],
    )
    bot = FakeBot()
    async with factory() as session:
        item = await _draft(session)
        result = await publisher.publish(session, bot, settings, item)
    assert result.broadcast is not None
    stats = await result.broadcast
    assert (stats.sent, stats.failed, stats.blocked) == (2, 0, 0)

    sends = _to_subscribers(bot, settings.channel_id)
    assert [p.chat_id for p in sends] == [501, 504]
    msg = sends[0]
    assert msg.photo == "cover0"
    assert msg.parse_mode == "HTML"
    assert msg.caption.startswith("Новое объявление\n\n<b>iPhone 15 Pro 256 ГБ, Black Titanium</b>")
    assert msg.caption.endswith(f"Лот {PUBLIC_ID}")
    rows = msg.reply_markup.inline_keyboard
    assert [[b.text for b in row] for row in rows] == [["Открыть"], ["Написать продавцу"]]
    assert rows[0][0].web_app.url == f"https://example.test/item/{PUBLIC_ID}"
    assert rows[1][0].url.startswith("https://t.me/seller_test?text=")

    assert await _events(factory, "notified") == [{"sent": 2, "failed": 0, "blocked": 0}]


async def test_republish_is_idempotent_and_does_not_broadcast(factory):
    settings = get_settings()
    await _add_subscribers(factory, [(501, True, False)])
    bot = FakeBot()
    async with factory() as session:
        item = await _draft(session)
        first = await publisher.publish(session, bot, settings, item)
        await first.broadcast
        again = await publisher.publish(session, bot, settings, item)
    assert again.already_published is True and again.broadcast is None
    await notify.drain()
    assert [p.chat_id for p in _to_subscribers(bot, settings.channel_id)] == [501]
    assert len(await _events(factory, "notified")) == 1


async def test_no_seller_button_without_seller_username(factory, monkeypatch):
    monkeypatch.setenv("SELLER_USERNAME", "")
    get_settings.cache_clear()
    settings = get_settings()
    await _add_subscribers(factory, [(501, True, False)])
    bot = FakeBot()
    async with factory() as session:
        result = await publisher.publish(session, bot, settings, await _draft(session))
    await result.broadcast
    rows = _to_subscribers(bot, settings.channel_id)[0].reply_markup.inline_keyboard
    assert [[b.text for b in row] for row in rows] == [["Открыть"]]


async def test_forbidden_marks_blocked_and_skips_next_time(factory):
    settings = get_settings()
    await _add_subscribers(factory, [(501, True, False), (502, True, False)])
    bot = FakeBot()
    bot.forbidden_chats.add(501)
    async with factory() as session:
        result = await publisher.publish(session, bot, settings, await _draft(session))
    stats = await result.broadcast
    assert (stats.sent, stats.failed, stats.blocked) == (1, 0, 1)
    async with factory() as s:
        blocked = await s.get(Subscriber, 501)
        ok = await s.get(Subscriber, 502)
    assert blocked.blocked_at is not None and blocked.subscribed is True
    assert ok.blocked_at is None
    assert await _events(factory, "notified") == [{"sent": 1, "failed": 0, "blocked": 1}]

    async with factory() as s:
        from app.services import subscribers

        assert await subscribers.recipients(s) == [502]


async def test_retry_after_is_honoured_and_message_delivered(factory, _fast):
    settings = get_settings()
    await _add_subscribers(factory, [(501, True, False), (502, True, False)])
    bot = FakeBot()
    bot.retry_after_once[501] = 7
    async with factory() as session:
        result = await publisher.publish(session, bot, settings, await _draft(session))
    stats = await result.broadcast
    assert (stats.sent, stats.failed, stats.blocked) == (2, 0, 0)
    assert [p.chat_id for p in _to_subscribers(bot, settings.channel_id)] == [501, 501, 502]
    assert 7.5 in _fast  # waited retry_after (+0.5 margin) before retrying
    # pacing: one pause per recipient at <= 20 msg/s
    assert _fast.count(notify.SEND_INTERVAL) == 2 and notify.SEND_INTERVAL <= 1 / 20


async def test_other_errors_are_skipped(factory):
    settings = get_settings()
    await _add_subscribers(factory, [(501, True, False), (502, True, False)])
    bot = FakeBot()
    bot.fail_photo_chats.add(501)
    async with factory() as session:
        result = await publisher.publish(session, bot, settings, await _draft(session))
    stats = await result.broadcast
    assert (stats.sent, stats.failed, stats.blocked) == (1, 1, 0)
    async with factory() as s:
        assert (await s.get(Subscriber, 501)).blocked_at is None


async def test_broadcast_caption_respects_1024(factory):
    settings = get_settings()
    await _add_subscribers(factory, [(501, True, False)])
    bot = FakeBot()
    async with factory() as session:
        result = await publisher.publish(session, bot, settings, await _draft(session, note="д" * 2000))
    await result.broadcast
    channel_post = [p for p in bot.photos() if p.chat_id == settings.channel_id][0]
    broadcast = _to_subscribers(bot, settings.channel_id)[0]
    assert len(channel_post.caption) <= post_render.CAPTION_LIMIT
    assert len(broadcast.caption) <= post_render.CAPTION_LIMIT
    assert broadcast.caption.startswith(notify.CAPTION_PREFIX) and broadcast.caption.endswith(f"Лот {PUBLIC_ID}")


async def test_no_subscribers_still_records_event(factory):
    settings = get_settings()
    bot = FakeBot()
    async with factory() as session:
        result = await publisher.publish(session, bot, settings, await _draft(session))
    stats = await result.broadcast
    assert stats.sent == 0
    assert len(bot.photos()) == 1  # the channel post only
    assert await _events(factory, "notified") == [{"sent": 0, "failed": 0, "blocked": 0}]
