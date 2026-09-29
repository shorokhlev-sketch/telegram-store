import asyncio
import json
from datetime import date

import pytest
from sqlalchemy import select

from app.bot.keyboards import DraftCb, WizardCb
from app.bot.router import create_dispatcher
from app.config import get_settings
from app.db.models import Item, Photo
from app.services import catalog
from tests.bot_utils import (
    ADMIN_ID,
    STRANGER_ID,
    FakeBot,
    button_datas,
    button_texts,
    MB,
    callback_update,
    document_update,
    feed,
    photo_update,
    text_update,
    video_update,
)
from tests.db_utils import make_session_factory


def assert_callback_sizes(bot: FakeBot) -> None:
    """Telegram rejects callback_data longer than 64 bytes."""
    for call in bot.calls:
        markup = getattr(call, "reply_markup", None)
        for row in getattr(markup, "inline_keyboard", None) or []:
            for button in row:
                if button.callback_data is not None:
                    assert len(button.callback_data.encode()) <= 64, button.callback_data


@pytest.fixture
async def env(tmp_path):
    factory = await make_session_factory(tmp_path / "wiz.db")
    settings = get_settings()
    bot = FakeBot()
    dp = create_dispatcher(settings, factory)
    yield settings, factory, bot, dp
    assert_callback_sizes(bot)  # every keyboard a test ever received
    await factory.kw["bind"].dispose()


def wiz(action: str, value: str = "") -> str:
    return WizardCb(action=action, value=value).pack()


def draft(action: str) -> str:
    return DraftCb(action=action).pack()


def model_cb(name: str) -> str:
    spec = catalog.get_apple_model(name)
    idx = [m.name for m in catalog.models_for(spec.category)].index(name)
    return wiz("model", f"{spec.category}.{idx}")


def brand_cb(name: str) -> str:
    return wiz("brand", name)


def apple_device_cb(category: str) -> str:
    return wiz("device", f"a.{category}")


def type_device_cb(device_type: str) -> str:
    return wiz("device", f"t.{device_type}")


def series_cb(category: str, key: str) -> str:
    return wiz("series", f"{category}.{key}")


def up(level: str) -> str:
    return wiz("up", level)


def rows(markup) -> list[list[str]]:
    return [[b.text for b in row] for row in markup.inline_keyboard]


BRAND_BUTTONS = [
    "Apple", "Samsung", "Xiaomi", "Huawei", "Honor", "Google", "OnePlus", "Nothing", "Sony", "Lenovo", "Другой",
]
APPLE_DEVICE_BUTTONS = ["iPhone", "iPad", "Mac", "Watch", "AirPods", "Другое", "← Назад"]
TYPE_DEVICE_BUTTONS = ["Телефон", "Планшет", "Ноутбук", "Часы", "Наушники", "Другое", "← Назад"]
IPHONE_SERIES = [
    "iPhone 18", "iPhone 17", "iPhone 16", "iPhone 15", "iPhone 14",
    "iPhone 13", "iPhone 12", "iPhone 11", "iPhone SE",
]


async def _apple_model(dp, bot, name: str) -> None:
    """Brand → device → [series] → model for a catalog Apple model."""
    spec = catalog.get_apple_model(name)
    await feed(dp, bot, callback_update(brand_cb("Apple")))
    await feed(dp, bot, callback_update(apple_device_cb(spec.category)))
    if catalog.needs_series(spec.category):
        key, _ = catalog.series_of(spec)
        await feed(dp, bot, callback_update(series_cb(spec.category, key)))
        if len(catalog.models_in_series(spec.category, key)) == 1:
            return
    assert bot.last_text() == "Модель:"
    await feed(dp, bot, callback_update(model_cb(name)))


def color_cb(model: str, color: str) -> str:
    return wiz("color", str(catalog.colors_for(model).index(color)))


def variant_cb(model: str, variant: str) -> str:
    return wiz("variant", str(catalog.variants_for(model).index(variant)))


async def _media(dp, bot, photos: int = 1) -> None:
    await feed(dp, bot, text_update("/new"))
    for i in range(photos):
        await feed(dp, bot, photo_update(f"u{i}"))
    await feed(dp, bot, callback_update(wiz("media_done")))
    assert bot.last_text() == "Производитель:"


async def _tail_to_preview(dp, bot, *, battery: str | None = "92") -> None:
    """battery → kit → grade → price → note → preview. battery=None presses «Пропустить»."""
    assert "аккумулятор" in bot.last_text().lower()
    if battery is None:
        await feed(dp, bot, callback_update(wiz("skip", "battery")))
    else:
        await feed(dp, bot, text_update(battery))
    assert "Комплект" in bot.last_text()
    await feed(dp, bot, callback_update(wiz("kit", "box")))
    await feed(dp, bot, callback_update(wiz("kit_done")))
    await feed(dp, bot, callback_update(wiz("grade", "B")))
    await feed(dp, bot, text_update("50000"))
    await feed(dp, bot, callback_update(wiz("note_skip")))
    assert bot.last_text().startswith("Предпросмотр выше.")


async def _get_single_item(factory) -> Item | None:
    async with factory() as s:
        return await s.scalar(select(Item))


async def _run_to_preview(dp, bot, factory, photos: int = 2) -> Item:
    """Drives a full happy path (iPhone). Returns the draft row."""
    await feed(dp, bot, text_update("/new"))
    assert "фото" in bot.last_text().lower() and "видео" in bot.last_text().lower()
    for i in range(photos):
        await feed(dp, bot, photo_update(f"u{i}"))
    await feed(dp, bot, callback_update(wiz("media_done")))
    assert bot.last_text() == "Производитель:"
    markup = bot.last_send().reply_markup
    assert button_texts(markup) == BRAND_BUTTONS
    assert [len(r) for r in rows(markup)] == [2, 2, 2, 2, 2, 1]

    await feed(dp, bot, callback_update(brand_cb("Apple")))
    assert bot.last_text() == "Устройство:"
    markup = bot.last_send().reply_markup
    assert rows(markup) == [["iPhone", "iPad", "Mac"], ["Watch", "AirPods", "Другое"], ["← Назад"]]

    await feed(dp, bot, callback_update(apple_device_cb("iphone")))
    assert bot.last_text() == "Серия:"
    markup = bot.last_send().reply_markup
    assert button_texts(markup) == IPHONE_SERIES + ["← Назад"]
    assert [len(r) for r in rows(markup)] == [2, 2, 2, 2, 1, 1]

    await feed(dp, bot, callback_update(series_cb("iphone", "15")))
    assert bot.last_text() == "Модель:"
    markup = bot.last_send().reply_markup
    assert rows(markup) == [["iPhone 15 Pro Max", "iPhone 15 Pro"], ["iPhone 15 Plus", "iPhone 15"], ["← Назад"]]
    await feed(dp, bot, callback_update(model_cb("iPhone 15 Pro")))
    assert bot.last_text() == "Объём памяти:"
    assert "256 ГБ" in button_texts(bot.last_send().reply_markup)
    assert "Пропустить" not in button_texts(bot.last_send().reply_markup)

    await feed(dp, bot, callback_update(wiz("storage", "256")))
    assert bot.last_text() == "Цвет:"
    await feed(dp, bot, callback_update(color_cb("iPhone 15 Pro", "Black Titanium")))
    assert "аккумулятор" in bot.last_text().lower()
    assert bot.last_send().reply_markup is None  # no skip for iPhone

    await feed(dp, bot, text_update("92"))
    assert "Комплект" in bot.last_text()
    await feed(dp, bot, callback_update(wiz("kit", "box")))
    await feed(dp, bot, callback_update(wiz("kit", "cable")))
    await feed(dp, bot, callback_update(wiz("kit_done")))
    assert bot.last_text().startswith("Состояние:")

    await feed(dp, bot, callback_update(wiz("grade", "A")))
    assert "Цена" in bot.last_text()
    await feed(dp, bot, text_update("75 000"))
    assert "Заметка" in bot.last_text()
    await feed(dp, bot, callback_update(wiz("note_skip")))
    assert bot.last_text().startswith("Предпросмотр выше.") and bot.last_text().endswith("Что делаем?")
    item = await _get_single_item(factory)
    assert item is not None
    return item


