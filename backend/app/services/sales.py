"""Mark a lot as sold: flip status, then tear down the channel post.

Order matters: the item leaves the storefront (`status=sold`, `sold_at`,
`Event(sold)`) *before* we touch Telegram, so a Telegram outage never keeps a
sold lot listed. Only afterwards do we delete every `channel_messages` row for
the item (including the legacy `cta` kind). Telegram sometimes refuses to
delete channel messages older than 48 hours for bots; when that happens we
fall back to editing the post in place so the buttons disappear and it reads
"sold". Every failed delete is recorded as a `delete_failed` event and the
admin who created the lot is notified once, listing how many posts need
manual cleanup. `channel_messages` rows are kept as history either way.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import ChannelMessage, Event, EventType, Item, ItemStatus, MessageKind
from app.services import drafts, post_render
from app.services.publisher import _send_lock, _with_retry

log = logging.getLogger(__name__)

SOLD_PREFIX = "ПРОДАНО\n\n"
SOLD_CTA_TEXT = "Продано"


class SalesError(Exception):
    pass


@dataclass(frozen=True)
class SoldResult:
    public_id: str
    already_sold: bool
    failed_message_ids: list[int]


def _sold_caption(item: Item) -> str:
    caption = post_render.render_caption(item)
    text = SOLD_PREFIX + caption
    if len(text) <= post_render.CAPTION_LIMIT:
        return text
    allowed = max(post_render.CAPTION_LIMIT - len(SOLD_PREFIX), 0)
    return SOLD_PREFIX + caption[:allowed]


async def _delete_or_fallback(bot: Bot, item: Item, cm: ChannelMessage) -> tuple[str | None, bool]:
    """Try to delete a channel message; on failure fall back to editing it in
    place so it no longer looks buyable. Returns (error_text, fallback_ok);
    error_text is None when the delete itself succeeded."""
    try:
        async with _send_lock:
            await _with_retry(lambda: bot.delete_message(chat_id=cm.chat_id, message_id=cm.message_id))
        return None, False
    except Exception as exc:  # noqa: BLE001 - Telegram errors vary by version/transport
        log.warning("delete_message failed for item %s message %s: %s", item.public_id, cm.message_id, exc)
        error_text = str(exc)

    fallback_ok = False
    try:
        async with _send_lock:
            if cm.kind == MessageKind.media.value:
                await _with_retry(
                    lambda: bot.edit_message_caption(
                        chat_id=cm.chat_id,
                        message_id=cm.message_id,
                        caption=_sold_caption(item),
                        parse_mode="HTML",
                        reply_markup=None,
                    )
                )
            else:
                await _with_retry(
                    lambda: bot.edit_message_text(
                        chat_id=cm.chat_id,
                        message_id=cm.message_id,
                        text=SOLD_CTA_TEXT,
                        reply_markup=None,
                    )
                )
        fallback_ok = True
    except Exception:  # noqa: BLE001
        log.exception("fallback edit failed for item %s message %s", item.public_id, cm.message_id)

    return error_text, fallback_ok


async def _notify_admin(bot: Bot, item: Item, failed_message_ids: list[int], any_fallback_failed: bool) -> None:
    n = len(failed_message_ids)
    if any_fallback_failed:
        text = (
            f"Лот {item.public_id} снят с витрины, но пост в канале удалить не удалось ({n} сообщ.). "
            "Заменить на «ПРОДАНО» тоже не получилось — удалите или отредактируйте вручную."
        )
    else:
        text = (
            f"Лот {item.public_id} снят с витрины, но пост в канале удалить не удалось ({n} сообщ.). "
            "Помечен как «ПРОДАНО» — удалите вручную, если нужно."
        )
    try:
        await bot.send_message(chat_id=item.created_by, text=text)
    except Exception:  # noqa: BLE001
        log.exception("failed to notify admin %s about item %s", item.created_by, item.public_id)


async def mark_sold(session: AsyncSession, bot: Bot, settings: Settings, item: Item) -> SoldResult:
    """Mark `item` sold. Idempotent for already-sold items."""
    if item.status == ItemStatus.sold.value:
        return SoldResult(item.public_id, True, [])
    if item.status != ItemStatus.active.value:
        raise SalesError(f"item {item.public_id} is {item.status}")

    item.status = ItemStatus.sold.value
    item.sold_at = drafts.now_utc()
    session.add(Event(item_id=item.id, type=EventType.sold.value, payload_json=None))
    await session.commit()

    failed_message_ids: list[int] = []
    any_fallback_failed = False
    for cm in list(item.channel_messages):
        error_text, fallback_ok = await _delete_or_fallback(bot, item, cm)
        if error_text is not None:
            failed_message_ids.append(cm.message_id)
            if not fallback_ok:
                any_fallback_failed = True
            payload = json.dumps({"message_id": cm.message_id, "error": error_text, "fallback_ok": fallback_ok})
            session.add(Event(item_id=item.id, type=EventType.delete_failed.value, payload_json=payload))

    if failed_message_ids:
        await session.commit()
        await _notify_admin(bot, item, failed_message_ids, any_fallback_failed)

    return SoldResult(item.public_id, False, failed_message_ids)
