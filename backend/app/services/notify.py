"""New-listing broadcast to bot subscribers.

`publisher.publish` schedules one broadcast per fresh publication. It runs as a
background asyncio task with its own DB session (the request's session is
closed by then). Pacing: one message per SEND_INTERVAL (<= 20/s) and a global
lock so two broadcasts never run in parallel. RetryAfter is honoured and the
same recipient retried; TelegramForbiddenError (blocked the bot / deactivated)
marks the subscriber `blocked_at` so later broadcasts skip them; any other
error is logged and the recipient skipped. The outcome is stored as an
`events` row of type `notified` with {sent, failed, blocked}.

Task references live in `_pending`, otherwise the event loop may GC a running
task; `drain()` awaits them (tests, shutdown)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import Event, EventType
from app.services import subscribers

log = logging.getLogger(__name__)

SEND_INTERVAL = 1 / 20  # seconds between messages: at most 20 per second
MAX_ATTEMPTS = 3  # per recipient, RetryAfter only
CAPTION_PREFIX = "Новое объявление\n\n"

_broadcast_lock = asyncio.Lock()
_pending: set[asyncio.Task] = set()

# indirection so tests can skip real waiting and record the requested delays
_sleep = asyncio.sleep


@dataclass
class BroadcastStats:
    sent: int = 0
    failed: int = 0
    blocked: int = 0


@dataclass(frozen=True)
class Listing:
    """Everything needed to announce one lot, captured at publish time."""

    item_id: int
    public_id: str
    photo: str  # tg_file_id of the cover
    caption: str  # already prefixed with CAPTION_PREFIX
    keyboard: InlineKeyboardMarkup


def spawn(coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
    """create_task that keeps a strong reference until the task is done."""
    task = asyncio.create_task(coro)
    _pending.add(task)
    task.add_done_callback(_pending.discard)
    return task


async def drain(timeout: float | None = None) -> None:
    """Wait for pending broadcasts; on timeout cancel what is left."""
    while _pending:
        tasks = list(_pending)
        done, not_done = await asyncio.wait(tasks, timeout=timeout)
        if not_done:
            log.warning("cancelling %s unfinished broadcast task(s)", len(not_done))
            for task in not_done:
                task.cancel()
            await asyncio.gather(*not_done, return_exceptions=True)
            return


async def _send_one(bot: Bot, chat_id: int, listing: Listing) -> str:
    """'sent' | 'blocked' | 'failed'."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=listing.photo,
                caption=listing.caption,
                parse_mode="HTML",
                reply_markup=listing.keyboard,
            )
            return "sent"
        except TelegramRetryAfter as exc:
            log.warning("broadcast RetryAfter %ss (chat %s, attempt %s)", exc.retry_after, chat_id, attempt)
            if attempt == MAX_ATTEMPTS:
                return "failed"
            await _sleep(exc.retry_after + 0.5)
        except TelegramForbiddenError:
            return "blocked"
        except Exception:  # noqa: BLE001 - one bad recipient must not stop the broadcast
            log.exception("broadcast of %s to %s failed", listing.public_id, chat_id)
            return "failed"
    return "failed"  # pragma: no cover


async def broadcast(
    bot: Bot,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    listing: Listing,
) -> BroadcastStats:
    stats = BroadcastStats()
    async with _broadcast_lock:
        async with session_factory() as session:
            chat_ids = await subscribers.recipients(session, exclude=set(settings.admin_ids))
            for chat_id in chat_ids:
                outcome = await _send_one(bot, chat_id, listing)
                if outcome == "sent":
                    stats.sent += 1
                elif outcome == "blocked":
                    stats.blocked += 1
                    await subscribers.mark_blocked(session, chat_id)
                else:
                    stats.failed += 1
                await _sleep(SEND_INTERVAL)
            payload = {"sent": stats.sent, "failed": stats.failed, "blocked": stats.blocked}
            session.add(Event(item_id=listing.item_id, type=EventType.notified.value, payload_json=json.dumps(payload)))
            await session.commit()
    log.info("broadcast %s: %s", listing.public_id, payload)
    return stats


async def _safe_broadcast(bot, settings, session_factory, listing: Listing) -> BroadcastStats:
    try:
        return await broadcast(bot, settings, session_factory, listing)
    except Exception:  # noqa: BLE001 - detached task: log, never raise into the void
        log.exception("broadcast of %s crashed", listing.public_id)
        return BroadcastStats()


def schedule(
    bot: Bot,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    listing: Listing,
) -> asyncio.Task:
    return spawn(_safe_broadcast(bot, settings, session_factory, listing))


async def report_when_done(bot: Bot, chat_id: int, task: asyncio.Task, public_id: str) -> None:
    """Tell the publishing admin how many subscribers got the lot."""
    stats: BroadcastStats = await task
    if not stats.sent:
        return  # nothing to report: no noise in the admin chat
    try:
        await bot.send_message(chat_id=chat_id, text=f"Лот {public_id}: разослано подписчикам: {stats.sent}")
    except Exception:  # noqa: BLE001
        log.exception("failed to report broadcast of %s", public_id)
