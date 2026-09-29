import json
from urllib.parse import parse_qs, urlsplit

import pytest
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendPhoto
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db.models import ChannelMessage, Event, Item, ItemTag, Photo
from app.services import publisher
from tests.bot_utils import FakeBot
from tests.db_utils import make_session_factory


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(publisher, "SEND_PAUSE_SECONDS", 0)


@pytest.fixture
async def factory(tmp_path):
    f = await make_session_factory(tmp_path / "pub.db")
    yield f
    await f.kw["bind"].dispose()


async def _make_item(session, *, photos=2, complete=True, media: list[str] | None = None) -> Item:
    """`media` lists kinds in position order (e.g. ["video", "photo"]); default: `photos` photos."""
    item = Item(
        public_id="PUBLIC01",
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
        price=75000 if complete else None,
        note="",
    )
    session.add(item)
    await session.flush()
    kinds = media if media is not None else ["photo"] * photos
    for i, kind in enumerate(kinds):
        session.add(
            Photo(item_id=item.id, kind=kind, tg_file_id=f"{kind}{i}", file_unique_id=f"u{i}", position=i)
        )
    await session.commit()
    return await session.scalar(
        select(Item).where(Item.id == item.id).options(selectinload(Item.photos), selectinload(Item.channel_messages))
    )


async def test_publish_sends_single_photo_with_button_and_persists(factory):
    settings = get_settings()
    bot = FakeBot()
    async with factory() as session:
        item = await _make_item(session, photos=3)
        result = await publisher.publish(session, bot, settings, item)

        assert result.already_published is False
        assert result.chat_id == settings.channel_id
        assert result.post_url == f"https://t.me/c/1234567890/{result.message_id}"

        # exactly one Telegram message: cover photo + caption + inline button
        assert len(bot.calls) == 1
        post = bot.calls[0]
        assert isinstance(post, SendPhoto)
        assert post.chat_id == settings.channel_id
        assert post.photo == "photo0"
        assert post.parse_mode == "HTML"
        assert "iPhone 15 Pro 256 ГБ, Black Titanium" in post.caption
        rows = post.reply_markup.inline_keyboard
        assert [[b.text for b in row] for row in rows] == [["Написать продавцу"], ["Открыть в приложении"]]
        seller = urlsplit(rows[0][0].url)
        assert (seller.scheme, seller.netloc, seller.path) == ("https", "t.me", "/seller_test")
        assert parse_qs(seller.query)["text"] == [
            "Здравствуйте, интересует iPhone 15 Pro 256 ГБ, Black Titanium, лот PUBLIC01"
        ]
        assert rows[1][0].url == "https://t.me/test_bot/shop?startapp=item_PUBLIC01"

        # DB: status, timestamps, channel_messages, tags, event
        fresh = await session.scalar(
            select(Item).where(Item.id == item.id).options(selectinload(Item.channel_messages))
        )
        assert fresh.status == "active" and fresh.published_at is not None
        assert [(m.kind, m.message_id) for m in fresh.channel_messages] == [("media", result.message_id)]
        tag_count = len((await session.scalars(select(ItemTag).where(ItemTag.item_id == item.id))).all())
        assert tag_count == 9  # device, brand, model, storage, color, grade, battery, kit:box, price
        events = (await session.scalars(select(Event.type).where(Event.item_id == item.id))).all()
        assert "published" in events


async def test_publish_is_idempotent_for_active_item(factory):
    settings = get_settings()
    bot = FakeBot()
    async with factory() as session:
        item = await _make_item(session)
        first = await publisher.publish(session, bot, settings, item)
        calls_after_first = len(bot.calls)
        again = await session.scalar(
            select(Item).where(Item.id == item.id).options(selectinload(Item.photos), selectinload(Item.channel_messages))
        )
        second = await publisher.publish(session, bot, settings, again)
        assert second.already_published is True
        assert second.message_id == first.message_id
        assert len(bot.calls) == calls_after_first  # nothing re-sent
        rows = (await session.scalars(select(ChannelMessage).where(ChannelMessage.item_id == item.id))).all()
        assert len(rows) == 1


