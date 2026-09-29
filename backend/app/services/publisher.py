"""Publish a finished draft to the Telegram channel.

Flow: a single photo message — the cover (first media of kind photo), the
caption and two inline buttons: "message the seller" (a t.me link with a
prefilled text, omitted when SELLER_USERNAME is empty) and "open in app".
Media groups can't carry keyboards, so the channel shows only the cover photo;
the full gallery (photos and videos) lives in the Mini App and on the site. The message_id is
stored in `channel_messages` in the same transaction that flips the item to
`active`. A process-wide lock plus a pause between sends keeps us
under Telegram's per-channel rate limit; RetryAfter is honoured and retried.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import ChannelMessage, Event, EventType, Item, ItemStatus, MediaKind, MessageKind, Photo
from app.services import drafts, notify, post_render, tags

log = logging.getLogger(__name__)

_send_lock = asyncio.Lock()
SEND_PAUSE_SECONDS = 1.0
MAX_ATTEMPTS = 4

T = TypeVar("T")


class PublishError(Exception):
    pass


class DraftIncomplete(PublishError):
    def __init__(self, missing_step: str) -> None:
        super().__init__(f"draft incomplete: {missing_step}")
        self.missing_step = missing_step


@dataclass(frozen=True)
class PublishResult:
    item_id: int
    public_id: str
    chat_id: int
    message_id: int
    already_published: bool = False
    # new-listing broadcast to subscribers; None for the idempotent path
    broadcast: asyncio.Task | None = field(default=None, compare=False, repr=False)

    @property
    def post_url(self) -> str:
        return channel_post_url(self.chat_id, self.message_id)


def channel_post_url(chat_id: int, message_id: int) -> str:
    """t.me link for a post in a channel identified by its -100... id."""
    raw = str(chat_id)
    internal = raw[4:] if raw.startswith("-100") else raw.lstrip("-")
    return f"https://t.me/c/{internal}/{message_id}"


async def _with_retry(call: Callable[[], Awaitable[T]]) -> T:
    attempt = 0
    while True:
        attempt += 1
        try:
            return await call()
        except TelegramRetryAfter as exc:
            if attempt >= MAX_ATTEMPTS:
                raise
            log.warning("RetryAfter %ss (attempt %s)", exc.retry_after, attempt)
            await asyncio.sleep(exc.retry_after + 0.5)


NO_PHOTO_ERROR = "нужно хотя бы одно фото"


def cover_photo(item: Item) -> Photo | None:
    """Channel cover: the first media of kind photo (a leading video is skipped)."""
    return next(
        (m for m in item.photos if (m.kind or MediaKind.photo.value) == MediaKind.photo.value),
        None,
    )


def seller_message_text(item: Item) -> str:
    return f"Здравствуйте, интересует {post_render.item_title(item)}, лот {item.public_id}"


def post_keyboard(settings: Settings, item: Item) -> InlineKeyboardMarkup:
    """Buttons under the channel post (and the wizard preview, 1:1)."""
    rows: list[list[InlineKeyboardButton]] = []
    seller_url = settings.seller_chat_url(seller_message_text(item))
    if seller_url:
        rows.append([InlineKeyboardButton(text="Написать продавцу", url=seller_url)])
    rows.append([InlineKeyboardButton(text="Открыть в приложении", url=settings.miniapp_item_url(item.public_id))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def listing_keyboard(settings: Settings, item: Item) -> InlineKeyboardMarkup:
    """Buttons under the new-listing message to subscribers (private chats, so
    the item opens as a Mini App via an inline `web_app` button)."""
    rows = [[InlineKeyboardButton(text="Открыть", web_app=WebAppInfo(url=settings.app_item_url(item.public_id)))]]
    seller_url = settings.seller_chat_url(seller_message_text(item))
    if seller_url:
        rows.append([InlineKeyboardButton(text="Написать продавцу", url=seller_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def listing_for(settings: Settings, item: Item, cover: Photo) -> notify.Listing:
    prefix = notify.CAPTION_PREFIX
    caption = prefix + post_render.render_caption(item, limit=post_render.CAPTION_LIMIT - len(prefix))
    return notify.Listing(
        item_id=item.id,
        public_id=item.public_id,
        photo=cover.tg_file_id,
        caption=caption,
        keyboard=listing_keyboard(settings, item),
    )


async def publish(session: AsyncSession, bot: Bot, settings: Settings, item: Item) -> PublishResult:
    """Publish `item` (a complete draft). Idempotent for already-active items.
    A fresh publication also schedules the new-listing broadcast to subscribers
    (background task with its own session, see notify.py)."""
    if item.status == ItemStatus.active.value:
        media = next((m.message_id for m in item.channel_messages if m.kind == MessageKind.media.value), 0)
        return PublishResult(item.id, item.public_id, settings.channel_id, media, True)
    if item.status != ItemStatus.draft.value:
        raise PublishError(f"item {item.public_id} is {item.status}")
    # checked before completeness: without a photo the media step is never "done"
    cover = cover_photo(item)
    if cover is None:
        raise PublishError(NO_PHOTO_ERROR)
    missing = drafts.next_step(item)
    if missing is not None:
        raise DraftIncomplete(missing)
    if not settings.channel_id:
        raise PublishError("CHANNEL_ID is not configured")

    caption = post_render.render_caption(item)
    keyboard = post_keyboard(settings, item)
    listing = listing_for(settings, item, cover)
    chat_id = settings.channel_id

    async with _send_lock:
        msg: Message = await _with_retry(
            lambda: bot.send_photo(
                chat_id=chat_id,
                photo=cover.tg_file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        )
        await asyncio.sleep(SEND_PAUSE_SECONDS)

    session.add(ChannelMessage(item_id=item.id, chat_id=chat_id, message_id=msg.message_id, kind=MessageKind.media.value))
    item.status = ItemStatus.active.value
    item.published_at = drafts.now_utc()
    await tags.sync_item_tags(session, item)
    session.add(Event(item_id=item.id, type=EventType.published.value, payload_json=f'{{"message_id": {msg.message_id}}}'))
    await session.commit()
    await session.refresh(item, ["channel_messages"])
    # The caller's session is closed long before the broadcast ends: give the
    # task its own sessions on the same engine.
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    task = notify.schedule(bot, settings, factory, listing)
    return PublishResult(item.id, item.public_id, chat_id, msg.message_id, broadcast=task)
