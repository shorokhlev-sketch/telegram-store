import pytest
from sqlalchemy import select

from app.bot.router import create_dispatcher
from app.config import get_settings
from app.db.models import ChannelMessage, Item
from app.services.drafts import now_utc
from tests.bot_utils import (
    ADMIN_ID,
    STRANGER_ID,
    FakeBot,
    callback_update,
    feed,
    photo_update,
    text_update,
)
from tests.db_utils import make_session_factory


@pytest.fixture
async def env(tmp_path):
    factory = await make_session_factory(tmp_path / "admin.db")
    settings = get_settings()
    bot = FakeBot()
    dp = create_dispatcher(settings, factory)
    yield settings, factory, bot, dp
    await factory.kw["bind"].dispose()


async def _seed_active_item(factory, *, public_id: str, model: str = "iPhone 15 Pro", price: int = 75000) -> Item:
    async with factory() as session:
        item = Item(
            public_id=public_id,
            status="active",
            created_by=ADMIN_ID,
            model=model,
            storage_gb=256,
            color="Black",
            battery_health=90,
            grade="A",
            kit_json="[]",
            price=price,
            note="",
            published_at=now_utc(),
        )
        session.add(item)
        await session.flush()
        session.add(ChannelMessage(item_id=item.id, chat_id=-100123, message_id=1, kind="media"))
        await session.commit()
        return item


def _first_button(markup):
    return markup.inline_keyboard[0][0]


async def test_sold_happy_path(env):
    _, factory, bot, dp = env
    await _seed_active_item(factory, public_id="AAAAAAAA")

    await feed(dp, bot, text_update("/sold aaaaaaaa"))  # lower-case, trimmed by handler

    assert bot.last_text() == "Лот AAAAAAAA продан и снят из канала и с витрины."
    assert bot.deleted_message_ids() == [1]
    async with factory() as s:
        item = await s.scalar(select(Item).where(Item.public_id == "AAAAAAAA"))
    assert item.status == "sold"
    assert item.sold_at is not None


async def test_sold_unknown_public_id(env):
    _, factory, bot, dp = env
    await feed(dp, bot, text_update("/sold NOSUCHID"))
    assert bot.last_text() == "Лот не найден"


async def test_sold_already_sold(env):
    _, factory, bot, dp = env
    await _seed_active_item(factory, public_id="BBBBBBBB")
    await feed(dp, bot, text_update("/sold BBBBBBBB"))
    bot.reset()
    await feed(dp, bot, text_update("/sold BBBBBBBB"))
    assert bot.last_text() == "Лот BBBBBBBB уже продан"
    assert bot.deleted_message_ids() == []  # no second attempt to touch Telegram


async def test_sold_without_arg_shows_usage(env):
    _, factory, bot, dp = env
    await feed(dp, bot, text_update("/sold"))
    assert bot.last_text() == "Использование: /sold <public_id>"


async def test_list_empty(env):
    _, factory, bot, dp = env
    await feed(dp, bot, text_update("/list"))
    assert bot.last_text() == "Активных лотов нет"
    assert bot.last_send().reply_markup is None


async def test_list_ask_yes_flow(env):
    _, factory, bot, dp = env
    await _seed_active_item(factory, public_id="LIST0001", model="iPhone 14", price=50000)

    await feed(dp, bot, text_update("/list"))
    assert bot.last_text() == "Активные лоты: 1"
    markup = bot.last_send().reply_markup
    button = _first_button(markup)
    assert "iPhone 14" in button.text and "LIST0001" in button.text and "50 000" in button.text

    await feed(dp, bot, callback_update(button.callback_data))
    asked = bot.edited_texts()[-1]
    assert asked.text == "Продано: iPhone 14, лот LIST0001?"
    yes_button = next(
        b for row in asked.reply_markup.inline_keyboard for b in row if b.text == "Да, продано"
    )

    await feed(dp, bot, callback_update(yes_button.callback_data))
    done = bot.edited_texts()[-1]
    assert done.text == "Продан: LIST0001\nАктивных лотов нет"
    assert done.reply_markup is None
    assert bot.callback_answers()[-1].text == "Продано"

    async with factory() as s:
        item = await s.scalar(select(Item).where(Item.public_id == "LIST0001"))
    assert item.status == "sold"


async def test_list_ask_no_flow_keeps_item_active(env):
    _, factory, bot, dp = env
    await _seed_active_item(factory, public_id="LIST0002")
    await feed(dp, bot, text_update("/list"))
    button = _first_button(bot.last_send().reply_markup)

    await feed(dp, bot, callback_update(button.callback_data))
    asked = bot.edited_texts()[-1]
    no_button = next(b for row in asked.reply_markup.inline_keyboard for b in row if b.text == "Отмена")

    await feed(dp, bot, callback_update(no_button.callback_data))
    back = bot.edited_texts()[-1]
    assert back.text == "Активные лоты: 1"

    async with factory() as s:
        item = await s.scalar(select(Item).where(Item.public_id == "LIST0002"))
    assert item.status == "active"


async def test_stale_ask_on_already_sold_lot(env):
    _, factory, bot, dp = env
    await _seed_active_item(factory, public_id="LIST0003")
    await feed(dp, bot, text_update("/list"))
    button = _first_button(bot.last_send().reply_markup)

    # Sell it out-of-band, then press the stale "ask" button from the old list.
    await feed(dp, bot, text_update("/sold LIST0003"))
    await feed(dp, bot, callback_update(button.callback_data))
    assert bot.callback_answers()[-1].text == "Уже продан"
    refreshed = bot.edited_texts()[-1]
    assert refreshed.text == "Активных лотов нет"


async def test_stranger_sold_and_list_are_blocked(env):
    settings, factory, bot, dp = env
    await _seed_active_item(factory, public_id="STRANGE1")

    # customers get no admin reply (and no hint either)
    await feed(dp, bot, text_update("/sold STRANGE1", user_id=STRANGER_ID))
    assert bot.sent_texts() == []
    assert bot.deleted_message_ids() == []

    bot.reset()
    await feed(dp, bot, text_update("/list", user_id=STRANGER_ID))
    assert bot.sent_texts() == []

    async with factory() as s:
        item = await s.scalar(select(Item).where(Item.public_id == "STRANGE1"))
    assert item.status == "active"


async def test_sold_works_while_admin_mid_wizard(env):
    _, factory, bot, dp = env
    await feed(dp, bot, text_update("/new"))
    assert "фото" in bot.last_text().lower()  # now in Wizard.media state

    await _seed_active_item(factory, public_id="MIDWIZ01")
    bot.reset()
    await feed(dp, bot, text_update("/sold MIDWIZ01"))
    assert bot.last_text() == "Лот MIDWIZ01 продан и снят из канала и с витрины."

    # the wizard's catch-all for Wizard.media must not have swallowed the command
    assert "Жду фото" not in bot.last_text()

    # a real photo still resumes the wizard normally afterwards
    bot.reset()
    await feed(dp, bot, photo_update("z"))
    assert bot.last_text().startswith("Файлов: 1/10")