async def test_stranger_cannot_start_wizard(env):
    settings, factory, bot, dp = env
    await feed(dp, bot, text_update("/new", user_id=STRANGER_ID))
    assert bot.sent_texts() == []
    assert await _get_single_item(factory) is None

    bot.reset()
    await feed(dp, bot, callback_update(wiz("media_done"), user_id=STRANGER_ID))
    assert bot.sent_texts() == []
    assert bot.callback_answers()[-1].show_alert is True


async def test_start_for_admin(env):
    _, _, bot, dp = env
    await feed(dp, bot, text_update("/start"))
    assert bot.last_text() == "Здравствуйте."


async def test_full_wizard_reaches_preview_and_persists_every_step(env):
    settings, factory, bot, dp = env
    item = await _run_to_preview(dp, bot, factory, photos=2)

    assert item.status == "draft"
    assert len(item.public_id) == 8
    assert item.model == "iPhone 15 Pro" and item.generation == "15"
    assert (item.category, item.device_type, item.brand) == ("iphone", "phone", "Apple")
    assert item.storage_gb == 256
    assert item.color == "Black Titanium"
    assert item.battery_health == 92
    assert json.loads(item.kit_json) == ["box", "cable"]
    assert item.grade == "A"
    assert item.price == 75_000
    assert item.note == ""
    async with factory() as s:
        photos = (await s.scalars(select(Photo).where(Photo.item_id == item.id).order_by(Photo.position))).all()
    assert [p.tg_file_id for p in photos] == ["big_u0", "big_u1"]

    # preview mirrors the channel post: cover photo + caption + app button, no album
    assert bot.media_groups() == []
    photos_sent = bot.photos()
    assert len(photos_sent) == 1
    preview = photos_sent[0]
    assert preview.photo == "big_u0"
    assert "iPhone 15 Pro 256 ГБ, Black Titanium" in preview.caption
    assert "75 000 ₽" in preview.caption
    assert [[b.text for b in row] for row in preview.reply_markup.inline_keyboard] == [
        ["Написать продавцу"],
        ["Открыть в приложении"],
    ]
    assert "только первое фото" in bot.last_text()

    datas = button_datas(bot.last_send().reply_markup)
    assert datas == [draft("publish"), draft("edit_menu"), draft("delete")]


async def test_media_step_validation_and_cap(env):
    _, factory, bot, dp = env
    await feed(dp, bot, text_update("/new"))
    bot.reset()
    await feed(dp, bot, callback_update(wiz("media_done")))
    assert bot.callback_answers()[-1].text == "Нужно хотя бы одно фото."
    assert bot.callback_answers()[-1].show_alert is True
    assert bot.sent_texts() == []

    await feed(dp, bot, text_update("hello"))
    assert "Нужны фото или видео" in bot.last_text()
    await feed(dp, bot, document_update("img", mime_type="image/jpeg"))
    assert bot.last_text() == "Фото нужно отправить как фото, не файлом."

    for i in range(10):
        await feed(dp, bot, photo_update(f"p{i}"))
    assert bot.last_text() == "Файлов: 10/10, это максимум."
    await feed(dp, bot, photo_update("p10"))  # 11th
    assert bot.last_text() == "Уже 10 файлов, это максимум."
    await feed(dp, bot, video_update("v11"))
    assert bot.last_text() == "Уже 10 файлов, это максимум."
    await feed(dp, bot, photo_update("p3"))  # duplicate
    assert bot.last_text().startswith("Этот файл уже добавлен.")
    item = await _get_single_item(factory)
    async with factory() as s:
        rows = (await s.scalars(select(Photo).where(Photo.item_id == item.id).order_by(Photo.position))).all()
    assert len(rows) == 10 and [r.position for r in rows] == list(range(10))

    await feed(dp, bot, callback_update(wiz("media_done")))
    assert bot.last_text() == "Производитель:"
    # media outside the media step is not silently dropped
    await feed(dp, bot, photo_update("late"))
    assert "Сейчас не шаг фото" in bot.last_text()


async def test_media_videos_accepted_and_limits_enforced(env):
    _, factory, bot, dp = env
    await feed(dp, bot, text_update("/new"))

    await feed(dp, bot, video_update("big", file_size=25 * MB))
    assert bot.last_text() == "Видео 25 МБ, максимум 20 МБ."
    await feed(dp, bot, video_update("long", duration=61))
    assert bot.last_text() == "Видео 61 с, максимум 60 с."
    await feed(dp, bot, document_update("bigdoc", file_size=21 * MB))
    assert "максимум 20 МБ" in bot.last_text()

    await feed(dp, bot, video_update("v1", duration=60, file_size=20 * MB))  # limits are inclusive
    assert bot.last_text() == "Файлов: 1/10. Нужно хотя бы одно фото."
    await feed(dp, bot, document_update("d1"))  # video sent as a file
    assert bot.last_text().startswith("Файлов: 2/10.")

    # «Готово» with videos only is refused
    bot.reset()
    await feed(dp, bot, callback_update(wiz("media_done")))
    assert bot.callback_answers()[-1].text == "Нужно хотя бы одно фото."
    assert bot.sent_texts() == []

    await feed(dp, bot, photo_update("p1"))
    assert bot.last_text() == "Файлов: 3/10."
    await feed(dp, bot, callback_update(wiz("media_done")))
    assert bot.last_text() == "Производитель:"

    item = await _get_single_item(factory)
    async with factory() as s:
        rows = (await s.scalars(select(Photo).where(Photo.item_id == item.id).order_by(Photo.position))).all()
    assert [(r.kind, r.tg_file_id, r.duration, r.file_size) for r in rows] == [
        ("video", "vid_v1", 60, 20 * MB),
        ("video", "doc_d1", None, 5 * MB),
        ("photo", "big_p1", None, None),
    ]


async def test_last_media_slot_is_reserved_for_a_photo(env):
    _, factory, bot, dp = env
    await feed(dp, bot, text_update("/new"))
    for i in range(9):
        await feed(dp, bot, video_update(f"v{i}"))
    await feed(dp, bot, video_update("v9"))
    assert bot.last_text().startswith("Последнее место — для фото.")
    await feed(dp, bot, photo_update("p"))
    assert bot.last_text() == "Файлов: 10/10, это максимум."


async def test_album_gets_one_progress_reply(env, monkeypatch):
    from app.bot import wizard

    monkeypatch.setattr(wizard, "ALBUM_REPLY_DELAY", 0.5)
    _, factory, bot, dp = env
    await feed(dp, bot, text_update("/new"))
    bot.reset()
    for i in range(4):
        await feed(dp, bot, photo_update(f"a{i}", media_group_id="alb1"))
    await feed(dp, bot, video_update("av", media_group_id="alb1"))
    assert bot.sent_texts() == []
    await asyncio.sleep(0.8)
    assert bot.sent_texts() == ["Файлов: 5/10."]


