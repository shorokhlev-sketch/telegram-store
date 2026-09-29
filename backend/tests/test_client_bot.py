"""Stage 11: persistent reply keyboards, customers, web_app_data leads."""

import json

import pytest
from aiogram.types import ReplyKeyboardMarkup
from sqlalchemy import select

from app.bot import client
from app.bot.keyboards import NEW_LOT_BUTTON, NOTIFY_OFF_BUTTON, NOTIFY_ON_BUTTON, SHOP_BUTTON, WizardCb
from app.bot.router import create_dispatcher
from app.config import get_settings
from app.db.models import Item, Lead, Subscriber
from app.services import notify, publisher
from app.services.drafts import now_utc
from tests.bot_utils import (
    ADMIN_ID,
    STRANGER_ID,
    FakeBot,
    callback_update,
    feed,
    photo_update,
    text_update,
    web_app_data_update,
)
from tests.db_utils import make_session_factory
from tests.test_wizard import _run_to_preview, draft


@pytest.fixture
async def env(tmp_path):
    factory = await make_session_factory(tmp_path / "client.db")
    settings = get_settings()
    bot = FakeBot()
    dp = create_dispatcher(settings, factory)
    yield settings, factory, bot, dp
    await factory.kw["bind"].dispose()


def wiz(action: str, value: str = "") -> str:
    return WizardCb(action=action, value=value).pack()


def reply_rows(markup: ReplyKeyboardMarkup) -> list[list[str]]:
    return [[b.text for b in row] for row in markup.keyboard]


async def _subscriber(factory, tg_id: int) -> Subscriber | None:
    async with factory() as s:
        return await s.get(Subscriber, tg_id)


async def _seed_item(factory, public_id: str, status: str = "active") -> None:
    async with factory() as s:
        s.add(
            Item(
                public_id=public_id,
                status=status,
                created_by=ADMIN_ID,
                category="iphone",
                device_type="phone",
                brand="Apple",
                model="iPhone 15 Pro",
                storage_gb=256,
                color="Black",
                grade="A",
                kit_json="[]",
                price=75000,
                published_at=now_utc(),
            )
        )
        await s.commit()


# ---------- customers ----------


async def test_customer_start_subscribes_and_shows_customer_keyboard(env):
    settings, factory, bot, dp = env
    await feed(dp, bot, text_update("/start", user_id=STRANGER_ID))

    sent = bot.last_send()
    assert sent.text == "Здравствуйте!"
    markup = sent.reply_markup
    assert isinstance(markup, ReplyKeyboardMarkup)
    assert markup.is_persistent is True and markup.resize_keyboard is True
    assert reply_rows(markup) == [[SHOP_BUTTON, NOTIFY_OFF_BUTTON]]
    shop = markup.keyboard[0][0]
    assert shop.web_app is not None and shop.web_app.url == "https://example.test/"
    assert markup.keyboard[0][1].web_app is None

    row = await _subscriber(factory, STRANGER_ID)
    assert row is not None and row.subscribed is True and row.blocked_at is None
    assert (row.username, row.first_name) == ("lev", "Lev")


async def test_customer_start_with_payload_and_shop_name(env, monkeypatch):
    monkeypatch.setenv("SHOP_NAME", "Ябл<о>ко")
    get_settings.cache_clear()
    settings = get_settings()
    _, factory, bot, _ = env
    dp = create_dispatcher(settings, factory)

    await feed(dp, bot, text_update("/start item_ABC", user_id=STRANGER_ID))
    assert bot.last_text() == "Здравствуйте! Это Ябл&lt;о&gt;ко."
    assert (await _subscriber(factory, STRANGER_ID)).subscribed is True


async def test_toggle_off_and_on(env):
    settings, factory, bot, dp = env
    await feed(dp, bot, text_update("/start", user_id=STRANGER_ID))

    await feed(dp, bot, text_update(NOTIFY_OFF_BUTTON, user_id=STRANGER_ID))
    assert bot.last_text() == "Уведомления выключены."
    assert reply_rows(bot.last_send().reply_markup) == [[SHOP_BUTTON, NOTIFY_ON_BUTTON]]
    assert (await _subscriber(factory, STRANGER_ID)).subscribed is False

    # /start again keeps the explicit opt-out
    await feed(dp, bot, text_update("/start", user_id=STRANGER_ID))
    assert bot.last_text() == "Здравствуйте!"  # state is shown by the toggle button, not by text
    assert reply_rows(bot.last_send().reply_markup) == [[SHOP_BUTTON, NOTIFY_ON_BUTTON]]
    assert (await _subscriber(factory, STRANGER_ID)).subscribed is False

    await feed(dp, bot, text_update(NOTIFY_ON_BUTTON, user_id=STRANGER_ID))
    assert bot.last_text() == "Уведомления включены."
    assert reply_rows(bot.last_send().reply_markup) == [[SHOP_BUTTON, NOTIFY_OFF_BUTTON]]
    assert (await _subscriber(factory, STRANGER_ID)).subscribed is True


