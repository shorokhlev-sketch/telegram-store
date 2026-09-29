from __future__ import annotations

from collections.abc import Sequence

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import Settings
from app.services import catalog, post_render
from app.services.drafts import OTHER_CATEGORY, OTHER_CATEGORY_LABEL

BACK_BUTTON = "← Назад"
OTHER_BRAND_BUTTON = "Другой"
OTHER_BRAND_VALUE = "other"
# Model keyboards go one per row when any name is longer than this.
MODEL_TWO_PER_ROW_MAX = 17


# ---------- persistent reply keyboards (always under the input field) ----------

SHOP_BUTTON = "Магазин"
NEW_LOT_BUTTON = "Новый лот"
NOTIFY_OFF_BUTTON = "Отключить уведомления"
NOTIFY_ON_BUTTON = "Включить уведомления"
# Labels shown on keyboards sent before emoji were dropped (Lev 28.09: no emoji anywhere).
# Users keep the old keyboard until the next /start, so the old texts still work.
LEGACY_NEW_LOT_BUTTONS = {"➕ Новый лот"}
LEGACY_NOTIFY_OFF_BUTTONS = {"🔕 Отключить уведомления"}
LEGACY_NOTIFY_ON_BUTTONS = {"🔔 Включить уведомления"}


def _shop_button(settings: Settings) -> KeyboardButton:
    # A Mini App opened from a reply keyboard gets no initData; it can only
    # sendData() back to the bot (handled in client.py as web_app_data).
    return KeyboardButton(text=SHOP_BUTTON, web_app=WebAppInfo(url=settings.app_url))


def admin_reply_kb(settings: Settings) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[_shop_button(settings), KeyboardButton(text=NEW_LOT_BUTTON)]],
        is_persistent=True,
        resize_keyboard=True,
    )


def customer_reply_kb(settings: Settings, subscribed: bool) -> ReplyKeyboardMarkup:
    toggle = NOTIFY_OFF_BUTTON if subscribed else NOTIFY_ON_BUTTON
    return ReplyKeyboardMarkup(
        keyboard=[[_shop_button(settings), KeyboardButton(text=toggle)]],
        is_persistent=True,
        resize_keyboard=True,
    )


class WizardCb(CallbackData, prefix="wiz"):
    # media_done | brand | device | series | model | up | storage | variant | color |
    # device_type | skip | kit | kit_done | grade | note_skip | edit | cover_menu | cover
    # (category: pre-matryoshka buttons, answered as stale)
    action: str
    value: str = ""


class DraftCb(CallbackData, prefix="draft"):
    action: str  # continue | restart | publish | edit_menu | edit | delete | back


class SoldCb(CallbackData, prefix="sold"):
    action: str  # ask | yes | no
    public_id: str = ""


def media_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="Готово", callback_data=WizardCb(action="media_done"))
    return b.as_markup()



def _back_row(b: InlineKeyboardBuilder, level: str) -> None:
    """«← Назад» one level up the matryoshka; `level` is the step on screen."""
    b.row(InlineKeyboardButton(text=BACK_BUTTON, callback_data=WizardCb(action="up", value=level).pack()))


def brands_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for brand in catalog.WIZARD_BRANDS:
        b.button(text=brand, callback_data=WizardCb(action="brand", value=brand))
    b.button(text=OTHER_BRAND_BUTTON, callback_data=WizardCb(action="brand", value=OTHER_BRAND_VALUE))
    b.adjust(2)
    return b.as_markup()


def back_kb(level: str) -> InlineKeyboardMarkup:
    """Only «← Назад»: under a typed-input prompt inside the matryoshka."""
    b = InlineKeyboardBuilder()
    _back_row(b, level)
    return b.as_markup()


def devices_kb(apple: bool) -> InlineKeyboardMarkup:
    """Apple: menu categories + «Другое» ("a.<category>"); other brands: device
    types ("t.<type>")."""
    b = InlineKeyboardBuilder()
    if apple:
        for code, label in catalog.APPLE_DEVICES:
            b.button(text=label, callback_data=WizardCb(action="device", value=f"a.{code}"))
        b.button(text=OTHER_CATEGORY_LABEL, callback_data=WizardCb(action="device", value=f"a.{OTHER_CATEGORY}"))
    else:
        for code, label in catalog.DEVICE_TYPES:
            b.button(text=label, callback_data=WizardCb(action="device", value=f"t.{code}"))
    b.adjust(3)
    _back_row(b, "device")
    return b.as_markup()


def series_kb(category: str) -> InlineKeyboardMarkup:
    """Value "<category>.<series key>", e.g. "iphone.15"."""
    b = InlineKeyboardBuilder()
    for key, label in catalog.series_for(category):
        b.button(text=label, callback_data=WizardCb(action="series", value=f"{category}.{key}"))
    b.adjust(2)
    _back_row(b, "series")
    return b.as_markup()