async def test_concurrent_albums_never_overflow_the_cap(env, monkeypatch):
    from app.bot import wizard

    monkeypatch.setattr(wizard, "ALBUM_REPLY_DELAY", 0.05)
    _, factory, bot, dp = env
    await feed(dp, bot, text_update("/new"))
    bot.reset()
    updates = [photo_update(f"x{i}", media_group_id="g1") for i in range(6)]
    updates += [photo_update(f"y{i}", media_group_id="g2") for i in range(6)]
    await asyncio.gather(*(feed(dp, bot, u) for u in updates))
    await asyncio.sleep(0.3)
    item = await _get_single_item(factory)
    async with factory() as s:
        rows = (await s.scalars(select(Photo).where(Photo.item_id == item.id))).all()
    assert len(rows) == 10
    assert sorted(r.position for r in rows) == list(range(10))
    texts = bot.sent_texts()
    assert texts.count("Уже 10 файлов, это максимум.") == 2
    assert "Файлов: 10/10, это максимум." in texts


async def test_ipad_via_family_series(env):
    _, factory, bot, dp = env
    await _media(dp, bot)
    await feed(dp, bot, callback_update(brand_cb("Apple")))
    await feed(dp, bot, callback_update(apple_device_cb("ipad")))
    assert bot.last_text() == "Серия:"
    assert rows(bot.last_send().reply_markup) == [["iPad", "iPad Air"], ["iPad mini", "iPad Pro"], ["← Назад"]]
    await feed(dp, bot, callback_update(series_cb("ipad", "air")))
    assert bot.last_text() == "Модель:"
    markup = bot.last_send().reply_markup
    assert button_texts(markup) == [m.name for m in catalog.models_in_series("ipad", "air")] + ["← Назад"]
    assert all(name.startswith("iPad Air") for name in button_texts(markup)[:-1])
    await feed(dp, bot, callback_update(model_cb("iPad Air 11 (M2)")))
    assert bot.last_text() == "Объём памяти:"
    await feed(dp, bot, callback_update(wiz("storage", "256")))
    await feed(dp, bot, callback_update(color_cb("iPad Air 11 (M2)", "Blue")))
    await _tail_to_preview(dp, bot)
    item = await _get_single_item(factory)
    assert (item.category, item.device_type, item.brand, item.model, item.storage_gb, item.color) == (
        "ipad", "tablet", "Apple", "iPad Air 11 (M2)", 256, "Blue"
    )


async def test_macbook_flow_storage_and_ram(env):
    _, factory, bot, dp = env
    await _media(dp, bot)
    await feed(dp, bot, callback_update(brand_cb("Apple")))
    await feed(dp, bot, callback_update(apple_device_cb("macbook")))
    assert bot.last_text() == "Серия:"
    assert rows(bot.last_send().reply_markup) == [["MacBook Air", "MacBook Pro"], ["MacBook Neo"], ["← Назад"]]
    await feed(dp, bot, callback_update(series_cb("macbook", "air")))
    assert bot.last_text() == "Модель:"
    markup = bot.last_send().reply_markup
    names = button_texts(markup)
    assert names == [m.name for m in catalog.models_in_series("macbook", "air")] + ["← Назад"]
    assert all(len(r) == 1 for r in rows(markup))  # long names: one per row
    await feed(dp, bot, callback_update(model_cb("MacBook Air 13 (M3)")))
    assert bot.last_text() == "Объём памяти:"
    assert button_texts(bot.last_send().reply_markup) == ["256 ГБ", "512 ГБ", "1 ТБ", "2 ТБ"]
    await feed(dp, bot, callback_update(wiz("storage", "512")))
    assert bot.last_text() == "Оперативная память:"
    assert button_texts(bot.last_send().reply_markup) == ["8 ГБ", "16 ГБ", "24 ГБ"]
    await feed(dp, bot, callback_update(variant_cb("MacBook Air 13 (M3)", "16 ГБ")))
    assert bot.last_text() == "Цвет:"
    await feed(dp, bot, callback_update(color_cb("MacBook Air 13 (M3)", "Midnight")))
    await _tail_to_preview(dp, bot)

    item = await _get_single_item(factory)
    assert (item.category, item.device_type, item.brand) == ("macbook", "laptop", "Apple")
    assert (item.model, item.generation, item.storage_gb, item.variant, item.color) == (
        "MacBook Air 13 (M3)", "2024", 512, "16 ГБ", "Midnight"
    )
    assert "MacBook Air 13 (M3) 512 ГБ, 16 ГБ RAM, Midnight" in bot.photos()[-1].caption


async def test_one_model_series_selects_the_model(env):
    _, factory, bot, dp = env
    await _media(dp, bot)
    await feed(dp, bot, callback_update(brand_cb("Apple")))
    await feed(dp, bot, callback_update(apple_device_cb("iphone")))
    await feed(dp, bot, callback_update(series_cb("iphone", "se")))
    assert bot.callback_answers()[-1].text == "iPhone SE (2022)"
    assert bot.last_text() == "Объём памяти:"
    item = await _get_single_item(factory)
    assert (item.model, item.generation, item.category) == ("iPhone SE (2022)", "SE3", "iphone")


async def test_watch_flow_case_size_without_storage(env):
    _, factory, bot, dp = env
    await _media(dp, bot)
    await feed(dp, bot, callback_update(brand_cb("Apple")))
    await feed(dp, bot, callback_update(apple_device_cb("watch")))
    assert bot.last_text() == "Серия:"
    assert rows(bot.last_send().reply_markup) == [["Series", "Ultra"], ["SE"], ["← Назад"]]
    await feed(dp, bot, callback_update(series_cb("watch", "series")))
    assert bot.last_text() == "Модель:"
    await feed(dp, bot, callback_update(model_cb("Apple Watch Series 10")))
    assert bot.last_text() == "Размер корпуса:"
    assert button_texts(bot.last_send().reply_markup) == ["42 мм", "46 мм"]
    await feed(dp, bot, callback_update(variant_cb("Apple Watch Series 10", "46 мм")))
    assert bot.last_text() == "Цвет:"
    await feed(dp, bot, callback_update(color_cb("Apple Watch Series 10", "Jet Black")))
    assert bot.last_send().reply_markup is None  # battery not skippable for Watch
    await _tail_to_preview(dp, bot, battery="88")

    item = await _get_single_item(factory)
    assert (item.category, item.device_type, item.storage_gb, item.variant, item.color) == (
        "watch", "watch", None, "46 мм", "Jet Black"
    )
    assert "Apple Watch Series 10, 46 мм, Jet Black" in bot.photos()[-1].caption


async def test_watch_single_size_and_color_are_implied(env):
    _, factory, bot, dp = env
    await _media(dp, bot)
    await _apple_model(dp, bot, "Apple Watch Ultra")
    assert "аккумулятор" in bot.last_text().lower()
    item = await _get_single_item(factory)
    assert (item.variant, item.color) == ("49 мм", "Natural Titanium")