async def test_publish_rejects_incomplete_draft(factory):
    settings = get_settings()
    bot = FakeBot()
    async with factory() as session:
        item = await _make_item(session, complete=False)
        with pytest.raises(publisher.DraftIncomplete) as exc:
            await publisher.publish(session, bot, settings, item)
        assert exc.value.missing_step == "price"
        assert bot.calls == []
        assert item.status == "draft"


async def test_retry_after_is_honoured(factory, monkeypatch):
    settings = get_settings()
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(publisher.asyncio, "sleep", fake_sleep)

    class FlakyBot(FakeBot):
        def __init__(self):
            super().__init__()
            self.failed_once = False

        async def __call__(self, method, request_timeout=None):
            if isinstance(method, SendPhoto) and not self.failed_once:
                self.failed_once = True
                raise TelegramRetryAfter(method=method, message="Too Many Requests: retry after 3", retry_after=3)
            return await super().__call__(method, request_timeout)

    bot = FlakyBot()
    async with factory() as session:
        item = await _make_item(session)
        result = await publisher.publish(session, bot, settings, item)
    assert result.already_published is False
    assert 3.5 in sleeps  # retry_after + 0.5
    assert bot.failed_once is True
    assert len([c for c in bot.calls if isinstance(c, SendPhoto)]) == 1  # the successful retry


async def test_send_failure_keeps_draft_and_writes_nothing(factory):
    settings = get_settings()

    class FailingBot(FakeBot):
        async def __call__(self, method, request_timeout=None):
            if isinstance(method, SendPhoto):
                raise RuntimeError("boom")
            return await super().__call__(method, request_timeout)

    bot = FailingBot()
    async with factory() as session:
        item = await _make_item(session, photos=2)
        with pytest.raises(RuntimeError):
            await publisher.publish(session, bot, settings, item)
        fresh = await session.get(Item, item.id)
        assert fresh.status == "draft"
        rows = (await session.scalars(select(ChannelMessage))).all()
        assert rows == []


def test_channel_post_url():
    assert publisher.channel_post_url(-1001234567890, 55) == "https://t.me/c/1234567890/55"


async def test_without_seller_username_only_app_button(factory, monkeypatch):
    monkeypatch.setenv("SELLER_USERNAME", "")
    get_settings.cache_clear()
    settings = get_settings()
    bot = FakeBot()
    async with factory() as session:
        item = await _make_item(session)
        await publisher.publish(session, bot, settings, item)
    rows = bot.calls[0].reply_markup.inline_keyboard
    assert [[b.text for b in row] for row in rows] == [["Открыть в приложении"]]
    assert rows[0][0].url == "https://t.me/test_bot/shop?startapp=item_PUBLIC01"


async def test_cover_skips_leading_video(factory):
    settings = get_settings()
    bot = FakeBot()
    async with factory() as session:
        item = await _make_item(session, media=["video", "video", "photo", "photo"])
        assert publisher.cover_photo(item).tg_file_id == "photo2"
        await publisher.publish(session, bot, settings, item)
    assert bot.calls[0].photo == "photo2"


async def test_publish_without_any_photo_is_refused(factory):
    settings = get_settings()
    bot = FakeBot()
    async with factory() as session:
        item = await _make_item(session, media=["video"])
        assert publisher.cover_photo(item) is None
        with pytest.raises(publisher.PublishError, match="нужно хотя бы одно фото"):
            await publisher.publish(session, bot, settings, item)
        assert bot.calls == []
        fresh = await session.get(Item, item.id)
        assert fresh.status == "draft"
        assert (await session.scalars(select(ChannelMessage))).all() == []


def test_post_keyboard_for_non_apple_item_uses_title():
    item = Item(
        public_id="NONAPPL1", status="draft", created_by=1, category="other", device_type="phone",
        brand="Samsung", model="Galaxy S23", year=2023, storage_gb=256, color="Phantom Black",
    )
    rows = publisher.post_keyboard(get_settings(), item).inline_keyboard
    text = parse_qs(urlsplit(rows[0][0].url).query)["text"][0]
    assert text == "Здравствуйте, интересует Samsung Galaxy S23 (2023) 256 ГБ, Phantom Black, лот NONAPPL1"
    assert rows[1][0].url.endswith("startapp=item_NONAPPL1")
