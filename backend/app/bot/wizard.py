"""Lot creation wizard (/new). Every step persists to the draft row immediately,
so the FSM only tracks *which input we're waiting for*; after a restart `/new`
finds the draft and resumes from the first empty field.

Steps come from `drafts.steps_for(item)`: media → the device "matryoshka"
(manufacturer → device → [series] → model) → Apple options (storage → variant →
color, depend on the model) or manual entry (Apple «Другое»: device type →
model → year → storage → color; other brands: model → year → storage → color)
→ battery → kit → grade → price → note → preview.

The chosen series is kept in FSM data as "<category>.<key>" (key "series");
every nested keyboard has «← Назад» (`WizardCb(action="up", value=<level on
screen>)`)."""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InputMediaPhoto, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.keyboards import DraftCb, WizardCb
from app.config import Settings, get_settings
from app.db.models import Item, MediaKind
from app.services import catalog, drafts, notify, post_render, publisher

log = logging.getLogger(__name__)


class Wizard(StatesGroup):
    media = State()
    brand = State()  # buttons (typed text is accepted too)
    brand_text = State()  # after «Другой»
    device = State()
    series = State()
    model = State()  # Apple: buttons
    storage = State()
    variant = State()
    color = State()  # Apple: buttons
    device_type = State()  # Apple «Другое»
    model_text = State()  # "other": typed model
    year = State()
    color_text = State()  # "other": typed color
    battery = State()
    kit = State()
    grade = State()
    price = State()
    note = State()
    preview = State()


BUTTON_STATES: tuple[State, ...] = (
    Wizard.brand,
    Wizard.device,
    Wizard.series,
    Wizard.model,
    Wizard.storage,
    Wizard.variant,
    Wizard.color,
    Wizard.device_type,
    Wizard.kit,
    Wizard.grade,
)
# every state where a photo/video is not expected
NON_MEDIA_STATES: tuple[State, ...] = tuple(s for s in Wizard.__all_states__ if s is not Wizard.media)


def _state_for(step: str, item: Item) -> State:
    if step == "model" and drafts.is_other(item):
        return Wizard.model_text
    if step == "color" and drafts.is_other(item):
        return Wizard.color_text
    return getattr(Wizard, step)


# ---------- helpers ----------


async def _current_draft(session: AsyncSession, state: FSMContext, user_id: int) -> Item | None:
    data = await state.get_data()
    item_id = data.get("item_id")
    if item_id:
        item = await drafts.load_item(session, item_id)
        if item and item.status == "draft":
            return item
    item = await drafts.get_draft(session, user_id)
    if item:
        await state.update_data(item_id=item.id)
    return item


# Prompts are labels, not instructions (Lev 28.09: no explainer copy).
MEDIA_PROMPT = f"Фото и видео: до {drafts.MAX_MEDIA} файлов, видео до {drafts.MAX_VIDEO_SECONDS} с и 20 МБ."
NEED_PHOTO = "Нужно хотя бы одно фото."
BRAND_PROMPT = "Производитель:"
DEVICE_PROMPT = "Устройство:"
SERIES_PROMPT = "Серия:"
MODEL_PROMPT = "Модель:"
STALE_BUTTON = "Кнопка устарела, выберите заново"


async def _chosen_series(state: FSMContext, item: Item) -> str | None:
    """Series key picked for the draft's category, if it is still valid."""
    raw = (await state.get_data()).get("series") or ""
    category, _, key = raw.partition(".")
    if category != item.category or key not in {k for k, _ in catalog.series_for(category)}:
        return None
    return key