async def test_airpods_flow_skips_series_single_color_and_battery_skip(env):
    _, factory, bot, dp = env
    await _media(dp, bot)
    await feed(dp, bot, callback_update(brand_cb("Apple")))
    await feed(dp, bot, callback_update(apple_device_cb("airpods")))
    assert bot.last_text() == "Модель:"  # no «Серия» level
    markup = bot.last_send().reply_markup
    assert button_texts(markup) == [m.name for m in catalog.models_for("airpods")] + ["← Назад"]
    await feed(dp, bot, callback_update(model_cb("AirPods Pro 2 (USB-C)")))
    # no storage, no variant, the only color is set automatically
    assert bot.last_text() == "Аккумулятор, %:" and "Пропустить" in button_texts(bot.last_send().reply_markup)
    assert button_datas(bot.last_send().reply_markup) == [wiz("skip", "battery")]
    await _tail_to_preview(dp, bot, battery=None)

    item = await _get_single_item(factory)
    assert (item.category, item.device_type, item.brand) == ("airpods", "headphones", "Apple")
    assert (item.storage_gb, item.variant, item.color, item.battery_health) == (None, None, "White", None)
    assert json.loads(item.skipped_json) == ["battery"]
    caption = bot.photos()[-1].caption
    assert "AirPods Pro 2 (USB-C), White" in caption and "АКБ" not in caption

    # the edit menu has no color step for a single-color model
    await feed(dp, bot, callback_update(draft("edit_menu")))
    assert button_texts(bot.last_send().reply_markup) == [
        "Фото и видео", "Производитель", "Модель", "АКБ", "Комплект", "Состояние", "Цена", "Заметка", "← Назад"
    ]
    # un-skip: setting battery later clears the skip mark
    await feed(dp, bot, callback_update(wiz("edit", "battery")))
    await feed(dp, bot, text_update("97"))
    item = await _get_single_item(factory)
    assert item.battery_health == 97 and item.skipped_json is None


async def test_samsung_phone_flow_with_validation_and_skips(env):
    _, factory, bot, dp = env
    await _media(dp, bot)
    await feed(dp, bot, callback_update(brand_cb("Samsung")))
    assert bot.last_text() == "Устройство:"
    markup = bot.last_send().reply_markup
    assert rows(markup) == [["Телефон", "Планшет", "Ноутбук"], ["Часы", "Наушники", "Другое"], ["← Назад"]]
    await feed(dp, bot, text_update("телефон"))
    assert bot.last_text() == "Выберите вариант кнопкой выше."
    await feed(dp, bot, callback_update(type_device_cb("phone")))
    # no series, no device type step, no manufacturer text: straight to the typed model
    assert bot.last_text() == "Модель:"
    assert button_texts(bot.last_send().reply_markup) == ["← Назад"]

    await feed(dp, bot, text_update("x" * 65))
    assert bot.last_text() == "Слишком длинно: максимум 64 символа."
    await feed(dp, bot, text_update("  Galaxy   S23 "))
    assert bot.last_text() == "Год выпуска:"

    this_year = date.today().year
    for bad in ["23", "1999", str(this_year + 1), "двадцать", "20231"]:
        await feed(dp, bot, text_update(bad))
        assert bot.last_text() == f"Год: 2000–{this_year}.", bad
    await feed(dp, bot, text_update("2023"))
    assert bot.last_text() == "Объём памяти:"
    assert button_texts(bot.last_send().reply_markup) == [
        "32 ГБ", "64 ГБ", "128 ГБ", "256 ГБ", "512 ГБ", "1 ТБ", "2 ТБ", "Пропустить"
    ]
    await feed(dp, bot, callback_update(wiz("skip", "storage")))
    assert bot.last_text() == "Цвет:"
    await feed(dp, bot, callback_update(wiz("skip", "color")))
    assert "Пропустить" in button_texts(bot.last_send().reply_markup)  # battery is skippable for manual entry
    await _tail_to_preview(dp, bot, battery="81")

    item = await _get_single_item(factory)
    assert (item.category, item.device_type, item.brand, item.model, item.year) == (
        "other", "phone", "Samsung", "Galaxy S23", 2023
    )
    assert (item.generation, item.storage_gb, item.variant, item.color) == (None, None, None, None)
    assert json.loads(item.skipped_json) == ["storage", "color"]
    caption = bot.photos()[-1].caption
    assert "Samsung Galaxy S23 (2023)" in caption and "Устройство: Телефон" in caption

    # the edit menu follows the branch; editing storage un-skips it
    await feed(dp, bot, callback_update(draft("edit_menu")))
    assert button_texts(bot.last_send().reply_markup) == [
        "Фото и видео", "Производитель", "Модель", "Год", "Память", "Цвет",
        "АКБ", "Комплект", "Состояние", "Цена", "Заметка", "← Назад",
    ]
    await feed(dp, bot, callback_update(wiz("edit", "storage")))
    assert bot.last_text() == "Объём памяти:"
    await feed(dp, bot, callback_update(wiz("storage", "1024")))
    assert bot.last_text().startswith("Предпросмотр выше.")
    item = await _get_single_item(factory)
    assert item.storage_gb == 1024 and json.loads(item.skipped_json) == ["color"]

    # «Модель» for another brand re-asks only the typed model
    await feed(dp, bot, callback_update(draft("edit_menu")))
    await feed(dp, bot, callback_update(wiz("edit", "model")))
    assert bot.last_text() == "Модель:"
    await feed(dp, bot, text_update("Galaxy S23 FE"))
    assert bot.last_text().startswith("Предпросмотр выше.")
    item = await _get_single_item(factory)
    assert (item.brand, item.device_type, item.model, item.year, item.storage_gb) == (
        "Samsung", "phone", "Galaxy S23 FE", 2023, 1024
    )


async def test_other_brand_typed_and_normalized(env):
    _, factory, bot, dp = env
    await _media(dp, bot)
    await feed(dp, bot, callback_update(brand_cb("other")))
    assert bot.last_text() == "Производитель:"
    assert button_datas(bot.last_send().reply_markup) == [up("brand")]
    await feed(dp, bot, text_update("x" * 65))
    assert bot.last_text() == "Слишком длинно: максимум 64 символа."
    await feed(dp, bot, text_update("samsung electronics"))
    assert bot.last_text() == "Устройство:"
    assert button_texts(bot.last_send().reply_markup) == TYPE_DEVICE_BUTTONS
    await feed(dp, bot, callback_update(type_device_cb("phone")))
    # typing an Apple model name for another brand must not flip the lot into the catalog
    await feed(dp, bot, text_update("Galaxy S23"))
    await feed(dp, bot, text_update("2023"))
    await feed(dp, bot, callback_update(wiz("storage", "256")))
    await feed(dp, bot, text_update("Phantom Black"))
    await _tail_to_preview(dp, bot)
    item = await _get_single_item(factory)
    assert (item.brand, item.category, item.model, item.storage_gb, item.color, item.skipped_json) == (
        "Samsung", "other", "Galaxy S23", 256, "Phantom Black", None
    )


async def test_other_brand_typed_apple_goes_to_the_apple_menu(env):
    _, factory, bot, dp = env
    await _media(dp, bot)
    await feed(dp, bot, callback_update(brand_cb("other")))
    await feed(dp, bot, text_update("эппл"))
    assert bot.last_text() == "Устройство:"
    assert button_texts(bot.last_send().reply_markup) == APPLE_DEVICE_BUTTONS
    item = await _get_single_item(factory)
    assert (item.brand, item.category) == ("Apple", None)


async def test_brand_typed_instead_of_button(env):
    _, factory, bot, dp = env
    await _media(dp, bot)
    await feed(dp, bot, text_update("  xiaomi "))
    assert bot.last_text() == "Устройство:"
    item = await _get_single_item(factory)
    assert item.brand == "Xiaomi"