async def test_customer_random_text_and_sticker_get_no_reply(env):
    """No explainer hints (Lev 28.09): free text from a customer is not answered."""
    settings, factory, bot, dp = env
    await feed(dp, bot, text_update("сколько стоит?", user_id=STRANGER_ID))
    assert bot.sent_texts() == []
    # never started: no row created
    assert await _subscriber(factory, STRANGER_ID) is None

    await feed(dp, bot, photo_update("zz", user_id=STRANGER_ID))
    # admin-only button text from a customer is just text
    await feed(dp, bot, text_update(NEW_LOT_BUTTON, user_id=STRANGER_ID))
    assert bot.sent_texts() == []
    async with factory() as s:
        assert (await s.scalar(select(Item))) is None


async def test_hint_unblocks_known_customer(env):
    settings, factory, bot, dp = env
    await feed(dp, bot, text_update("/start", user_id=STRANGER_ID))
    async with factory() as s:
        row = await s.get(Subscriber, STRANGER_ID)
        row.blocked_at = now_utc()
        await s.commit()
    await feed(dp, bot, text_update("привет", user_id=STRANGER_ID))
    row = await _subscriber(factory, STRANGER_ID)
    assert row.blocked_at is None and row.subscribed is True
    assert reply_rows(bot.last_send().reply_markup) == [[SHOP_BUTTON, NOTIFY_OFF_BUTTON]]


async def test_customer_callback_on_admin_button_is_unavailable(env):
    settings, factory, bot, dp = env
    await feed(dp, bot, callback_update(wiz("media_done"), user_id=STRANGER_ID))
    answer = bot.callback_answers()[-1]
    assert answer.text == "Недоступно" and answer.show_alert is True
    assert bot.sent_texts() == []


# ---------- admins ----------


async def test_admin_start_shows_admin_keyboard_and_does_not_subscribe(env):
    settings, factory, bot, dp = env
    await feed(dp, bot, text_update("/start"))
    sent = bot.last_send()
    assert sent.text == client.ADMIN_HELP
    assert sent.text == "Здравствуйте."
    assert reply_rows(sent.reply_markup) == [[SHOP_BUTTON, NEW_LOT_BUTTON]]
    assert sent.reply_markup.is_persistent is True
    assert sent.reply_markup.keyboard[0][0].web_app.url == "https://example.test/"
    assert await _subscriber(factory, ADMIN_ID) is None


async def test_new_lot_button_starts_wizard(env):
    settings, factory, bot, dp = env
    await feed(dp, bot, text_update(NEW_LOT_BUTTON))
    assert "фото" in bot.last_text().lower()
    async with factory() as s:
        assert (await s.scalar(select(Item))).status == "draft"


async def test_new_lot_button_mid_wizard_is_not_swallowed_by_text_step(env):
    settings, factory, bot, dp = env
    await feed(dp, bot, text_update("/new"))
    await feed(dp, bot, photo_update("p1"))
    await feed(dp, bot, callback_update(wiz("media_done")))
    await feed(dp, bot, callback_update(wiz("brand", "other")))  # «Другой»: typed manufacturer
    assert bot.last_text() == "Производитель:"

    await feed(dp, bot, text_update(NEW_LOT_BUTTON))
    # same as /new: offers the unfinished draft instead of saving the text as brand
    assert bot.last_text().startswith("Есть незавершённый черновик")
    async with factory() as s:
        item = await s.scalar(select(Item))
    assert item.brand is None

    # and the draft continues normally from the brand step
    await feed(dp, bot, callback_update(draft("continue")))
    assert bot.last_text() == "Производитель:"
    await feed(dp, bot, text_update("Lenovo"))
    async with factory() as s:
        assert (await s.scalar(select(Item))).brand == "Lenovo"


async def test_new_lot_button_mid_media_step(env):
    settings, factory, bot, dp = env
    await feed(dp, bot, text_update("/new"))
    await feed(dp, bot, text_update(NEW_LOT_BUTTON))
    assert "Жду фото" not in bot.last_text()
    assert bot.last_text().startswith("Есть незавершённый черновик")


async def test_publish_confirmation_carries_admin_keyboard_then_broadcast_report(env, monkeypatch):
    monkeypatch.setattr(publisher, "SEND_PAUSE_SECONDS", 0)
    monkeypatch.setattr(notify, "SEND_INTERVAL", 0)
    settings, factory, bot, dp = env
    await feed(dp, bot, text_update("/start", user_id=STRANGER_ID))  # one subscriber
    await _run_to_preview(dp, bot, factory)
    bot.reset()

    await feed(dp, bot, callback_update(draft("publish")))
    confirmation = bot.last_send()
    assert confirmation.text.startswith("Опубликовано: лот ")
    assert reply_rows(confirmation.reply_markup) == [[SHOP_BUTTON, NEW_LOT_BUTTON]]

    await notify.drain()
    item_public_id = (await _item(factory)).public_id
    assert bot.last_text() == f"Лот {item_public_id}: разослано подписчикам: 1"
    assert bot.last_send().chat_id == ADMIN_ID
    to_subscriber = [p for p in bot.photos() if p.chat_id == STRANGER_ID]
    assert len(to_subscriber) == 1
    assert not [p for p in bot.photos() if p.chat_id == ADMIN_ID]


