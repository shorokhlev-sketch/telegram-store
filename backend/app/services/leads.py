"""Buyer leads: persist the lead and notify the seller.

Two sources (`LeadSource`):
  - `app`: buyer identified by Telegram — WebApp initData (Mini App opened from
    the menu button / inline buttons / t.me links) or `web_app_data` sent to the
    bot from a Mini App opened via the reply keyboard (no initData there).
    Both go through `create_app_lead`.
  - `site`: buyer typed a name + contact on the plain website; guarded by a
    honeypot field, a per-IP rate limit and a duplicate-contact window at the
    API layer (app/api/leads.py) before `create_lead` is ever called.

Rate limiting and duplicate suppression are in-memory / DB-query based
(fine for a single-process MVP, per CLAUDE.md).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from html import escape
from typing import Any

from aiogram import Bot
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Event, EventType, Item, ItemStatus, Lead, LeadSource
from app.services import post_render

log = logging.getLogger(__name__)

SITE_RATE_LIMIT_WINDOW_S = 3600
SITE_RATE_LIMIT_MAX = 5
DUPLICATE_WINDOW = timedelta(minutes=10)

_site_lead_hits: dict[str, list[float]] = {}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def check_site_rate_limit(client_ip: str) -> bool:
    """True and records a hit when `client_ip` is still under the per-hour limit."""
    now = time.monotonic()
    window_start = now - SITE_RATE_LIMIT_WINDOW_S
    hits = [t for t in _site_lead_hits.get(client_ip, []) if t >= window_start]
    if len(hits) >= SITE_RATE_LIMIT_MAX:
        _site_lead_hits[client_ip] = hits
        return False
    hits.append(now)
    _site_lead_hits[client_ip] = hits
    return True


def reset_rate_limits() -> None:
    """Test helper: this state is module-global and otherwise leaks between tests."""
    _site_lead_hits.clear()


async def has_recent_duplicate(session: AsyncSession, item_id: int, contact: str) -> bool:
    """True when the same (case-insensitive) contact already filed a lead for
    this item within the last 10 minutes."""
    cutoff = now_utc() - DUPLICATE_WINDOW
    stmt = select(Lead.id).where(
        Lead.item_id == item_id,
        Lead.created_at >= cutoff,
        func.lower(Lead.buyer_contact) == contact.strip().lower(),
    )
    return (await session.scalar(stmt)) is not None


_USERNAME_RE = re.compile(r"@[A-Za-z][A-Za-z0-9_]{3,31}")


def _buyer_link(source: str, contact: str, buyer_tg_id: int | None) -> str | None:
    if source == LeadSource.app.value and buyer_tg_id:
        return f'<a href="tg://user?id={buyer_tg_id}">Открыть профиль</a>'
    if _USERNAME_RE.fullmatch(contact):
        return f'<a href="https://t.me/{contact[1:]}">Открыть профиль</a>'
    return None


def build_notification_text(
    item: Item, *, source: str, name: str, contact: str, buyer_tg_id: int | None
) -> str:
    title = post_render.item_title(item)
    price_text = post_render.format_price(item.price) if item.price is not None else "цена не указана"
    source_label = "Приложение" if source == LeadSource.app.value else "Сайт"
    lines = [
        f"Заявка: {escape(title)}, {escape(price_text)}, лот {escape(item.public_id)}",
        f"Источник: {source_label}",
        f"Покупатель: {escape(name)}",
        f"Контакт: {escape(contact)}",
    ]
    link = _buyer_link(source, contact, buyer_tg_id)
    if link:
        lines.append(link)
    return "\n".join(lines)


@dataclass(frozen=True)
class LeadResult:
    lead: Lead
    notified: bool


async def create_lead(
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
    item: Item,
    *,
    source: str,
    name: str,
    contact: str,
    buyer_tg_id: int | None = None,
    buyer_username: str | None = None,
) -> LeadResult:
    """Persist the lead and notify the seller, unless it's a duplicate of a
    very recent one (same item + contact): still saved, notification skipped."""
    duplicate = await has_recent_duplicate(session, item.id, contact)

    lead = Lead(
        item_id=item.id,
        source=source,
        buyer_name=name[:128],
        buyer_contact=contact[:128],
        buyer_tg_id=buyer_tg_id,
        buyer_username=buyer_username,
        created_at=now_utc(),
    )
    session.add(lead)
    session.add(Event(item_id=item.id, type=EventType.lead.value, payload_json=f'{{"source": "{source}"}}'))
    await session.commit()

    if duplicate:
        return LeadResult(lead, notified=False)

    chat_id = settings.leads_chat_id_effective
    if not chat_id:
        log.warning("no LEADS_CHAT_ID/admin configured, lead %s not announced", lead.id)
        return LeadResult(lead, notified=False)

    text = build_notification_text(item, source=source, name=name, contact=contact, buyer_tg_id=buyer_tg_id)
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    except Exception:  # noqa: BLE001 - notification failure must not fail the request
        log.exception("failed to notify seller about lead %s", lead.id)
        return LeadResult(lead, notified=False)

    lead.notified_at = now_utc()
    await session.commit()
    return LeadResult(lead, notified=True)


async def find_item(session: AsyncSession, public_id: str) -> Item | None:
    """The lot a lead is about; drafts are invisible to buyers (None)."""
    item = await session.scalar(select(Item).where(Item.public_id == public_id.strip()))
    if item is None or item.status == ItemStatus.draft.value:
        return None
    return item


def is_open(item: Item) -> bool:
    return item.status == ItemStatus.active.value


async def create_app_lead(
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
    item: Item,
    *,
    user: Mapping[str, Any],
    phone: str | None = None,
) -> LeadResult:
    """Lead from a Telegram-identified buyer. `user` is shaped like initData's
    `user` (id, first_name, last_name, username). Contact: phone if given, else
    @username, else a tg://user deep link."""
    first_name = (user.get("first_name") or "").strip()
    last_name = (user.get("last_name") or "").strip()
    name = " ".join(p for p in (first_name, last_name) if p) or "Покупатель"
    username = user.get("username") or None
    user_id = user.get("id")
    phone = (phone or "").strip()
    if phone:
        contact = phone
    elif username:
        contact = f"@{username}"
    elif user_id is not None:
        contact = f"tg://user?id={user_id}"
    else:
        contact = "не указан"
    return await create_lead(
        session,
        bot,
        settings,
        item,
        source=LeadSource.app.value,
        name=name,
        contact=contact,
        buyer_tg_id=int(user_id) if user_id is not None else None,
        buyer_username=username,
    )
