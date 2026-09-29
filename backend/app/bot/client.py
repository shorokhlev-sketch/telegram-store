"""Everything the bot does for people who are not admins, plus the handlers
shared by everyone (/start, web_app_data).

Two routers:
  - `build_common_router()` is included first: /start (admin help or customer
    welcome) and `web_app_data` (a "Купить" from the Mini App opened via the
    reply keyboard — such an app has no initData and can only sendData()).
    Being first, the wizard's catch-all state handlers never see these.
  - `build_customer_router()` is included last and only matches non-admins:
    notification toggles, a one-line hint for anything else, and «Недоступно»
    for stale admin inline buttons.
"""

from __future__ import annotations

import json
import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import CommandStart, Filter
from aiogram.types import CallbackQuery, Message, TelegramObject, User
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.config import Settings
from app.services import leads, subscribers

log = logging.getLogger(__name__)

ADMIN_HELP = "Здравствуйте."
NOTIFY_OFF_TEXT = "Уведомления выключены."
NOTIFY_ON_TEXT = "Уведомления включены."
LEAD_SENT_TEXT = "Заявка отправлена, продавец свяжется с вами."
LEAD_SOLD_TEXT = "Этот лот уже продан."
LEAD_NOT_FOUND_TEXT = "Лот не найден."
NOT_AVAILABLE = "Недоступно"


def customer_welcome(settings: Settings, subscribed: bool) -> str:
    shop = settings.shop_name.strip()
    # No explainer copy (Lev 28.09): the keyboard already says what to press.
    return f"Здравствуйте! Это {escape(shop)}." if shop else "Здравствуйте!"


class IsAdmin(Filter):
    """Router-level access filter: `IsAdmin()` for admin routers,
    `IsAdmin(False)` for customer-only handlers."""

    def __init__(self, admin: bool = True) -> None:
        self.admin = admin

    async def __call__(self, event: TelegramObject, settings: Settings, event_from_user: User | None = None) -> bool:
        is_admin = event_from_user is not None and settings.is_admin(event_from_user.id)
        return is_admin is self.admin


# ---------- shared ----------


async def cmd_start(message: Message, session: AsyncSession, settings: Settings) -> None:
    user = message.from_user
    if user is not None and settings.is_admin(user.id):
        await message.answer(ADMIN_HELP, reply_markup=kb.admin_reply_kb(settings))
        return
    if user is None:  # pragma: no cover - anonymous group admins etc.
        return
    row = await subscribers.on_start(session, user)
    await message.answer(
        customer_welcome(settings, row.subscribed),
        reply_markup=kb.customer_reply_kb(settings, row.subscribed),
    )


def _parse_lead_payload(raw: str) -> tuple[str, str | None] | None:
    """`{"t":"lead","id":"<public_id>","phone":"<optional>"}` -> (public_id, phone)."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("t") != "lead":
        return None
    public_id = data.get("id")
    if not isinstance(public_id, str) or not public_id.strip():
        return None
    phone = data.get("phone")
    phone = phone.strip()[:64] if isinstance(phone, str) else ""
    return public_id.strip()[:16], phone or None


async def on_web_app_data(message: Message, session: AsyncSession, settings: Settings) -> None:
    raw = message.web_app_data.data if message.web_app_data else ""
    parsed = _parse_lead_payload(raw)
    user = message.from_user
    if parsed is None or user is None:
        log.warning("ignoring web_app_data from %s: %r", user.id if user else None, raw[:200])
        return
    public_id, phone = parsed
    item = await leads.find_item(session, public_id)
    if item is None:
        await message.answer(LEAD_NOT_FOUND_TEXT)
        return
    if not leads.is_open(item):
        await message.answer(LEAD_SOLD_TEXT)
        return
    buyer = {
        "id": user.id,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "username": user.username,
    }
    await leads.create_app_lead(session, message.bot, settings, item, user=buyer, phone=phone)
    await message.answer(LEAD_SENT_TEXT)


def build_common_router() -> Router:
    router = Router(name="common")
    router.message.register(cmd_start, CommandStart())
    router.message.register(on_web_app_data, F.web_app_data)
    return router


# ---------- customers only ----------


async def _toggle(message: Message, session: AsyncSession, settings: Settings, value: bool) -> None:
    row = await subscribers.set_subscribed(session, message.from_user, value)
    text = NOTIFY_ON_TEXT if row.subscribed else NOTIFY_OFF_TEXT
    await message.answer(text, reply_markup=kb.customer_reply_kb(settings, row.subscribed))


async def notify_off(message: Message, session: AsyncSession, settings: Settings) -> None:
    await _toggle(message, session, settings, False)


async def notify_on(message: Message, session: AsyncSession, settings: Settings) -> None:
    await _toggle(message, session, settings, True)


async def customer_hint(message: Message, session: AsyncSession, settings: Settings) -> None:
    """Free text from a customer: no hint reply (the keyboard speaks for itself);
    only record that the user is alive (clears blocked_at)."""
    if message.from_user:
        await subscribers.touch(session, message.from_user)


async def customer_callback(cq: CallbackQuery) -> None:
    # customers have no inline callbacks of their own: this is an admin button
    await cq.answer(NOT_AVAILABLE, show_alert=True)


def build_customer_router() -> Router:
    router = Router(name="customer")
    router.message.filter(IsAdmin(False), F.chat.type == "private")
    router.callback_query.filter(IsAdmin(False))
    router.message.register(notify_off, F.text.in_({kb.NOTIFY_OFF_BUTTON, *kb.LEGACY_NOTIFY_OFF_BUTTONS}))
    router.message.register(notify_on, F.text.in_({kb.NOTIFY_ON_BUTTON, *kb.LEGACY_NOTIFY_ON_BUTTONS}))
    router.message.register(customer_hint)
    router.callback_query.register(customer_callback)
    return router