async def _prompt(step: str, item: Item, send, state: FSMContext) -> None:
    """Send the prompt for `step` and set the FSM state. `send` is
    message.answer-like: (text, reply_markup=...)."""
    await state.set_state(_state_for(step, item))
    other = drafts.is_other(item)
    match step:
        case "media":
            await send(MEDIA_PROMPT, reply_markup=kb.media_kb())
        case "brand":
            await send(BRAND_PROMPT, reply_markup=kb.brands_kb())
        case "device":
            await send(DEVICE_PROMPT, reply_markup=kb.devices_kb(apple=drafts.is_apple(item)))
        case "series":
            key = await _chosen_series(state, item)
            if key is None:
                await send(SERIES_PROMPT, reply_markup=kb.series_kb(item.category or ""))
            else:
                await state.set_state(Wizard.model)
                await send(MODEL_PROMPT, reply_markup=kb.models_kb(item.category or "", key))
        case "model" if other:
            await send(MODEL_PROMPT, reply_markup=kb.back_kb("model"))
        case "model":
            await send(MODEL_PROMPT, reply_markup=kb.models_kb(item.category or ""))
        case "storage":
            await send("Объём памяти:", reply_markup=kb.storages_kb(drafts.storage_choices(item), skippable=other))
        case "variant":
            await send(f"{drafts.step_label(item, 'variant')}:", reply_markup=kb.variants_kb(drafts.variant_choices(item)))
        case "color" if other:
            await send("Цвет:", reply_markup=kb.skip_kb("color"))
        case "color":
            await send("Цвет:", reply_markup=kb.colors_kb(drafts.color_choices(item)))
        case "device_type":
            await send("Тип устройства:", reply_markup=kb.device_types_kb())
        case "year":
            await send("Год выпуска:")
        case "battery":
            if drafts.battery_skippable(item):
                await send("Аккумулятор, %:", reply_markup=kb.skip_kb("battery"))
            else:
                await send("Аккумулятор, %:")
        case "kit":
            await send(
                "Комплект:",
                reply_markup=kb.kit_kb(drafts.kit_codes(item)),
            )
        case "grade":
            await send("Состояние:", reply_markup=kb.grades_kb())
        case "price":
            await send("Цена, ₽:")
        case "note":
            await send(
                "Заметка:",
                reply_markup=kb.note_kb(),
            )
        case _:  # pragma: no cover
            raise ValueError(step)


async def _advance(item: Item, send, state: FSMContext, bot, chat_id: int) -> None:
    step = drafts.next_step(item)
    if step is None:
        await _show_preview(item, send, state, bot, chat_id)
    else:
        await _prompt(step, item, send, state)


async def _show_preview(item: Item, send, state: FSMContext, bot, chat_id: int) -> None:
    # mirrors the channel post exactly: cover photo + caption + post buttons
    cover = publisher.cover_photo(item)
    if cover is None:
        await send(NEED_PHOTO)
        await _prompt("media", item, send, state)
        return
    await state.set_state(Wizard.preview)
    await bot.send_photo(
        chat_id=chat_id,
        photo=cover.tg_file_id,
        caption=post_render.render_caption(item),
        parse_mode="HTML",
        reply_markup=publisher.post_keyboard(get_settings(), item),
    )
    text = "Предпросмотр выше. Что делаем?"
    if len(item.photos) > 1:
        text = (
            "Предпросмотр выше. В канал уходит только первое фото, "
            "остальные фото и видео видны в приложении.\nЧто делаем?"
        )
    await send(text, reply_markup=kb.preview_kb())


def _answer_of(message: Message):
    async def send(text: str, reply_markup=None):
        return await message.answer(text, reply_markup=reply_markup)

    return send


async def _go_on(cq: CallbackQuery, item: Item, state: FSMContext) -> None:
    await _advance(item, _answer_of(cq.message), state, cq.bot, cq.message.chat.id)


async def _go_on_msg(message: Message, item: Item, state: FSMContext) -> None:
    await _advance(item, _answer_of(message), state, message.bot, message.chat.id)


# ---------- /new ----------