async def _item(factory) -> Item:
    async with factory() as s:
        return await s.scalar(select(Item))


# ---------- web_app_data leads ----------


async def test_web_app_data_lead_creates_lead_and_notifies_seller(env):
    settings, factory, bot, dp = env
    await _seed_item(factory, "WEBAPP01")
    payload = json.dumps({"t": "lead", "id": "WEBAPP01", "phone": "+79990001122"})

    await feed(dp, bot, web_app_data_update(payload))

    assert bot.last_text() == "Заявка отправлена, продавец свяжется с вами."
    assert bot.last_send().chat_id == STRANGER_ID
    seller = [m for m in bot.calls if getattr(m, "chat_id", None) == settings.leads_chat_id_effective]
    assert len(seller) == 1
    assert "WEBAPP01" in seller[0].text and "Источник: Приложение" in seller[0].text
    assert "Покупатель: Ivan Petrov" in seller[0].text and "+79990001122" in seller[0].text
    assert f'tg://user?id={STRANGER_ID}' in seller[0].text

    async with factory() as s:
        leads = (await s.scalars(select(Lead))).all()
    assert len(leads) == 1
    lead = leads[0]
    assert (lead.source, lead.buyer_contact, lead.buyer_tg_id, lead.buyer_username) == (
        "app", "+79990001122", STRANGER_ID, "buyer"
    )
    assert lead.notified_at is not None


async def test_web_app_data_lead_without_phone_uses_username_and_dedupes(env):
    settings, factory, bot, dp = env
    await _seed_item(factory, "WEBAPP02")
    payload = json.dumps({"t": "lead", "id": "WEBAPP02"})
    await feed(dp, bot, web_app_data_update(payload))
    await feed(dp, bot, web_app_data_update(payload))

    async with factory() as s:
        leads = (await s.scalars(select(Lead))).all()
    assert [l.buyer_contact for l in leads] == ["@buyer", "@buyer"]
    seller = [m for m in bot.calls if getattr(m, "chat_id", None) == settings.leads_chat_id_effective]
    assert len(seller) == 1  # duplicate within 10 min: saved, not re-announced
    assert bot.sent_texts().count("Заявка отправлена, продавец свяжется с вами.") == 2


async def test_web_app_data_lead_on_sold_item(env):
    settings, factory, bot, dp = env
    await _seed_item(factory, "WEBSOLD1", status="sold")
    await feed(dp, bot, web_app_data_update(json.dumps({"t": "lead", "id": "WEBSOLD1"})))
    assert bot.sent_texts() == ["Этот лот уже продан."]
    async with factory() as s:
        assert (await s.scalars(select(Lead))).all() == []


async def test_web_app_data_unknown_or_draft_item(env):
    settings, factory, bot, dp = env
    await _seed_item(factory, "WEBDRAF1", status="draft")
    await feed(dp, bot, web_app_data_update(json.dumps({"t": "lead", "id": "WEBDRAF1"})))
    await feed(dp, bot, web_app_data_update(json.dumps({"t": "lead", "id": "NOSUCHID"})))
    assert bot.sent_texts() == ["Лот не найден.", "Лот не найден."]


@pytest.mark.parametrize("raw", ["not json", "[1,2]", '{"t":"other","id":"X"}', '{"t":"lead"}', '{"t":"lead","id":5}'])
async def test_web_app_data_bad_payload_is_ignored(env, raw, caplog):
    settings, factory, bot, dp = env
    await feed(dp, bot, web_app_data_update(raw))
    assert bot.calls == []
    assert "ignoring web_app_data" in caplog.text


async def test_web_app_data_from_admin_mid_wizard_is_a_lead(env):
    settings, factory, bot, dp = env
    await _seed_item(factory, "WEBADM01")
    await feed(dp, bot, text_update("/new"))  # Wizard.media catch-all is active
    await feed(dp, bot, web_app_data_update(json.dumps({"t": "lead", "id": "WEBADM01"}), user_id=ADMIN_ID))
    assert "Заявка отправлена, продавец свяжется с вами." in bot.sent_texts()
    assert "Жду фото" not in bot.last_text()


def test_no_emoji_in_user_facing_bot_texts():
    """Lev 28.09: no emoji anywhere. Legacy labels are only accepted as input, never sent."""
    import re
    from pathlib import Path

    pat = re.compile("[\U0001F000-\U0001FAFF☀-⛿✀-✒✔-➿⬀-⯿️]")
    for path in Path("app").rglob("*.py"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "LEGACY_" in line:
                continue
            assert not pat.search(line), f"emoji in {path}:{n}: {line.strip()}"
