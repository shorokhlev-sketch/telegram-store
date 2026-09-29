"""Admin commands: `/sold <public_id>`, `/list` with a "Продано" button per
lot, `/backup` for an on-demand copy of the database, and the «Новый лот»
reply-keyboard button (same as /new). Access is enforced by an `IsAdmin`
router filter (see router.py), so handlers here assume the caller is an admin.
This router is included before the wizard's, so none of these can be
swallowed by a wizard step waiting for text."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bot.keyboards import LEGACY_NEW_LOT_BUTTONS, NEW_LOT_BUTTON, SoldCb, sold_confirm_kb, sold_list_kb
from app.bot.wizard import cmd_new
from app.config import Settings
from app.db.models import Item, ItemStatus
from app.services import backup, sales

log = logging.getLogger(__name__)

MAX_LIST_ITEMS = 50


async def _load_by_public_id(session: AsyncSession, public_id: str) -> Item | None:
    stmt = (
        select(Item)
        .where(Item.public_id == public_id.strip().upper())
        .options(selectinload(Item.channel_messages))
    )
    return await session.scalar(stmt)


async def _active_items(session: AsyncSession) -> list[Item]:
    stmt = (
        select(Item)
        .where(Item.status == ItemStatus.active.value)
        .options(selectinload(Item.channel_messages))
        .order_by(Item.published_at.desc())
        .limit(MAX_LIST_ITEMS)
    )
    return list((await session.scalars(stmt)).all())


async def _render_list(session: AsyncSession, prefix_line: str | None = None) -> tuple[str, InlineKeyboardMarkup | None]:
    items = await _active_items(session)
    text = f"Активные лоты: {len(items)}" if items else "Активных лотов нет"
    if prefix_line:
        text = f"{prefix_line}\n{text}"
    markup = sold_list_kb(items) if items else None
    return text, markup


def _failure_note(failed_message_ids: list[int]) -> str:
    if not failed_message_ids:
        return ""
    return f"\nВнимание: не удалось удалить {len(failed_message_ids)} сообщ. в канале, лот всё равно снят с витрины."


async def cmd_sold(message: Message, session: AsyncSession, bot, settings: Settings) -> None:
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        # plain text: "<public_id>" would be parsed as an HTML tag
        await message.answer("Использование: /sold <public_id>", parse_mode=None)
        return
    public_id = parts[1].strip()
    item = await _load_by_public_id(session, public_id)
    if item is None:
        await message.answer("Лот не найден")
        return
    if item.status == ItemStatus.draft.value:
        await message.answer("Это черновик, он не опубликован")
        return
    if item.status == ItemStatus.sold.value:
        await message.answer(f"Лот {item.public_id} уже продан")
        return
    result = await sales.mark_sold(session, bot, settings, item)
    text = f"Лот {result.public_id} продан и снят из канала и с витрины."
    await message.answer(text + _failure_note(result.failed_message_ids))


async def cmd_list(message: Message, session: AsyncSession) -> None:
    text, markup = await _render_list(session)
    await message.answer(text, reply_markup=markup)


async def cmd_backup(message: Message, session: AsyncSession, bot, settings: Settings) -> None:
    if not settings.backup_chat_id:
        await message.answer("BACKUP_CHAT_ID не настроен, бэкап отправлять некуда.")
        return
    await message.answer("Делаю бэкап…")
    try:
        await backup.run_backup(bot, settings, session)
    except Exception:  # noqa: BLE001 - report to the admin, don't crash the bot
        log.exception("manual /backup failed")
        await message.answer("Бэкап не удался, смотрите логи сервера.")
        return
    await message.answer("Бэкап отправлен.")


async def cb_sold_ask(cq: CallbackQuery, callback_data: SoldCb, session: AsyncSession) -> None:
    item = await _load_by_public_id(session, callback_data.public_id)
    if item is None or item.status != ItemStatus.active.value:
        await cq.answer("Уже продан")
        text, markup = await _render_list(session)
        await cq.message.edit_text(text, reply_markup=markup)
        return
    await cq.answer()
    await cq.message.edit_text(
        f"Продано: {item.model or item.public_id}, лот {item.public_id}?",
        reply_markup=sold_confirm_kb(item.public_id),
    )


async def cb_sold_no(cq: CallbackQuery, session: AsyncSession) -> None:
    await cq.answer()
    text, markup = await _render_list(session)
    await cq.message.edit_text(text, reply_markup=markup)


async def cb_sold_yes(cq: CallbackQuery, callback_data: SoldCb, session: AsyncSession, bot, settings: Settings) -> None:
    item = await _load_by_public_id(session, callback_data.public_id)
    if item is None or item.status != ItemStatus.active.value:
        await cq.answer("Уже продан")
        text, markup = await _render_list(session)
        await cq.message.edit_text(text, reply_markup=markup)
        return
    result = await sales.mark_sold(session, bot, settings, item)
    await cq.answer("Продано")
    prefix = f"Продан: {result.public_id}" + _failure_note(result.failed_message_ids)
    text, markup = await _render_list(session, prefix_line=prefix)
    await cq.message.edit_text(text, reply_markup=markup)


def build_router() -> Router:
    router = Router(name="admin")
    router.message.register(cmd_sold, Command("sold"))
    router.message.register(cmd_list, Command("list"))
    router.message.register(cmd_backup, Command("backup"))
    router.message.register(cmd_new, F.text.in_({NEW_LOT_BUTTON, *LEGACY_NEW_LOT_BUTTONS}))
    router.callback_query.register(cb_sold_ask, SoldCb.filter(F.action == "ask"))
    router.callback_query.register(cb_sold_yes, SoldCb.filter(F.action == "yes"))
    router.callback_query.register(cb_sold_no, SoldCb.filter(F.action == "no"))
    return router