def models_kb(category: str, series: str | None = None) -> InlineKeyboardMarkup:
    """Models of `series` (or the whole category). Callback value is
    "<category>.<index in models_for(category)>" (":" is the CallbackData
    separator), so a list can grow without shifting another category's buttons
    and the index does not depend on the series."""
    models = catalog.models_for(category)
    shown = catalog.models_in_series(category, series) if series else models
    b = InlineKeyboardBuilder()
    for spec in shown:
        b.button(text=spec.name, callback_data=WizardCb(action="model", value=f"{category}.{models.index(spec)}"))
    b.adjust(2 if all(len(m.name) <= MODEL_TWO_PER_ROW_MAX for m in shown) else 1)
    _back_row(b, "model")
    return b.as_markup()


def _skip_button(b: InlineKeyboardBuilder, step: str) -> None:
    b.row(InlineKeyboardButton(text="Пропустить", callback_data=WizardCb(action="skip", value=step).pack()))


def storages_kb(storages: Sequence[int], skippable: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for gb in storages:
        b.button(text=catalog.format_storage(gb), callback_data=WizardCb(action="storage", value=str(gb)))
    b.adjust(3)
    if skippable:
        _skip_button(b, "storage")
    return b.as_markup()


def variants_kb(variants: Sequence[str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for idx, variant in enumerate(variants):
        b.button(text=variant, callback_data=WizardCb(action="variant", value=str(idx)))
    b.adjust(3)
    return b.as_markup()


def colors_kb(colors: Sequence[str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for idx, color in enumerate(colors):
        b.button(text=color, callback_data=WizardCb(action="color", value=str(idx)))
    b.adjust(2)
    return b.as_markup()


def device_types_kb() -> InlineKeyboardMarkup:
    """Device type of an Apple «Другое» lot (other brands pick it in `devices_kb`)."""
    b = InlineKeyboardBuilder()
    for code, label in catalog.DEVICE_TYPES:
        b.button(text=label, callback_data=WizardCb(action="device_type", value=code))
    b.adjust(3)
    _back_row(b, "device_type")
    return b.as_markup()


def skip_kb(step: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    _skip_button(b, step)
    return b.as_markup()


def kit_kb(selected: list[str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for item in catalog.KIT_ITEMS:
        label = f"✓ {item.label}" if item.code in selected else item.label
        b.button(text=label, callback_data=WizardCb(action="kit", value=item.code))
    b.button(text="Готово", callback_data=WizardCb(action="kit_done"))
    b.adjust(2, 2, 1)
    return b.as_markup()


def grades_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for g in catalog.GRADES:
        b.button(text=g.label, callback_data=WizardCb(action="grade", value=g.code))
    b.adjust(1)
    return b.as_markup()


def note_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="Пропустить", callback_data=WizardCb(action="note_skip"))
    return b.as_markup()


def draft_resume_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="Продолжить", callback_data=DraftCb(action="continue"))
    b.button(text="Удалить и начать заново", callback_data=DraftCb(action="restart"))
    b.adjust(1)
    return b.as_markup()


def preview_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="Опубликовать", callback_data=DraftCb(action="publish"))
    b.button(text="Изменить поле", callback_data=DraftCb(action="edit_menu"))
    b.button(text="Удалить черновик", callback_data=DraftCb(action="delete"))
    b.adjust(1)
    return b.as_markup()


def edit_menu_kb(fields: Sequence[tuple[str, str]], cover: bool = False) -> InlineKeyboardMarkup:
    """`fields`: (step, label) pairs in wizard order. `cover`: offer «Обложка»
    (right after the media step) when the draft has 2+ photos."""
    b = InlineKeyboardBuilder()
    for step, label in fields:
        b.button(text=label, callback_data=WizardCb(action="edit", value=step))
        if cover and step == "media":
            b.button(text="Обложка", callback_data=WizardCb(action="cover_menu"))
    b.adjust(3)
    b.row(InlineKeyboardButton(text=BACK_BUTTON, callback_data=DraftCb(action="back").pack()))
    return b.as_markup()


def cover_kb(photo_ids: Sequence[int]) -> InlineKeyboardMarkup:
    """«1», «2», … in album order; the value is the photos.id of that photo."""
    b = InlineKeyboardBuilder()
    for n, photo_id in enumerate(photo_ids, 1):
        b.button(text=str(n), callback_data=WizardCb(action="cover", value=str(photo_id)))
    b.adjust(5)
    b.row(InlineKeyboardButton(text=BACK_BUTTON, callback_data=DraftCb(action="back").pack()))
    return b.as_markup()


def sold_list_kb(items: Sequence) -> InlineKeyboardMarkup:
    """One row per active lot: model, price, public_id."""
    b = InlineKeyboardBuilder()
    for item in items:
        price = post_render.format_price(item.price) if item.price is not None else "?"
        text = f"{item.model or '?'} · {price} · {item.public_id}"
        b.button(text=text, callback_data=SoldCb(action="ask", public_id=item.public_id))
    b.adjust(1)
    return b.as_markup()


def sold_confirm_kb(public_id: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="Да, продано", callback_data=SoldCb(action="yes", public_id=public_id))
    b.button(text="Отмена", callback_data=SoldCb(action="no", public_id=public_id))
    b.adjust(1)
    return b.as_markup()