async def cmd_new(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    draft = await drafts.get_draft(session, message.from_user.id)
    if draft:
        await state.update_data(item_id=draft.id)
        await message.answer(
            f"Есть незавершённый черновик ({drafts.filled_summary(draft)}).",
            reply_markup=kb.draft_resume_kb(),
        )
        return
    item = await drafts.create_draft(session, message.from_user.id)
    await state.update_data(item_id=item.id)
    await _go_on_msg(message, item, state)


async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Ок, вышли из визарда. Черновик сохранён, /new чтобы продолжить.")


async def cb_continue(cq: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    item = await _current_draft(session, state, cq.from_user.id)
    await cq.answer()
    if not item:
        await cq.message.answer("Черновика нет. Наберите /new.")
        return
    await _go_on(cq, item, state)


async def cb_restart(cq: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    item = await _current_draft(session, state, cq.from_user.id)
    if item:
        await drafts.delete_draft(session, item)
    await state.clear()
    await cq.answer("Черновик удалён")
    item = await drafts.create_draft(session, cq.from_user.id)
    await state.update_data(item_id=item.id)
    await _go_on(cq, item, state)


# ---------- media ----------

# Album items arrive as separate updates that may be processed concurrently:
# a per-draft lock serializes the cap check, and one delayed progress reply per
# album replaces N replies. Single process, so in-memory state is enough.
ALBUM_REPLY_DELAY = 1.0
_media_locks: dict[int, asyncio.Lock] = {}
_album_replies: dict[str, asyncio.Task] = {}


def _media_progress(item: Item) -> str:
    photos, videos = drafts.media_counts(item)
    total = photos + videos
    if total >= drafts.MAX_MEDIA:
        return f"Файлов: {total}/{drafts.MAX_MEDIA}, это максимум."
    if photos == 0:
        return f"Файлов: {total}/{drafts.MAX_MEDIA}. {NEED_PHOTO}"
    return f"Файлов: {total}/{drafts.MAX_MEDIA}."


async def _album_reply_later(message: Message, key: str, text: str) -> None:
    await asyncio.sleep(ALBUM_REPLY_DELAY)
    if _album_replies.get(key) is asyncio.current_task():
        del _album_replies[key]
    try:
        await message.answer(text, reply_markup=kb.media_kb())
    except Exception:  # pragma: no cover - network errors in a detached task
        log.exception("album progress reply failed")


def _schedule_album_reply(message: Message, text: str) -> None:
    key = f"{message.chat.id}:{message.media_group_id}"
    previous = _album_replies.pop(key, None)
    if previous is not None:
        previous.cancel()
    _album_replies[key] = asyncio.create_task(_album_reply_later(message, key, text))


def _video_error(duration: int | None, file_size: int | None) -> str | None:
    if file_size is not None and file_size > drafts.MAX_VIDEO_BYTES:
        mb = file_size / (1024 * 1024)
        return f"Видео {mb:.0f} МБ, максимум 20 МБ."
    if duration is not None and duration > drafts.MAX_VIDEO_SECONDS:
        return f"Видео {duration} с, максимум {drafts.MAX_VIDEO_SECONDS} с."
    return None


async def _accept_media(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    kind: str,
    file_id: str,
    file_unique_id: str,
    duration: int | None = None,
    file_size: int | None = None,
) -> None:
    item = await _current_draft(session, state, message.from_user.id)
    if not item:
        await message.answer("Черновика нет. Наберите /new.")
        return
    if kind == MediaKind.video.value:
        error = _video_error(duration, file_size)
        if error:
            await message.answer(error, reply_markup=kb.media_kb())
            return
    lock = _media_locks.setdefault(item.id, asyncio.Lock())
    async with lock:
        result = await drafts.add_media(
            session, item, kind, file_id, file_unique_id, duration=duration, file_size=file_size
        )
        if result is drafts.AddMediaResult.added and message.media_group_id:
            # scheduled under the lock, so the last reply carries the final count
            _schedule_album_reply(message, _media_progress(item))
            return
    match result:
        case drafts.AddMediaResult.added:
            text = _media_progress(item)
        case drafts.AddMediaResult.duplicate:
            text = "Этот файл уже добавлен. " + _media_progress(item)
        case drafts.AddMediaResult.full:
            text = f"Уже {drafts.MAX_MEDIA} файлов, это максимум."
        case drafts.AddMediaResult.need_photo:
            text = f"Последнее место — для фото. {NEED_PHOTO}"
    await message.answer(text, reply_markup=kb.media_kb())


async def step_photo(message: Message, state: FSMContext, session: AsyncSession) -> None:
    largest = message.photo[-1]
    await _accept_media(
        message, state, session, MediaKind.photo.value, largest.file_id, largest.file_unique_id,
        file_size=largest.file_size,
    )


async def step_video(message: Message, state: FSMContext, session: AsyncSession) -> None:
    video = message.video
    await _accept_media(
        message, state, session, MediaKind.video.value, video.file_id, video.file_unique_id,
        duration=video.duration, file_size=video.file_size,
    )


def _is_video_document(message: Message) -> bool:
    return bool(message.document and (message.document.mime_type or "").startswith("video/"))


async def step_video_document(message: Message, state: FSMContext, session: AsyncSession) -> None:
    # A document carries no duration, so only the size limit can be checked here.
    doc = message.document
    await _accept_media(
        message, state, session, MediaKind.video.value, doc.file_id, doc.file_unique_id,
        file_size=doc.file_size,
    )


async def step_media_other(message: Message) -> None:
    if message.document and (message.document.mime_type or "").startswith("image/"):
        await message.answer("Фото нужно отправить как фото, не файлом.", reply_markup=kb.media_kb())
        return
    await message.answer("Нужны фото или видео.", reply_markup=kb.media_kb())


async def step_media_elsewhere(message: Message) -> None:
    await message.answer(
        "Сейчас не шаг фото."
    )


async def cb_media_done(cq: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    item = await _current_draft(session, state, cq.from_user.id)
    if not item:
        await cq.answer("Черновика нет", show_alert=True)
        return
    photos, _ = drafts.media_counts(item)
    if photos == 0:
        await cq.answer(NEED_PHOTO, show_alert=True)
        return
    await cq.answer()
    await _go_on(cq, item, state)


# ---------- inline choices ----------


async def cb_legacy_category(cq: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """«Что продаём?» keyboards sent before the matryoshka: show the current step."""
    item = await _current_draft(session, state, cq.from_user.id)
    await cq.answer(STALE_BUTTON, show_alert=True)
    if item:
        await _go_on(cq, item, state)


async def _draft_at(cq: CallbackQuery, state: FSMContext, session: AsyncSession, step: str) -> Item | None:
    """The draft if the wizard is waiting for `step`; otherwise answers the
    callback (no draft / stale button) and returns None."""
    item = await _current_draft(session, state, cq.from_user.id)
    if not item:
        await cq.answer("Черновика нет", show_alert=True)
        return None
    if drafts.next_step(item) != step:
        await cq.answer(STALE_BUTTON, show_alert=True)
        return None
    return item


async def cb_brand(cq: CallbackQuery, callback_data: WizardCb, state: FSMContext, session: AsyncSession) -> None:
    value = callback_data.value
    if value != kb.OTHER_BRAND_VALUE and value not in catalog.WIZARD_BRANDS:
        await cq.answer(STALE_BUTTON, show_alert=True)
        return
    item = await _draft_at(cq, state, session, "brand")
    if not item:
        return
    if value == kb.OTHER_BRAND_VALUE:
        await cq.answer()
        await state.set_state(Wizard.brand_text)
        await cq.message.answer(BRAND_PROMPT, reply_markup=kb.back_kb("brand"))
        return
    await drafts.set_field(session, item, "brand", value)
    await state.update_data(series=None)
    await cq.answer(value)
    await _go_on(cq, item, state)


async def cb_device(cq: CallbackQuery, callback_data: WizardCb, state: FSMContext, session: AsyncSession) -> None:
    kind, _, code = callback_data.value.partition(".")
    item = await _draft_at(cq, state, session, "device")
    if not item:
        return
    if (kind == "a") != drafts.is_apple(item) or kind not in ("a", "t"):
        await cq.answer(STALE_BUTTON, show_alert=True)
        return
    try:
        await drafts.set_field(session, item, "device", code)
    except ValueError:
        await session.rollback()
        await cq.answer(STALE_BUTTON, show_alert=True)
        return
    await state.update_data(series=None)
    await cq.answer(drafts.device_label(item) or drafts.OTHER_CATEGORY_LABEL)
    await _go_on(cq, item, state)


async def cb_series(cq: CallbackQuery, callback_data: WizardCb, state: FSMContext, session: AsyncSession) -> None:
    category, _, key = callback_data.value.partition(".")
    labels = dict(catalog.series_for(category))
    item = await _draft_at(cq, state, session, "series")
    if not item:
        return
    if item.category != category or key not in labels:
        await cq.answer(STALE_BUTTON, show_alert=True)
        return
    models = catalog.models_in_series(category, key)
    if len(models) == 1:
        # a one-model series is the model itself
        await drafts.set_field(session, item, "model", models[0].name)
        await state.update_data(series=None)
        await cq.answer(models[0].name)
    else:
        await state.update_data(series=f"{category}.{key}")
        await cq.answer(labels[key])
    await _go_on(cq, item, state)


async def cb_up(cq: CallbackQuery, callback_data: WizardCb, state: FSMContext, session: AsyncSession) -> None:
    """«← Назад»: one level up the matryoshka (brand ← device ← series ← model)."""
    level = callback_data.value
    item = await _current_draft(session, state, cq.from_user.id)
    if not item:
        await cq.answer("Черновика нет", show_alert=True)
        return
    step = drafts.next_step(item)
    series = await _chosen_series(state, item) if step == "series" else None
    on_screen = "model" if series else step
    if level != on_screen or level not in ("brand", "device", "series", "model", "device_type"):
        await cq.answer(STALE_BUTTON, show_alert=True)
        return
    if not series:  # with a chosen series, «Назад» from the models only drops it
        await drafts.step_back(session, item, level)
    await state.update_data(series=None)
    await cq.answer()
    await _go_on(cq, item, state)


async def cb_model(cq: CallbackQuery, callback_data: WizardCb, state: FSMContext, session: AsyncSession) -> None:
    category, _, raw_idx = callback_data.value.partition(".")
    models = catalog.models_for(category)
    if not raw_idx.isdigit() or int(raw_idx) >= len(models):
        await cq.answer("Кнопка устарела, выберите модель заново", show_alert=True)
        return
    item = await _current_draft(session, state, cq.from_user.id)
    if not item:
        await cq.answer("Черновика нет", show_alert=True)
        return
    if item.category != category:
        await cq.answer("Кнопка от другой категории, выберите заново", show_alert=True)
        return
    spec = models[int(raw_idx)]
    await drafts.set_field(session, item, "model", spec.name)
    await state.update_data(series=None)
    await cq.answer(spec.name)
    await _go_on(cq, item, state)


async def cb_storage(cq: CallbackQuery, callback_data: WizardCb, state: FSMContext, session: AsyncSession) -> None:
    item = await _current_draft(session, state, cq.from_user.id)
    if not item or not item.category:
        await cq.answer("Сначала устройство", show_alert=True)
        return
    value = callback_data.value
    choices = drafts.storage_choices(item)
    if not value.isdigit() or int(value) not in choices:
        await cq.answer("Нет такого объёма для этой модели", show_alert=True)
        return
    await drafts.set_field(session, item, "storage", int(value))
    await cq.answer(catalog.format_storage(int(value)))
    await _go_on(cq, item, state)


async def _pick_indexed(
    cq: CallbackQuery, callback_data: WizardCb, state: FSMContext, session: AsyncSession, step: str, choices_of
) -> None:
    item = await _current_draft(session, state, cq.from_user.id)
    if not item or not item.model or drafts.is_other(item):
        await cq.answer("Сначала модель", show_alert=True)
        return
    choices = choices_of(item)
    raw = callback_data.value
    if not raw.isdigit() or int(raw) >= len(choices):
        await cq.answer("Нет такого варианта", show_alert=True)
        return
    value = choices[int(raw)]
    await drafts.set_field(session, item, step, value)
    await cq.answer(value)
    await _go_on(cq, item, state)


async def cb_variant(cq: CallbackQuery, callback_data: WizardCb, state: FSMContext, session: AsyncSession) -> None:
    await _pick_indexed(cq, callback_data, state, session, "variant", drafts.variant_choices)


async def cb_color(cq: CallbackQuery, callback_data: WizardCb, state: FSMContext, session: AsyncSession) -> None:
    await _pick_indexed(cq, callback_data, state, session, "color", drafts.color_choices)


async def cb_device_type(cq: CallbackQuery, callback_data: WizardCb, state: FSMContext, session: AsyncSession) -> None:
    label = catalog.device_type_label(callback_data.value)
    if label is None:
        await cq.answer("Нет такого типа", show_alert=True)
        return
    item = await _current_draft(session, state, cq.from_user.id)
    if not item or not drafts.is_other(item):
        await cq.answer("Тип устройства выбирается в категории «Другое»", show_alert=True)
        return
    await drafts.set_field(session, item, "device_type", callback_data.value)
    await cq.answer(label)
    await _go_on(cq, item, state)


async def cb_skip(cq: CallbackQuery, callback_data: WizardCb, state: FSMContext, session: AsyncSession) -> None:
    item = await _current_draft(session, state, cq.from_user.id)
    if not item:
        await cq.answer("Черновика нет", show_alert=True)
        return
    step = callback_data.value
    if step not in drafts.steps_for(item) or step not in drafts.skippable_steps(item):
        await cq.answer("Этот шаг нельзя пропустить", show_alert=True)
        return
    await drafts.skip_field(session, item, step)
    await cq.answer("Пропущено")
    await _go_on(cq, item, state)


async def cb_kit_toggle(cq: CallbackQuery, callback_data: WizardCb, state: FSMContext, session: AsyncSession) -> None:
    item = await _current_draft(session, state, cq.from_user.id)
    if not item:
        await cq.answer("Черновика нет", show_alert=True)
        return
    data = await state.get_data()
    selected = list(data.get("kit_selected") or drafts.kit_codes(item))
    code = callback_data.value
    if code not in catalog.kit_codes():
        await cq.answer()
        return
    if code in selected:
        selected.remove(code)
    else:
        selected.append(code)
    selected = [c for c in catalog.kit_codes() if c in selected]
    await state.update_data(kit_selected=selected)
    await cq.answer()
    await cq.message.edit_reply_markup(reply_markup=kb.kit_kb(selected))


async def cb_kit_done(cq: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    item = await _current_draft(session, state, cq.from_user.id)
    if not item:
        await cq.answer("Черновика нет", show_alert=True)
        return
    data = await state.get_data()
    selected = data.get("kit_selected")
    if selected is None:
        selected = drafts.kit_codes(item)
    await drafts.set_field(session, item, "kit", selected)
    await state.update_data(kit_selected=None)
    await cq.answer()
    await _go_on(cq, item, state)


async def cb_grade(cq: CallbackQuery, callback_data: WizardCb, state: FSMContext, session: AsyncSession) -> None:
    item = await _current_draft(session, state, cq.from_user.id)
    if not item:
        await cq.answer("Черновика нет", show_alert=True)
        return
    if callback_data.value not in catalog.grade_codes():
        await cq.answer("Нет такого состояния", show_alert=True)
        return
    await drafts.set_field(session, item, "grade", callback_data.value)
    await cq.answer()
    await _go_on(cq, item, state)


async def cb_note_skip(cq: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    item = await _current_draft(session, state, cq.from_user.id)
    if not item:
        await cq.answer("Черновика нет", show_alert=True)
        return
    await drafts.set_field(session, item, "note", None)
    await cq.answer()
    await _go_on(cq, item, state)


# ---------- text inputs ----------


async def _short_text(message: Message, example: str) -> str | None:
    """Validated 1..64-char free text for the manual branch, or None after
    telling the admin what's wrong."""
    text = " ".join(message.text.split())
    if not text or text.startswith("/"):
        await message.answer("Нужен текст.")
        return None
    if len(text) > drafts.TEXT_FIELD_MAX:
        await message.answer(f"Слишком длинно: максимум {drafts.TEXT_FIELD_MAX} символа.")
        return None
    return text


async def _set_text_step(message: Message, state: FSMContext, session: AsyncSession, step: str, value) -> None:
    item = await _current_draft(session, state, message.from_user.id)
    if not item:
        await message.answer("Черновика нет. Наберите /new.")
        return
    await drafts.set_field(session, item, step, value)
    await _go_on_msg(message, item, state)


async def step_brand(message: Message, state: FSMContext, session: AsyncSession) -> None:
    text = await _short_text(message, "Samsung")
    if text is None:
        return
    await state.update_data(series=None)
    await _set_text_step(message, state, session, "brand", text)


async def step_model_text(message: Message, state: FSMContext, session: AsyncSession) -> None:
    text = await _short_text(message, "Galaxy S23")
    if text is not None:
        await _set_text_step(message, state, session, "model", text)


async def step_color_text(message: Message, state: FSMContext, session: AsyncSession) -> None:
    text = await _short_text(message, "чёрный")
    if text is not None:
        await _set_text_step(message, state, session, "color", text)


async def step_year(message: Message, state: FSMContext, session: AsyncSession) -> None:
    raw = message.text.strip()
    if not (len(raw) == 4 and raw.isdigit() and catalog.validate_year(int(raw))):
        await message.answer(
            f"Год: {catalog.MIN_YEAR}–{date.today().year}."
        )
        return
    await _set_text_step(message, state, session, "year", int(raw))


async def step_battery(message: Message, state: FSMContext, session: AsyncSession) -> None:
    raw = message.text.strip().rstrip("%").strip()
    if not raw.isdigit() or not 0 <= int(raw) <= 100:
        await message.answer("Нужно целое число от 0 до 100. Например: 92")
        return
    await _set_text_step(message, state, session, "battery", int(raw))


async def step_price(message: Message, state: FSMContext, session: AsyncSession) -> None:
    raw = message.text.replace(" ", "").replace("₽", "").replace("р", "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("Нужно целое число больше нуля. Например: 75000")
        return
    await _set_text_step(message, state, session, "price", int(raw))


async def step_note(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await _set_text_step(message, state, session, "note", message.text.strip())


async def step_expects_button(message: Message) -> None:
    await message.answer("Выберите вариант кнопкой выше.")


# ---------- preview actions ----------


async def cb_edit_menu(cq: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    item = await _current_draft(session, state, cq.from_user.id)
    await cq.answer()
    if not item:
        await cq.message.answer("Черновика нет. Наберите /new.")
        return
    fields = [(step, drafts.step_label(item, step)) for step in drafts.editable_steps(item)]
    cover = len(drafts.photo_media(item)) >= 2
    await cq.message.answer("Что изменить?", reply_markup=kb.edit_menu_kb(fields, cover=cover))


COVER_PROMPT = "Выберите обложку."


async def cb_cover_menu(cq: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Album of the draft's photos (videos can't be the cover) + «1», «2», … buttons."""
    item = await _current_draft(session, state, cq.from_user.id)
    if not item:
        await cq.answer("Черновика нет", show_alert=True)
        return
    photos = drafts.photo_media(item)[: drafts.MAX_MEDIA]
    if len(photos) < 2:
        await cq.answer("Нужно хотя бы два фото", show_alert=True)
        return
    await cq.answer()
    await cq.bot.send_media_group(
        chat_id=cq.message.chat.id,
        media=[InputMediaPhoto(media=p.tg_file_id) for p in photos],
    )
    await cq.message.answer(COVER_PROMPT, reply_markup=kb.cover_kb([p.id for p in photos]))


async def cb_cover(cq: CallbackQuery, callback_data: WizardCb, state: FSMContext, session: AsyncSession) -> None:
    item = await _current_draft(session, state, cq.from_user.id)
    if not item:
        await cq.answer("Черновика нет", show_alert=True)
        return
    raw = callback_data.value
    photo = next((p for p in drafts.photo_media(item) if raw.isdigit() and p.id == int(raw)), None)
    if photo is None:
        await cq.answer("Кнопка устарела, выберите заново", show_alert=True)
        return
    await drafts.set_cover(session, item, photo)
    await cq.answer()
    await _go_on(cq, item, state)


async def cb_back(cq: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    item = await _current_draft(session, state, cq.from_user.id)
    await cq.answer()
    if not item:
        await cq.message.answer("Черновика нет. Наберите /new.")
        return
    await _go_on(cq, item, state)


async def cb_edit_field(cq: CallbackQuery, callback_data: WizardCb, state: FSMContext, session: AsyncSession) -> None:
    item = await _current_draft(session, state, cq.from_user.id)
    if not item:
        await cq.answer("Черновика нет", show_alert=True)
        return
    step = callback_data.value
    if step == "category":  # «Категория» buttons sent before the matryoshka
        step = "brand"
    if step not in drafts.editable_steps(item):
        await cq.answer("Нет такого поля", show_alert=True)
        return
    await drafts.reset_field(session, item, step)
    await state.update_data(kit_selected=None, series=None)
    await cq.answer()
    # the reset step is the first empty one; dependent steps follow it
    await _go_on(cq, item, state)


async def cb_delete(cq: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    item = await _current_draft(session, state, cq.from_user.id)
    if item:
        await drafts.delete_draft(session, item)
    await state.clear()
    await cq.answer("Черновик удалён")
    await cq.message.answer("Черновик удалён. /new чтобы создать новый.")


async def cb_publish(cq: CallbackQuery, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    item = await _current_draft(session, state, cq.from_user.id)
    if not item:
        await cq.answer("Черновика нет", show_alert=True)
        return
    missing = drafts.next_step(item)
    if missing is not None:
        await cq.answer(f"Не заполнено: {drafts.step_label(item, missing)}", show_alert=True)
        return
    await cq.answer("Публикую…")
    try:
        result = await publisher.publish(session, cq.bot, settings, item)
    except publisher.PublishError as exc:
        await cq.message.answer(f"Не удалось опубликовать: {exc}")
        return
    except Exception:
        log.exception("publish failed for %s", item.public_id)
        await cq.message.answer("Ошибка Telegram при публикации. Попробуйте ещё раз через минуту.")
        return
    await state.clear()
    await cq.message.answer(
        f"Опубликовано: лот {result.public_id}\n{result.post_url}",
        disable_web_page_preview=True,
        reply_markup=kb.admin_reply_kb(settings),
    )
    if result.broadcast is not None:
        # sent after the confirmation above, when the subscriber broadcast ends
        notify.spawn(notify.report_when_done(cq.bot, cq.message.chat.id, result.broadcast, result.public_id))


def build_router() -> Router:
    router = Router(name="wizard")
    router.message.register(cmd_new, Command("new"))
    router.message.register(cmd_cancel, Command("cancel"))
    router.callback_query.register(cb_continue, DraftCb.filter(F.action == "continue"))
    router.callback_query.register(cb_restart, DraftCb.filter(F.action == "restart"))

    router.message.register(step_photo, Wizard.media, F.photo)
    router.message.register(step_video, Wizard.media, F.video)
    router.message.register(step_video_document, Wizard.media, _is_video_document)
    router.message.register(step_media_other, Wizard.media)
    router.message.register(step_media_elsewhere, StateFilter(*NON_MEDIA_STATES), F.photo | F.video | F.document)
    # "photos_done": «Готово» buttons still sitting in chats from before v2
    router.callback_query.register(cb_media_done, WizardCb.filter(F.action.in_({"media_done", "photos_done"})))

    router.callback_query.register(cb_legacy_category, WizardCb.filter(F.action == "category"))
    router.callback_query.register(cb_brand, WizardCb.filter(F.action == "brand"))
    router.callback_query.register(cb_device, WizardCb.filter(F.action == "device"))
    router.callback_query.register(cb_series, WizardCb.filter(F.action == "series"))
    router.callback_query.register(cb_up, WizardCb.filter(F.action == "up"))
    router.callback_query.register(cb_model, WizardCb.filter(F.action == "model"))
    router.callback_query.register(cb_storage, WizardCb.filter(F.action == "storage"))
    router.callback_query.register(cb_variant, WizardCb.filter(F.action == "variant"))
    router.callback_query.register(cb_color, WizardCb.filter(F.action == "color"))
    router.callback_query.register(cb_device_type, WizardCb.filter(F.action == "device_type"))
    router.callback_query.register(cb_skip, WizardCb.filter(F.action == "skip"))
    router.callback_query.register(cb_kit_toggle, WizardCb.filter(F.action == "kit"))
    router.callback_query.register(cb_kit_done, WizardCb.filter(F.action == "kit_done"))
    router.callback_query.register(cb_grade, WizardCb.filter(F.action == "grade"))
    router.callback_query.register(cb_note_skip, WizardCb.filter(F.action == "note_skip"))

    router.message.register(step_brand, StateFilter(Wizard.brand, Wizard.brand_text), F.text)
    router.message.register(step_model_text, Wizard.model_text, F.text)
    router.message.register(step_year, Wizard.year, F.text)
    router.message.register(step_color_text, Wizard.color_text, F.text)
    router.message.register(step_battery, Wizard.battery, F.text)
    router.message.register(step_price, Wizard.price, F.text)
    router.message.register(step_note, Wizard.note, F.text)
    router.message.register(step_expects_button, StateFilter(*BUTTON_STATES))

    router.callback_query.register(cb_edit_menu, DraftCb.filter(F.action == "edit_menu"))
    router.callback_query.register(cb_back, DraftCb.filter(F.action == "back"))
    router.callback_query.register(cb_edit_field, WizardCb.filter(F.action == "edit"))
    router.callback_query.register(cb_cover_menu, WizardCb.filter(F.action == "cover_menu"))
    router.callback_query.register(cb_cover, WizardCb.filter(F.action == "cover"))
    router.callback_query.register(cb_delete, DraftCb.filter(F.action == "delete"))
    router.callback_query.register(cb_publish, DraftCb.filter(F.action == "publish"))
    return router