async def test_apple_other_manual_branch(env):
    _, factory, bot, dp = env
    await _media(dp, bot)
    await feed(dp, bot, callback_update(brand_cb("Apple")))
    await feed(dp, bot, callback_update(apple_device_cb("other")))
    assert bot.last_text() == "Тип устройства:"
    assert button_texts(bot.last_send().reply_markup) == TYPE_DEVICE_BUTTONS
    item = await _get_single_item(factory)
    assert (item.brand, item.category, item.device_type) == ("Apple", "other", None)

    await feed(dp, bot, callback_update(wiz("device_type", "other")))
    assert bot.last_text() == "Модель:"
    await feed(dp, bot, text_update("iMac 24 (M3)"))
    assert bot.last_text() == "Год выпуска:"
    await feed(dp, bot, text_update("2023"))
    assert bot.last_text() == "Объём памяти:"
    assert "Пропустить" in button_texts(bot.last_send().reply_markup)
    await feed(dp, bot, callback_update(wiz("storage", "512")))
    await feed(dp, bot, callback_update(wiz("skip", "color")))
    await _tail_to_preview(dp, bot, battery=None)

    item = await _get_single_item(factory)
    assert (item.category, item.brand, item.device_type, item.model, item.generation, item.year) == (
        "other", "Apple", "other", "iMac 24 (M3)", None, 2023
    )
    assert (item.storage_gb, item.color) == (512, None)
    assert "Apple iMac 24 (M3) (2023) 512 ГБ" in bot.photos()[-1].caption

    await feed(dp, bot, callback_update(draft("edit_menu")))
    assert button_texts(bot.last_send().reply_markup)[:6] == [
        "Фото и видео", "Производитель", "Модель", "Год", "Память", "Цвет"
    ]
    # «Модель» for Apple restarts from the device
    await feed(dp, bot, callback_update(wiz("edit", "model")))
    assert bot.last_text() == "Устройство:"
    item = await _get_single_item(factory)
    assert (item.brand, item.category, item.device_type, item.model, item.year, item.storage_gb) == (
        "Apple", None, None, None, None, None
    )


async def test_apple_other_catalog_name_stays_manual(env):
    _, factory, bot, dp = env
    await _media(dp, bot)
    await feed(dp, bot, callback_update(brand_cb("Apple")))
    await feed(dp, bot, callback_update(apple_device_cb("other")))
    await feed(dp, bot, callback_update(wiz("device_type", "phone")))
    await feed(dp, bot, text_update("iPhone 15 Pro"))
    assert bot.last_text() == "Год выпуска:"
    item = await _get_single_item(factory)
    assert (item.category, item.brand, item.model, item.generation) == ("other", "Apple", "iPhone 15 Pro", None)


async def test_back_button_at_every_level(env):
    _, factory, bot, dp = env
    await _media(dp, bot)

    # «Другой» → text → «← Назад» → brand buttons
    await feed(dp, bot, callback_update(brand_cb("other")))
    await feed(dp, bot, callback_update(up("brand")))
    assert bot.last_text() == "Производитель:"
    assert button_texts(bot.last_send().reply_markup) == BRAND_BUTTONS

    # device ← brand
    await feed(dp, bot, callback_update(brand_cb("Apple")))
    await feed(dp, bot, callback_update(up("device")))
    assert bot.last_text() == "Производитель:"
    assert (await _get_single_item(factory)).brand is None

    # series ← device
    await feed(dp, bot, callback_update(brand_cb("Apple")))
    await feed(dp, bot, callback_update(apple_device_cb("iphone")))
    await feed(dp, bot, callback_update(up("series")))
    assert bot.last_text() == "Устройство:"
    assert button_texts(bot.last_send().reply_markup) == APPLE_DEVICE_BUTTONS
    item = await _get_single_item(factory)
    assert (item.brand, item.category, item.device_type) == ("Apple", None, None)

    # model ← series (the category stays)
    await feed(dp, bot, callback_update(apple_device_cb("iphone")))
    await feed(dp, bot, callback_update(series_cb("iphone", "15")))
    await feed(dp, bot, callback_update(up("model")))
    assert bot.last_text() == "Серия:"
    assert (await _get_single_item(factory)).category == "iphone"
    # and a different series from there
    await feed(dp, bot, callback_update(series_cb("iphone", "14")))
    assert button_texts(bot.last_send().reply_markup)[0] == "iPhone 14 Pro Max"

    # model ← device where there is no series (AirPods)
    await feed(dp, bot, callback_update(up("model")))
    await feed(dp, bot, callback_update(up("series")))
    await feed(dp, bot, callback_update(apple_device_cb("airpods")))
    assert bot.last_text() == "Модель:"
    await feed(dp, bot, callback_update(up("model")))
    assert bot.last_text() == "Устройство:"
    assert (await _get_single_item(factory)).category is None

    # Apple «Другое»: device type ← device, typed model ← device type
    await feed(dp, bot, callback_update(apple_device_cb("other")))
    await feed(dp, bot, callback_update(up("device_type")))
    assert bot.last_text() == "Устройство:"
    await feed(dp, bot, callback_update(apple_device_cb("other")))
    await feed(dp, bot, callback_update(wiz("device_type", "laptop")))
    await feed(dp, bot, callback_update(up("model")))
    assert bot.last_text() == "Тип устройства:"
    item = await _get_single_item(factory)
    assert (item.category, item.device_type) == ("other", None)

    # another brand: typed model ← device ← brand
    await feed(dp, bot, callback_update(up("device_type")))
    await feed(dp, bot, callback_update(up("device")))
    await feed(dp, bot, callback_update(brand_cb("Samsung")))
    await feed(dp, bot, callback_update(type_device_cb("tablet")))
    await feed(dp, bot, callback_update(up("model")))
    assert bot.last_text() == "Устройство:"
    assert button_texts(bot.last_send().reply_markup) == TYPE_DEVICE_BUTTONS
    item = await _get_single_item(factory)
    assert (item.brand, item.category, item.device_type) == ("Samsung", None, None)
    await feed(dp, bot, callback_update(up("device")))
    assert bot.last_text() == "Производитель:"
    item = await _get_single_item(factory)
    assert (item.brand, item.category) == (None, None)


async def test_stale_back_and_matryoshka_buttons_are_rejected(env):
    _, factory, bot, dp = env
    await _media(dp, bot)
    await _apple_model(dp, bot, "iPhone 15 Pro")
    assert bot.last_text() == "Объём памяти:"
    before = await _get_single_item(factory)

    for data in (
        up("device"), up("series"), up("model"),  # «Назад» from keyboards left above
        brand_cb("Samsung"), apple_device_cb("ipad"), series_cb("iphone", "14"),
    ):
        bot.reset()
        await feed(dp, bot, callback_update(data))
        assert bot.callback_answers()[-1].text == "Кнопка устарела, выберите заново", data
        assert bot.callback_answers()[-1].show_alert is True
        assert bot.sent_texts() == []
    after = await _get_single_item(factory)
    assert (after.brand, after.category, after.model) == (before.brand, before.category, before.model)

    # a device button of the other kind (device type while Apple is picked)
    await feed(dp, bot, text_update("/new"))
    await feed(dp, bot, callback_update(draft("restart")))
    await feed(dp, bot, photo_update("p"))
    await feed(dp, bot, callback_update(wiz("media_done")))
    await feed(dp, bot, callback_update(brand_cb("Apple")))
    await feed(dp, bot, callback_update(type_device_cb("phone")))
    assert bot.callback_answers()[-1].text == "Кнопка устарела, выберите заново"
    await feed(dp, bot, callback_update(brand_cb("Motorola")))  # not a brand button
    assert bot.callback_answers()[-1].text == "Кнопка устарела, выберите заново"
    assert (await _get_single_item(factory)).category is None

    # pre-matryoshka «Что продаём?» buttons: alert and the current step again
    bot.reset()
    await feed(dp, bot, callback_update(wiz("category", "iphone")))
    assert bot.callback_answers()[-1].text == "Кнопка устарела, выберите заново"
    assert bot.last_text() == "Устройство:"
    assert (await _get_single_item(factory)).category is None


