import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.bot_utils import FakeBot, text_update


@pytest.fixture(autouse=True)
def _no_webhook(monkeypatch):
    monkeypatch.setenv("SET_WEBHOOK_ON_START", "0")
    from app.config import get_settings

    get_settings.cache_clear()


async def test_webhook_rejects_bad_secret_and_accepts_good():
    async with app.router.lifespan_context(app):
        fake = FakeBot()
        app.state.bot = fake
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/tg/webhook", json=text_update("/start"))
            assert r.status_code == 403
            r = await client.post(
                "/tg/webhook",
                json=text_update("/start"),
                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            )
            assert r.status_code == 403
            r = await client.post(
                "/tg/webhook",
                json=text_update("/start"),
                headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
            )
            assert r.status_code == 200
        assert fake.last_text() == "Здравствуйте."


class _FailingUiBot(FakeBot):
    async def __call__(self, method, request_timeout=None):
        from aiogram.exceptions import TelegramBadRequest
        from aiogram.methods import DeleteMyCommands, SetChatMenuButton

        if isinstance(method, (SetChatMenuButton, DeleteMyCommands)):
            self.calls.append(method)
            raise TelegramBadRequest(method=method, message="Bad Request: chat not found")
        return await super().__call__(method, request_timeout)


def _start_with_webhook(monkeypatch, bot):
    import app.main as main_module
    from app.config import get_settings

    monkeypatch.setenv("SET_WEBHOOK_ON_START", "1")
    get_settings.cache_clear()
    monkeypatch.setattr(main_module, "create_bot", lambda settings: bot)


async def test_startup_sets_webhook_menu_button_and_clears_commands(monkeypatch):
    from aiogram.methods import DeleteMyCommands, SetChatMenuButton, SetWebhook
    from aiogram.types import BotCommandScopeChat, BotCommandScopeDefault, MenuButtonWebApp

    fake = FakeBot()
    _start_with_webhook(monkeypatch, fake)
    async with app.router.lifespan_context(app):
        pass

    assert [type(c) for c in fake.calls] == [SetWebhook, SetChatMenuButton, DeleteMyCommands, DeleteMyCommands, DeleteMyCommands]
    menu = fake.calls[1].menu_button
    assert isinstance(menu, MenuButtonWebApp)
    assert menu.text == "Магазин" and menu.web_app.url == "https://example.test/"
    assert fake.calls[1].chat_id is None  # default menu button for all private chats

    default = fake.calls[2]
    assert isinstance(default.scope, BotCommandScopeDefault)
    admin_calls = fake.calls[3:]
    assert [c.scope.chat_id for c in admin_calls] == [111, 222]
    assert all(isinstance(c.scope, BotCommandScopeChat) for c in admin_calls)


async def test_startup_survives_menu_and_command_failures(monkeypatch):
    fake = _FailingUiBot()
    _start_with_webhook(monkeypatch, fake)
    async with app.router.lifespan_context(app):
        assert app.state.bot is fake
    assert len(fake.calls) == 5  # every call attempted despite failures


async def test_startup_without_webhook_touches_nothing(monkeypatch):
    import app.main as main_module

    fake = FakeBot()
    monkeypatch.setattr(main_module, "create_bot", lambda settings: fake)
    async with app.router.lifespan_context(app):
        pass
    assert fake.calls == []


async def test_fake_bot_rejects_html_telegram_would_reject():
    """Regression: '/sold <public_id>' in an HTML message broke /start on lab (28.09)."""
    import pytest

    from tests.bot_utils import FakeBot

    bot = FakeBot()
    with pytest.raises(AssertionError, match="public_id"):
        await bot.send_message(chat_id=1, text="/sold <public_id> — отметить")
    await bot.send_message(chat_id=1, text="<b>ok</b> <a href='x'>link</a>")
    await bot.send_message(chat_id=1, text="/sold <public_id>", parse_mode=None)
