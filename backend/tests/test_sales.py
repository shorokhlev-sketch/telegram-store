import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db.models import ChannelMessage, Event, Item, ItemStatus, MessageKind
from app.services import sales
from app.services.drafts import now_utc
from tests.bot_utils import ADMIN_ID, FakeBot
from tests.db_utils import make_session_factory


@pytest.fixture
async def factory(tmp_path):
    f = await make_session_factory(tmp_path / "sales.db")
    yield f
    await f.kw["bind"].dispose()


async def _make_active_item(session, *, extra_cta: bool = False, status: str = "active") -> Item:
    item = Item(
        public_id="SOLDIT01",
        status=status,
        created_by=ADMIN_ID,
        model="iPhone 15 Pro",
        generation="15",
        storage_gb=256,
        color="Black Titanium",
        battery_health=92,
        grade="A",
        kit_json=json.dumps(["box"]),
        price=75000,
        note="",
        published_at=now_utc() if status != "draft" else None,
    )
    session.add(item)
    await session.flush()
    session.add(ChannelMessage(item_id=item.id, chat_id=-100123, message_id=5001, kind=MessageKind.media.value))
    if extra_cta:
        session.add(ChannelMessage(item_id=item.id, chat_id=-100123, message_id=5002, kind=MessageKind.cta.value))
    await session.commit()
    return await session.scalar(
        select(Item).where(Item.id == item.id).options(selectinload(Item.channel_messages))
    )


async def test_mark_sold_sets_status_and_deletes_all_messages_including_legacy_cta(factory):
    settings = get_settings()
    bot = FakeBot()
    async with factory() as session:
        item = await _make_active_item(session, extra_cta=True)
        result = await sales.mark_sold(session, bot, settings, item)

        assert result.public_id == "SOLDIT01"
        assert result.already_sold is False
        assert result.failed_message_ids == []
        assert bot.deleted_message_ids() == [5001, 5002]

        fresh = await session.get(Item, item.id)
        assert fresh.status == "sold"
        assert fresh.sold_at is not None
        events = (await session.scalars(select(Event.type).where(Event.item_id == item.id))).all()
        assert "sold" in events
        assert "delete_failed" not in events

        # channel_messages history rows are kept, not deleted
        rows = (await session.scalars(select(ChannelMessage).where(ChannelMessage.item_id == item.id))).all()
        assert len(rows) == 2


async def test_mark_sold_is_idempotent_and_makes_no_telegram_calls(factory):
    settings = get_settings()
    bot = FakeBot()
    async with factory() as session:
        item = await _make_active_item(session)
        first = await sales.mark_sold(session, bot, settings, item)
        assert first.already_sold is False
        bot.reset()

        again = await session.scalar(
            select(Item).where(Item.id == item.id).options(selectinload(Item.channel_messages))
        )
        second = await sales.mark_sold(session, bot, settings, again)
        assert second.already_sold is True
        assert second.failed_message_ids == []
        assert bot.calls == []


async def test_mark_sold_rejects_draft(factory):
    settings = get_settings()
    bot = FakeBot()
    async with factory() as session:
        item = await _make_active_item(session, status="draft")
        with pytest.raises(sales.SalesError):
            await sales.mark_sold(session, bot, settings, item)
        assert bot.calls == []
        fresh = await session.get(Item, item.id)
        assert fresh.status == "draft"


async def test_delete_failure_falls_back_to_caption_edit_and_notifies_admin(factory):
    settings = get_settings()
    bot = FakeBot()
    bot.fail_delete_ids = {5001}
    async with factory() as session:
        item = await _make_active_item(session)
        result = await sales.mark_sold(session, bot, settings, item)

        assert result.failed_message_ids == [5001]
        fresh = await session.get(Item, item.id)
        assert fresh.status == "sold"  # still sold despite the Telegram failure

        captions = bot.edited_captions()
        assert len(captions) == 1
        assert captions[0].message_id == 5001
        assert captions[0].caption.startswith("ПРОДАНО\n\n")
        assert len(captions[0].caption) <= 1024
        assert captions[0].reply_markup is None

        events = (await session.scalars(select(Event).where(Event.item_id == item.id))).all()
        delete_failed = [e for e in events if e.type == "delete_failed"]
        assert len(delete_failed) == 1
        payload = json.loads(delete_failed[0].payload_json)
        assert payload["message_id"] == 5001
        assert payload["fallback_ok"] is True

        admin_texts = bot.sent_texts()
        assert len(admin_texts) == 1
        assert "SOLDIT01" in admin_texts[0]
        assert "ПРОДАНО" in admin_texts[0]


async def test_cta_kind_delete_failure_falls_back_to_edit_text(factory):
    settings = get_settings()
    bot = FakeBot()
    bot.fail_delete_ids = {5002}
    async with factory() as session:
        item = await _make_active_item(session, extra_cta=True)
        result = await sales.mark_sold(session, bot, settings, item)

        assert result.failed_message_ids == [5002]
        texts = bot.edited_texts()
        assert len(texts) == 1
        assert texts[0].message_id == 5002
        assert texts[0].text == "Продано"
        assert texts[0].reply_markup is None
        # the other (media) message deleted fine
        assert bot.deleted_message_ids() == [5001, 5002]


async def test_delete_and_fallback_both_fail_does_not_raise(factory):
    settings = get_settings()
    bot = FakeBot()
    bot.fail_delete_ids = {5001}
    bot.fail_edit_ids = {5001}
    async with factory() as session:
        item = await _make_active_item(session)
        result = await sales.mark_sold(session, bot, settings, item)

        assert result.failed_message_ids == [5001]
        fresh = await session.get(Item, item.id)
        assert fresh.status == "sold"

        events = (await session.scalars(select(Event).where(Event.item_id == item.id))).all()
        delete_failed = [e for e in events if e.type == "delete_failed"]
        assert len(delete_failed) == 1
        payload = json.loads(delete_failed[0].payload_json)
        assert payload["fallback_ok"] is False

        admin_texts = bot.sent_texts()
        assert len(admin_texts) == 1
        assert "не получилось" in admin_texts[0]


async def test_admin_notification_failure_does_not_raise(factory, monkeypatch):
    settings = get_settings()
    bot = FakeBot()
    bot.fail_delete_ids = {5001}

    async def boom(**kwargs):
        raise RuntimeError("telegram is down")

    monkeypatch.setattr(bot, "send_message", boom)

    async with factory() as session:
        item = await _make_active_item(session)
        result = await sales.mark_sold(session, bot, settings, item)
        assert result.failed_message_ids == [5001]
        fresh = await session.get(Item, item.id)
        assert fresh.status == "sold"