async def test_battery_and_price_validation(env):
    _, factory, bot, dp = env
    await _media(dp, bot)
    await _apple_model(dp, bot, "iPhone 16")
    await feed(dp, bot, text_update("typed instead of button"))
    assert bot.last_text() == "Выберите вариант кнопкой выше."
    await feed(dp, bot, callback_update(wiz("storage", "4096")))
    assert bot.callback_answers()[-1].text == "Нет такого объёма для этой модели"
    await feed(dp, bot, callback_update(wiz("storage", "128")))
    await feed(dp, bot, callback_update(wiz("color", "0")))
    # battery can't be skipped for an iPhone
    await feed(dp, bot, callback_update(wiz("skip", "battery")))
    assert bot.callback_answers()[-1].text == "Этот шаг нельзя пропустить"

    for bad in ["abc", "101", "-5"]:
        await feed(dp, bot, text_update(bad))
        assert "от 0 до 100" in bot.last_text()
    await feed(dp, bot, text_update("100%"))
    assert "Комплект" in bot.last_text()
    await feed(dp, bot, callback_update(wiz("kit_done")))
    await feed(dp, bot, callback_update(wiz("grade", "C")))
    for bad in ["дорого", "0", "-1"]:
        await feed(dp, bot, text_update(bad))
        assert "больше нуля" in bot.last_text()
    await feed(dp, bot, text_update("35000₽"))
    assert "Заметка" in bot.last_text()

    item = await _get_single_item(factory)
    assert item.battery_health == 100 and item.price == 35_000 and item.kit_json == "[]"


async def test_stale_or_foreign_model_buttons_are_rejected(env):
    _, factory, bot, dp = env
    await _media(dp, bot)
    await feed(dp, bot, callback_update(brand_cb("Apple")))
    await feed(dp, bot, callback_update(apple_device_cb("ipad")))
    await feed(dp, bot, callback_update(series_cb("ipad", "pro")))
    await feed(dp, bot, callback_update(wiz("model", "3")))  # pre-v2 index-only button
    assert bot.callback_answers()[-1].text == "Кнопка устарела, выберите модель заново"
    await feed(dp, bot, callback_update(model_cb("iPhone 15")))  # iPhone button while in iPad
    assert bot.callback_answers()[-1].text == "Кнопка от другой категории, выберите заново"
    item = await _get_single_item(factory)
    assert item.model is None and item.category == "ipad"


async def test_restart_resumes_draft_from_first_empty_step(env):
    settings, factory, bot, dp = env
    await _media(dp, bot)
    await _apple_model(dp, bot, "iPhone 14")
    await feed(dp, bot, callback_update(wiz("storage", "128")))

    # "server restart": fresh dispatcher => fresh in-memory FSM storage
    dp2 = create_dispatcher(settings, factory)
    bot2 = FakeBot()
    await feed(dp2, bot2, text_update("/new"))
    assert bot2.last_text().startswith("Есть незавершённый черновик")
    assert "iPhone 14" in bot2.last_text() and "128 ГБ" in bot2.last_text()
    await feed(dp2, bot2, callback_update(draft("continue")))
    assert bot2.last_text() == "Цвет:"  # media, brand, device, model, storage already filled

    await feed(dp2, bot2, callback_update(wiz("color", "0")))
    assert "аккумулятор" in bot2.last_text().lower()
    assert_callback_sizes(bot2)

    async with factory() as s:
        items = (await s.scalars(select(Item))).all()
    assert len(items) == 1  # no duplicate draft


async def _restart_and_continue(settings, factory):
    dp2 = create_dispatcher(settings, factory)
    bot2 = FakeBot()
    await feed(dp2, bot2, text_update("/new"))
    summary = bot2.last_text()
    await feed(dp2, bot2, callback_update(draft("continue")))
    assert_callback_sizes(bot2)
    return dp2, bot2, summary


async def test_restart_resumes_at_every_matryoshka_level(env):
    settings, factory, bot, dp = env
    await _media(dp, bot)

    # manufacturer picked, device not yet
    await feed(dp, bot, callback_update(brand_cb("Apple")))
    dp, bot, summary = await _restart_and_continue(settings, factory)
    assert summary == "Есть незавершённый черновик (фото: 1, Apple)."
    assert bot.last_text() == "Устройство:"
    assert button_texts(bot.last_send().reply_markup) == APPLE_DEVICE_BUTTONS

    # device picked, series not yet
    await feed(dp, bot, callback_update(apple_device_cb("iphone")))
    dp, bot, summary = await _restart_and_continue(settings, factory)
    assert summary == "Есть незавершённый черновик (фото: 1, Apple, iPhone)."
    assert bot.last_text() == "Серия:"

    # series picked, model not yet: the series is navigation only, asked again
    await feed(dp, bot, callback_update(series_cb("iphone", "15")))
    assert bot.last_text() == "Модель:"
    dp, bot, _ = await _restart_and_continue(settings, factory)
    assert bot.last_text() == "Серия:"
    await feed(dp, bot, callback_update(series_cb("iphone", "15")))
    await feed(dp, bot, callback_update(model_cb("iPhone 15")))
    assert bot.last_text() == "Объём памяти:"

    # another brand: device picked, typed model not yet
    await feed(dp, bot, callback_update(draft("restart")))
    await feed(dp, bot, photo_update("p2"))
    await feed(dp, bot, callback_update(wiz("media_done")))
    await feed(dp, bot, callback_update(brand_cb("Honor")))
    dp, bot, _ = await _restart_and_continue(settings, factory)
    assert bot.last_text() == "Устройство:"
    assert button_texts(bot.last_send().reply_markup) == TYPE_DEVICE_BUTTONS
    await feed(dp, bot, callback_update(type_device_cb("watch")))
    dp, bot, summary = await _restart_and_continue(settings, factory)
    assert summary == "Есть незавершённый черновик (фото: 1, Honor, Часы)."
    assert bot.last_text() == "Модель:"
    await feed(dp, bot, text_update("Watch 5"))
    assert bot.last_text() == "Год выпуска:"


async def test_legacy_draft_without_category_resumes_cleanly(env):
    settings, factory, bot, dp = env
    # pre-v2 drafts: category NULL, one with a photo, one (lab) with nothing at all
    async with factory() as s:
        with_photo = Item(public_id="LEGACY01", status="draft", created_by=ADMIN_ID)
        empty = Item(public_id="LEGACY02", status="draft", created_by=222)
        s.add_all([with_photo, empty])
        await s.flush()
        s.add(Photo(item_id=with_photo.id, tg_file_id="old", file_unique_id="old", position=0))
        await s.commit()

    await feed(dp, bot, text_update("/new"))
    assert bot.last_text() == "Есть незавершённый черновик (фото: 1)."
    await feed(dp, bot, callback_update(draft("continue")))
    assert bot.last_text() == "Производитель:"

    await feed(dp, bot, text_update("/new", user_id=222))
    assert bot.last_text() == "Есть незавершённый черновик (пусто)."
    await feed(dp, bot, callback_update(draft("continue"), user_id=222))
    assert bot.last_text().startswith("Фото и видео:")


async def _legacy_draft(factory, admin_id: int, **fields) -> None:
    async with factory() as s:
        item = Item(public_id=f"OLD{admin_id:05d}", status="draft", created_by=admin_id, **fields)
        s.add(item)
        await s.flush()
        s.add(Photo(item_id=item.id, tg_file_id=f"f{admin_id}", file_unique_id=f"u{admin_id}", position=0))
        await s.commit()


