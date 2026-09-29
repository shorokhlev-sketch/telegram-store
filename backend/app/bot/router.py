from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommandScopeChat,
    BotCommandScopeDefault,
    MenuButtonWebApp,
    TelegramObject,
    WebAppInfo,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.admin import build_router as build_admin_router
from app.bot.client import IsAdmin, build_common_router, build_customer_router
from app.bot.wizard import build_router as build_wizard_router
from app.config import Settings

log = logging.getLogger(__name__)


class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self._factory() as session:
            data["session"] = session
            return await handler(event, data)


def create_bot(settings: Settings) -> Bot:
    return Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))


def create_dispatcher(settings: Settings, session_factory: async_sessionmaker[AsyncSession]) -> Dispatcher:
    """Router order matters (first match wins):
    1. common — /start and web_app_data for everyone;
    2. admin — /sold, /list, /backup, «Новый лот»; admins only;
    3. wizard — /new, /cancel and the FSM; admins only. After admin so its
       catch-all state handlers (e.g. Wizard.media, Wizard.brand) never swallow
       admin commands or the «Новый лот» button;
    4. customer — non-admins: notification toggles, hint, «Недоступно»."""
    dp = Dispatcher(storage=MemoryStorage(), settings=settings)
    dp.update.outer_middleware(DbSessionMiddleware(session_factory))

    admin_router = build_admin_router()
    wizard_router = build_wizard_router()
    for router in (admin_router, wizard_router):
        router.message.filter(IsAdmin())
        router.callback_query.filter(IsAdmin())

    dp.include_router(build_common_router())
    dp.include_router(admin_router)
    dp.include_router(wizard_router)
    dp.include_router(build_customer_router())
    return dp


MENU_BUTTON_TEXT = "Магазин"


async def configure_bot_ui(bot: Bot, settings: Settings) -> None:
    """Chat menu button «Магазин» (opens the app WITH initData); command lists
    are removed. Every call is best-effort: a failure is logged, startup goes on."""
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text=MENU_BUTTON_TEXT, web_app=WebAppInfo(url=settings.app_url))
        )
    except Exception:  # noqa: BLE001
        log.exception("set_chat_menu_button failed")
    # No command lists anywhere (Lev 28.09: Telegram suggested commands on top of
    # the two keyboard buttons). Commands still work when typed; we also wipe
    # lists set by earlier deploys.
    try:
        await bot.delete_my_commands(scope=BotCommandScopeDefault())
    except Exception:  # noqa: BLE001
        log.exception("delete_my_commands (default) failed")
    for admin_id in settings.admin_ids:
        try:
            await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception:  # noqa: BLE001 - e.g. the admin never opened the bot
            log.exception("delete_my_commands for admin %s failed", admin_id)
