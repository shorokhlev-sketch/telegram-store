"""Bot customers and their new-listing notification preference.

A row appears on the customer's first /start (auto-subscribed) or when they
press a notification toggle. Admins are never auto-subscribed. `blocked_at` is
set by the broadcaster on TelegramForbiddenError and cleared as soon as the
user writes to the bot again (they obviously unblocked it)."""

from __future__ import annotations

from datetime import datetime, timezone

from aiogram.types import User
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Subscriber


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _profile(user: User) -> dict:
    return {
        "username": user.username[:64] if user.username else None,
        "first_name": user.first_name[:128] if user.first_name else None,
    }


async def get(session: AsyncSession, tg_id: int) -> Subscriber | None:
    return await session.get(Subscriber, tg_id, populate_existing=True)


async def _upsert(session: AsyncSession, user: User, *, subscribed: bool | None) -> Subscriber:
    """Create or refresh the row for `user` and clear `blocked_at`.
    `subscribed=None` keeps an existing preference (new rows: subscribed)."""
    now = now_utc()
    profile = _profile(user)
    values = {
        "tg_id": user.id,
        **profile,
        "subscribed": True if subscribed is None else subscribed,
        "created_at": now,
        "updated_at": now,
        "blocked_at": None,
    }
    update = {**profile, "updated_at": now, "blocked_at": None}
    if subscribed is not None:
        update["subscribed"] = subscribed
    stmt = insert(Subscriber).values(**values).on_conflict_do_update(index_elements=["tg_id"], set_=update)
    await session.execute(stmt)
    await session.commit()
    row = await get(session, user.id)
    assert row is not None
    return row


async def on_start(session: AsyncSession, user: User) -> Subscriber:
    """/start: new customers are subscribed; an explicit opt-out is kept."""
    return await _upsert(session, user, subscribed=None)


async def set_subscribed(session: AsyncSession, user: User, value: bool) -> Subscriber:
    return await _upsert(session, user, subscribed=value)


async def touch(session: AsyncSession, user: User) -> Subscriber | None:
    """Any other message from a known customer: refresh the profile and unblock.
    Unknown users are not added (only /start subscribes)."""
    if await get(session, user.id) is None:
        return None
    return await _upsert(session, user, subscribed=None)


async def is_subscribed(session: AsyncSession, tg_id: int) -> bool:
    row = await get(session, tg_id)
    return bool(row and row.subscribed)


async def recipients(session: AsyncSession, exclude: set[int] | frozenset[int] = frozenset()) -> list[int]:
    """tg_ids that should get a new-listing broadcast, oldest first."""
    stmt = (
        select(Subscriber.tg_id)
        .where(Subscriber.subscribed.is_(True), Subscriber.blocked_at.is_(None))
        .order_by(Subscriber.created_at, Subscriber.tg_id)
    )
    return [tg_id for tg_id in (await session.scalars(stmt)).all() if tg_id not in exclude]


async def mark_blocked(session: AsyncSession, tg_id: int) -> None:
    row = await get(session, tg_id)
    if row is None:
        return
    row.blocked_at = now_utc()
    row.updated_at = row.blocked_at
    await session.commit()