async def test_pre_matryoshka_drafts_with_category_resume_sensibly(env):
    settings, factory, bot, dp = env
    # Apple category picked with the old single «Что продаём?» step
    await _legacy_draft(factory, ADMIN_ID, category="iphone", device_type="phone", brand="Apple")
    await feed(dp, bot, text_update("/new"))
    await feed(dp, bot, callback_update(draft("continue")))
    assert bot.last_text() == "Серия:"

    # «Другое» with the device type picked, manufacturer not yet (old order)
    await _legacy_draft(factory, 222, category="other", device_type="laptop")
    await feed(dp, bot, text_update("/new", user_id=222))
    await feed(dp, bot, callback_update(draft("continue"), user_id=222))
    assert bot.last_text() == "Производитель:"
    await feed(dp, bot, callback_update(brand_cb("Lenovo"), user_id=222))
    assert bot.last_text() == "Модель:"  # the device type is kept
    async with factory() as s:
        item = await s.scalar(select(Item).where(Item.created_by == 222))
    assert (item.brand, item.category, item.device_type) == ("Lenovo", "other", "laptop")

    # «Другое» picked, nothing else: manufacturer, then the device type
    async with factory() as s:
        await s.delete(item)
        await s.commit()
    await _legacy_draft(factory, 222, category="other")
    await feed(dp, bot, text_update("/new", user_id=222))
    await feed(dp, bot, callback_update(draft("continue"), user_id=222))
    assert bot.last_text() == "Производитель:"
    await feed(dp, bot, callback_update(brand_cb("Sony"), user_id=222))
    assert bot.last_text() == "Устройство:"
    assert button_texts(bot.last_send().reply_markup) == TYPE_DEVICE_BUTTONS


async def test_restart_button_deletes_old_draft_and_starts_new(env):
    _, factory, bot, dp = env
    await feed(dp, bot, text_update("/new"))
    await feed(dp, bot, photo_update("a"))
    first = await _get_single_item(factory)
    await feed(dp, bot, text_update("/new"))
    await feed(dp, bot, callback_update(draft("restart")))
    assert "фото" in bot.last_text().lower()
    async with factory() as s:
        items = (await s.scalars(select(Item))).all()
        photos = (await s.scalars(select(Photo))).all()
    assert len(items) == 1 and items[0].public_id != first.public_id
    assert photos == []  # cascade removed the old draft's photos


async def test_edit_field_from_preview_returns_to_preview(env):
    _, factory, bot, dp = env
    await _run_to_preview(dp, bot, factory)
    await feed(dp, bot, callback_update(draft("edit_menu")))
    assert bot.last_text() == "Что изменить?"
    assert button_texts(bot.last_send().reply_markup) == [
        "Фото и видео", "Обложка", "Производитель", "Модель", "Память", "Цвет",
        "АКБ", "Комплект", "Состояние", "Цена", "Заметка", "← Назад",
    ]

    await feed(dp, bot, callback_update(wiz("edit", "price")))
    assert "Цена" in bot.last_text()
    bot.reset()
    await feed(dp, bot, text_update("69000"))
    assert bot.last_text().startswith("Предпросмотр выше.")
    assert "69 000 ₽" in bot.photos()[-1].caption

    # «Модель» for Apple restarts from the device; storage and color are re-asked
    await feed(dp, bot, callback_update(draft("edit_menu")))
    await feed(dp, bot, callback_update(wiz("edit", "model")))
    assert bot.last_text() == "Устройство:"
    assert button_texts(bot.last_send().reply_markup) == APPLE_DEVICE_BUTTONS
    item = await _get_single_item(factory)
    assert (item.model, item.storage_gb, item.color, item.category, item.brand) == (None, None, None, None, "Apple")
    await feed(dp, bot, callback_update(apple_device_cb("iphone")))
    assert bot.last_text() == "Серия:"  # the series of the old model is not reused
    await feed(dp, bot, callback_update(series_cb("iphone", "13")))
    await feed(dp, bot, callback_update(model_cb("iPhone 13")))
    assert bot.last_text() == "Объём памяти:"
    await feed(dp, bot, callback_update(wiz("storage", "128")))
    assert bot.last_text() == "Цвет:"
    await feed(dp, bot, callback_update(color_cb("iPhone 13", "Starlight")))
    assert bot.last_text().startswith("Предпросмотр выше.") and bot.last_text().endswith("Что делаем?")
    item = await _get_single_item(factory)
    assert item.model == "iPhone 13" and item.storage_gb == 128 and item.color == "Starlight"
    assert (item.category, item.device_type, item.generation) == ("iphone", "phone", "13")
    assert item.price == 69_000 and item.grade == "A"  # untouched


async def test_edit_brand_resets_matryoshka_and_reasks(env):
    _, factory, bot, dp = env
    await _run_to_preview(dp, bot, factory)
    await feed(dp, bot, callback_update(draft("edit_menu")))
    await feed(dp, bot, callback_update(wiz("edit", "brand")))
    assert bot.last_text() == "Производитель:"
    assert button_texts(bot.last_send().reply_markup) == BRAND_BUTTONS
    item = await _get_single_item(factory)
    assert (item.category, item.model, item.generation, item.storage_gb, item.color, item.brand, item.device_type) == (
        None, None, None, None, None, None, None
    )
    assert (item.price, item.grade, item.battery_health) == (75_000, "A", 92)

    await feed(dp, bot, callback_update(brand_cb("Xiaomi")))
    await feed(dp, bot, callback_update(type_device_cb("phone")))
    await feed(dp, bot, text_update("Redmi Note 12"))
    await feed(dp, bot, text_update("2023"))
    await feed(dp, bot, callback_update(wiz("skip", "storage")))
    await feed(dp, bot, callback_update(wiz("skip", "color")))
    # battery, kit, grade, price, note were kept: straight back to preview
    assert bot.last_text().startswith("Предпросмотр выше.")
    item = await _get_single_item(factory)
    assert (item.category, item.device_type, item.brand, item.model, item.year) == (
        "other", "phone", "Xiaomi", "Redmi Note 12", 2023
    )

    # and back to Apple: the manual fields and skip marks go away.
    # A «Категория» button from a pre-matryoshka edit menu acts as «Производитель».
    await feed(dp, bot, callback_update(wiz("edit", "category")))
    assert bot.last_text() == "Производитель:"
    await _apple_model(dp, bot, "AirPods 4")
    assert bot.last_text().startswith("Предпросмотр выше.")
    item = await _get_single_item(factory)
    assert (item.brand, item.category, item.device_type, item.year, item.color, item.skipped_json) == (
        "Apple", "airpods", "headphones", None, "White", None
    )

    # device / series are not separate edit fields
    await feed(dp, bot, callback_update(wiz("edit", "device")))
    assert bot.callback_answers()[-1].text == "Нет такого поля"


def test_every_wizard_keyboard_fits_callback_limit():
    from app.bot import keyboards as kb

    markups = [
        kb.media_kb(), kb.brands_kb(), kb.back_kb("brand"), kb.back_kb("model"),
        kb.devices_kb(apple=True), kb.devices_kb(apple=False), kb.device_types_kb(),
        kb.storages_kb((32, 64, 128, 256, 512, 1024, 2048), skippable=True), kb.skip_kb("battery"),
        kb.kit_kb(["box"]), kb.grades_kb(), kb.note_kb(), kb.draft_resume_kb(), kb.preview_kb(),
        kb.edit_menu_kb([("media", "Фото и видео"), ("brand", "Производитель")], cover=True),
        kb.cover_kb(list(range(10**9, 10**9 + 10))),
    ]
    for category in catalog.CATEGORIES:
        markups.append(kb.models_kb(category.code))
        markups.append(kb.series_kb(category.code))
        for key, _ in catalog.series_for(category.code):
            markups.append(kb.models_kb(category.code, key))
    for spec in catalog.APPLE_MODELS:
        markups += [kb.variants_kb(spec.variants), kb.colors_kb(spec.colors)]
    for markup in markups:
        for row in markup.inline_keyboard:
            for button in row:
                assert len(button.callback_data.encode()) <= 64, button.callback_data


def test_model_keyboards_one_or_two_per_row():
    from app.bot import keyboards as kb

    for category in catalog.CATEGORIES:
        keys = [k for k, _ in catalog.series_for(category.code)] or [None]
        for key in keys:
            markup = kb.models_kb(category.code, key)
            *models, back = rows(markup)
            assert back == ["← Назад"]
            assert {len(r) for r in models} <= {1, 2}
            if any(len(name) > kb.MODEL_TWO_PER_ROW_MAX for r in models for name in r):
                assert all(len(r) == 1 for r in models), (category.code, key)
    for category in catalog.CATEGORIES:
        *series_rows, back = rows(kb.series_kb(category.code))
        if catalog.series_for(category.code):
            assert all(len(r) <= 2 for r in series_rows) and back == ["← Назад"]


async def _photo_order(factory) -> list[tuple[str, int]]:
    async with factory() as s:
        rows = (await s.scalars(select(Photo).order_by(Photo.position))).all()
    return [(p.file_unique_id, p.position) for p in rows]


async def test_cover_pick_reorders_and_reshows_preview(env):
    _, factory, bot, dp = env
    await _run_to_preview(dp, bot, factory, photos=3)
    await feed(dp, bot, callback_update(draft("edit_menu")))
    assert "Обложка" in button_texts(bot.last_send().reply_markup)

    bot.reset()
    await feed(dp, bot, callback_update(wiz("cover_menu")))
    album = bot.media_groups()
    assert len(album) == 1
    assert [m.media for m in album[0].media] == ["big_u0", "big_u1", "big_u2"]
    assert bot.last_text() == "Выберите обложку."
    markup = bot.last_send().reply_markup
    assert button_texts(markup) == ["1", "2", "3", "← Назад"]

    third = button_datas(markup)[2]
    bot.reset()
    await feed(dp, bot, callback_update(third))
    assert await _photo_order(factory) == [("u2", 0), ("u0", 1), ("u1", 2)]
    # preview again, with the new cover
    assert bot.photos()[-1].photo == "big_u2"
    assert bot.last_text().startswith("Предпросмотр выше.")


async def test_cover_pick_skips_videos_and_rejects_stale_buttons(env):
    _, factory, bot, dp = env
    await feed(dp, bot, text_update("/new"))
    await feed(dp, bot, photo_update("p0"))
    await feed(dp, bot, video_update("v0"))
    await feed(dp, bot, photo_update("p1"))
    item = await _get_single_item(factory)
    bot.reset()
    await feed(dp, bot, callback_update(wiz("cover_menu")))
    assert [m.media for m in bot.media_groups()[0].media] == ["big_p0", "big_p1"]
    async with factory() as session:
        video_id = await session.scalar(select(Photo.id).where(Photo.item_id == item.id, Photo.kind == "video"))
    await feed(dp, bot, callback_update(wiz("cover", str(video_id))))
    assert bot.callback_answers()[-1].text == "Кнопка устарела, выберите заново"
    assert await _photo_order(factory) == [("p0", 0), ("v0", 1), ("p1", 2)]


async def test_cover_hidden_with_one_photo(env):
    _, factory, bot, dp = env
    await _run_to_preview(dp, bot, factory, photos=1)
    await feed(dp, bot, callback_update(draft("edit_menu")))
    assert "Обложка" not in button_texts(bot.last_send().reply_markup)
    bot.reset()
    await feed(dp, bot, callback_update(wiz("cover_menu")))
    assert bot.media_groups() == []
    assert bot.callback_answers()[-1].show_alert is True


async def test_delete_draft_from_preview(env):
    _, factory, bot, dp = env
    await _run_to_preview(dp, bot, factory)
    await feed(dp, bot, callback_update(draft("delete")))
    assert "удалён" in bot.last_text()
    assert await _get_single_item(factory) is None
    await feed(dp, bot, text_update("/new"))
    assert "фото" in bot.last_text().lower()


async def test_publish_from_preview_posts_to_channel_and_clears_state(env, monkeypatch):
    from app.services import publisher

    monkeypatch.setattr(publisher, "SEND_PAUSE_SECONDS", 0)
    settings, factory, bot, dp = env
    await _run_to_preview(dp, bot, factory)
    bot.reset()
    await feed(dp, bot, callback_update(draft("publish")))

    assert bot.callback_answers()[0].text == "Публикую…"
    posts = [p for p in bot.photos() if p.chat_id == settings.channel_id]
    assert len(posts) == 1 and bot.media_groups() == []
    assert bot.last_text().startswith("Опубликовано: лот ")
    assert "https://t.me/c/1234567890/" in bot.last_text()

    item = await _get_single_item(factory)
    assert item.status == "active"
    async with factory() as s:
        from app.db.models import ChannelMessage

        rows = (await s.scalars(select(ChannelMessage).where(ChannelMessage.item_id == item.id))).all()
    assert [r.kind for r in rows] == ["media"]

    # state cleared: /new starts a fresh draft instead of offering the published one
    await feed(dp, bot, text_update("/new"))
    assert "фото" in bot.last_text().lower()


async def test_publish_other_lot_with_leading_video(env, monkeypatch):
    from app.db.models import ItemTag, Tag
    from app.services import publisher

    monkeypatch.setattr(publisher, "SEND_PAUSE_SECONDS", 0)
    settings, factory, bot, dp = env
    await feed(dp, bot, text_update("/new"))
    await feed(dp, bot, video_update("intro"))
    await feed(dp, bot, photo_update("cover"))
    await feed(dp, bot, callback_update(wiz("media_done")))
    await feed(dp, bot, callback_update(brand_cb("other")))
    await feed(dp, bot, text_update("самсунг"))
    await feed(dp, bot, callback_update(type_device_cb("phone")))
    await feed(dp, bot, text_update("Galaxy S23"))
    await feed(dp, bot, text_update("2023"))
    await feed(dp, bot, callback_update(wiz("storage", "256")))
    await feed(dp, bot, text_update("Phantom Black"))
    await _tail_to_preview(dp, bot, battery=None)
    assert bot.photos()[-1].photo == "big_cover"  # preview cover skips the video

    bot.reset()
    await feed(dp, bot, callback_update(draft("publish")))
    posts = [p for p in bot.photos() if p.chat_id == settings.channel_id]
    assert len(posts) == 1 and posts[0].photo == "big_cover"
    assert "Samsung Galaxy S23 (2023) 256 ГБ, Phantom Black" in posts[0].caption
    assert bot.last_text().startswith("Опубликовано: лот ")

    item = await _get_single_item(factory)
    assert item.status == "active"
    async with factory() as s:
        slugs = set(
            (await s.scalars(select(Tag.slug).join(ItemTag, ItemTag.tag_id == Tag.id).where(ItemTag.item_id == item.id))).all()
        )
    assert {"device:phone", "brand:samsung", "model:samsung-galaxy-s23", "storage:256"} <= slugs
    assert not any(slug.startswith("battery:") for slug in slugs)
