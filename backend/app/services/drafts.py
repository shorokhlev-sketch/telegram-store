"""Draft item lifecycle used by the bot wizard.

A draft is an `items` row with status=draft. Every wizard step writes straight
to the row, so a server restart never loses input: the next step is derived
from which fields are still empty.

The step list is not static. The device is picked as a "matryoshka":
manufacturer -> device -> [series] -> model. `steps_for(item)` depends on the
manufacturer (Apple catalog vs. manual entry), the device (Apple category or
"other") and the chosen Apple model (which options it has). A step the admin
explicitly skipped is recorded in `items.skipped_json` and counts as done.

Data written is the same as before the matryoshka: `brand` is set as soon as
the manufacturer is picked, `category` (+ `device_type`) when the device is
picked. The series is a navigation level only: it lives in the wizard's FSM
data and counts as done once a model is set, so after a restart the wizard asks
the series again.
"""

from __future__ import annotations

import base64
import json
import secrets
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Event, EventType, Item, ItemStatus, MediaKind, Photo
from app.services import catalog

MAX_MEDIA = 10
# Bot API getFile only downloads files up to 20 MB, so bigger videos could never
# reach the Mini App / site.
MAX_VIDEO_BYTES = 20 * 1024 * 1024
MAX_VIDEO_SECONDS = 60

OTHER_CATEGORY = "other"
OTHER_CATEGORY_LABEL = "Другое"
# Storage buttons for the manual branch (GB).
OTHER_STORAGES: tuple[int, ...] = (32, 64, 128, 256, 512, 1024, 2048)
TEXT_FIELD_MAX = 64

_COMMON_TAIL: tuple[str, ...] = ("battery", "kit", "grade", "price", "note")
# Manual entry after the device: Apple «Другое» also asks the device type.
_MANUAL_TAIL: tuple[str, ...] = ("model", "year", "storage", "color")

# Every step name the wizard knows (superset of any `steps_for` result).
ALL_STEPS: tuple[str, ...] = (
    "media",
    "brand",
    "device",
    "series",
    "device_type",
    "model",
    "year",
    "storage",
    "variant",
    "color",
) + _COMMON_TAIL

STEP_LABELS: dict[str, str] = {
    "media": "Фото и видео",
    "brand": "Производитель",
    "device": "Устройство",
    "series": "Серия",
    "device_type": "Тип устройства",
    "model": "Модель",
    "year": "Год",
    "storage": "Память",
    "variant": "Опция",
    "color": "Цвет",
    "battery": "АКБ",
    "kit": "Комплект",
    "grade": "Состояние",
    "price": "Цена",
    "note": "Заметка",
}

# Fields below the manufacturer: cleared when the device changes.
_DEVICE_STEPS: tuple[str, ...] = ("device_type", "model", "year", "storage", "variant", "color")
# Steps shown in «Изменить поле»: the device and series are reached through
# «Производитель» (from the top) or «Модель» (from the device, Apple).
_NOT_EDITABLE: frozenset[str] = frozenset({"device", "series", "device_type"})
# Apple options that depend on the chosen model.
_MODEL_OPTION_STEPS: tuple[str, ...] = ("storage", "variant", "color")


def generate_public_id() -> str:
    return base64.b32encode(secrets.token_bytes(5)).decode("ascii")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Branch helpers
# ---------------------------------------------------------------------------


def is_other(item: Item) -> bool:
    return item.category == OTHER_CATEGORY


def is_apple(item: Item) -> bool:
    """Apple lot: manufacturer Apple, or (legacy drafts) an Apple catalog category."""
    return item.brand == catalog.APPLE or catalog.get_category(item.category or "") is not None


def is_apple_catalog(item: Item) -> bool:
    """Picked from the Apple menu (not Apple «Другое»)."""
    return catalog.get_category(item.category or "") is not None


def apple_spec(item: Item) -> catalog.ModelSpec | None:
    if is_other(item) or not item.model:
        return None
    return catalog.get_apple_model(item.model)


def storage_choices(item: Item) -> tuple[int, ...]:
    if is_other(item):
        return OTHER_STORAGES
    spec = apple_spec(item)
    return spec.storages if spec else ()


def variant_choices(item: Item) -> tuple[str, ...]:
    spec = apple_spec(item)
    return spec.variants if spec else ()


def color_choices(item: Item) -> tuple[str, ...]:
    spec = apple_spec(item)
    return spec.colors if spec else ()


def battery_skippable(item: Item) -> bool:
    if is_other(item):
        return True
    category = catalog.get_category(item.category or "")
    return bool(category and category.battery_skippable)


def skippable_steps(item: Item) -> frozenset[str]:
    steps = {"note"}
    if is_other(item):
        steps |= {"storage", "color"}
    if battery_skippable(item):
        steps.add("battery")
    return frozenset(steps)


def device_label(item: Item) -> str | None:
    """Short device name for the summary: Apple «iPhone»/«Mac», otherwise the device type."""
    if is_apple_catalog(item):
        return dict(catalog.APPLE_DEVICES).get(item.category)
    if item.device_type:
        return catalog.device_type_label(item.device_type)
    return None


def step_label(item: Item, step: str) -> str:
    if step == "variant":
        category = catalog.get_category(item.category or "")
        if category and category.variant_label:
            return category.variant_label
    return STEP_LABELS[step]


# ---------------------------------------------------------------------------
# Skip marks (items.skipped_json)
# ---------------------------------------------------------------------------


def skipped_steps(item: Item) -> set[str]:
    if not item.skipped_json:
        return set()
    try:
        raw = json.loads(item.skipped_json)
    except json.JSONDecodeError:
        return set()
    return {s for s in raw if isinstance(s, str)} if isinstance(raw, list) else set()


def _write_skipped(item: Item, skipped: set[str]) -> None:
    ordered = [s for s in ALL_STEPS if s in skipped]
    item.skipped_json = json.dumps(ordered) if ordered else None


def _unskip(item: Item, *steps: str) -> None:
    skipped = skipped_steps(item)
    if skipped & set(steps):
        _write_skipped(item, skipped - set(steps))


# ---------------------------------------------------------------------------
# Step engine
# ---------------------------------------------------------------------------


def _apple_option_steps(item: Item) -> list[str]:
    """Steps after the model in the Apple branch. Options with a single choice
    are set automatically when the model is picked, so they are not asked."""
    category = catalog.get_category(item.category or "")
    spec = apple_spec(item)
    if spec is None:
        if item.model:
            # Legacy lot whose model left the catalog: keep whatever was filled.
            return [s for s, v in (("storage", item.storage_gb), ("variant", item.variant), ("color", item.color)) if v is not None]
        # Model not chosen yet: the category tells which options will follow.
        steps = []
        if category and category.has_storage:
            steps.append("storage")
        if category and category.variant_label:
            steps.append("variant")
        return steps + ["color"]
    steps = []
    if category and category.has_storage and len(spec.storages) > 1:
        steps.append("storage")
    if len(spec.variants) > 1:
        steps.append("variant")
    if len(spec.colors) > 1:
        steps.append("color")
    return steps


def steps_for(item: Item) -> list[str]:
    """media -> brand -> device -> then per branch:
    Apple menu:    [series] -> model -> options (storage / variant / color);
    Apple «Другое»: device_type -> model -> year -> storage -> color;
    other brands:  model -> year -> storage -> color;
    then battery -> kit -> grade -> price -> note."""
    steps = ["media", "brand", "device"]
    if is_apple_catalog(item):
        if catalog.needs_series(item.category):
            steps.append("series")
        steps.append("model")
        steps += _apple_option_steps(item)
    elif is_other(item) and is_apple(item):
        steps += ["device_type", *_MANUAL_TAIL]
    elif is_other(item) or (item.brand and not is_apple(item)):
        steps += _MANUAL_TAIL
    else:
        steps.append("model")  # device not picked yet: the branch is unknown
    return steps + list(_COMMON_TAIL)


def editable_steps(item: Item) -> list[str]:
    """Steps offered in «Изменить поле», in wizard order."""
    return [s for s in steps_for(item) if s not in _NOT_EDITABLE]


def media_counts(item: Item) -> tuple[int, int]:
    """(photos, videos) among the item's media."""
    photos = sum(1 for m in item.photos if (m.kind or MediaKind.photo.value) == MediaKind.photo.value)
    return photos, len(item.photos) - photos


def _is_done(item: Item, step: str) -> bool:
    if step in skipped_steps(item) and step in skippable_steps(item):
        return True
    match step:
        case "media":
            # the channel post needs a photo as its cover; a video alone is not enough
            return media_counts(item)[0] > 0
        case "brand":
            return bool(item.brand)
        case "device":
            if is_apple(item):
                return item.category is not None
            return is_other(item) and item.device_type is not None
        case "series":
            return bool(item.model)
        case "device_type":
            return item.device_type is not None
        case "model":
            return bool(item.model)
        case "year":
            return item.year is not None
        case "storage":
            return item.storage_gb is not None
        case "variant":
            return item.variant is not None
        case "color":
            return item.color is not None
        case "battery":
            return item.battery_health is not None
        case "kit":
            return item.kit_json is not None
        case "grade":
            return item.grade is not None
        case "price":
            return item.price is not None
        case "note":
            return item.note is not None
    raise ValueError(step)


def next_step(item: Item) -> str | None:
    """First incomplete step, or None when the draft is ready for preview."""
    for step in steps_for(item):
        if not _is_done(item, step):
            return step
    return None


def filled_summary(item: Item) -> str:
    parts: list[str] = []
    photos, videos = media_counts(item)
    if photos:
        parts.append(f"фото: {photos}")
    if videos:
        parts.append(f"видео: {videos}")
    if item.brand and not (is_apple_catalog(item) and item.model):
        parts.append(item.brand)
    device = device_label(item)
    if device and not (is_apple_catalog(item) and item.model):
        parts.append(device)
    if item.model:
        parts.append(item.model)
    if item.year:
        parts.append(str(item.year))
    if item.storage_gb:
        parts.append(catalog.format_storage(item.storage_gb))
    if item.variant:
        parts.append(item.variant)
    if item.color:
        parts.append(item.color)
    if item.battery_health is not None:
        parts.append(f"АКБ {item.battery_health}%")
    if item.grade:
        parts.append(f"состояние {item.grade}")
    if item.price is not None:
        parts.append(f"{item.price:,} ₽".replace(",", " "))
    return ", ".join(parts) if parts else "пусто"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


async def get_draft(session: AsyncSession, admin_id: int) -> Item | None:
    stmt = (
        select(Item)
        .where(Item.status == ItemStatus.draft.value, Item.created_by == admin_id)
        .options(selectinload(Item.photos))
        .order_by(Item.created_at.desc())
        .limit(1)
    )
    return await session.scalar(stmt)


async def load_item(session: AsyncSession, item_id: int) -> Item | None:
    stmt = (
        select(Item)
        .where(Item.id == item_id)
        .options(selectinload(Item.photos), selectinload(Item.channel_messages))
    )
    return await session.scalar(stmt)


async def create_draft(session: AsyncSession, admin_id: int) -> Item:
    for _ in range(5):
        public_id = generate_public_id()
        exists = await session.scalar(select(Item.id).where(Item.public_id == public_id))
        if not exists:
            break
    else:  # pragma: no cover
        raise RuntimeError("could not allocate a unique public_id")
    item = Item(public_id=public_id, status=ItemStatus.draft.value, created_by=admin_id)
    session.add(item)
    await session.flush()
    session.add(Event(item_id=item.id, type=EventType.created.value, payload_json=None))
    await session.commit()
    return await load_item(session, item.id)


async def delete_draft(session: AsyncSession, item: Item) -> None:
    await session.delete(item)
    await session.commit()


class AddMediaResult(str, Enum):
    added = "added"
    duplicate = "duplicate"
    full = "full"
    need_photo = "need_photo"  # the last free slot is reserved for the first photo


async def add_media(
    session: AsyncSession,
    item: Item,
    kind: str,
    tg_file_id: str,
    file_unique_id: str,
    *,
    duration: int | None = None,
    file_size: int | None = None,
) -> AddMediaResult:
    """Append a photo or video. Reads the current media from the DB (not the
    possibly stale `item.photos`), so concurrent album messages serialized by
    the caller never overflow the cap. `item.photos` is refreshed afterwards."""
    rows = list(
        (await session.scalars(select(Photo).where(Photo.item_id == item.id).order_by(Photo.position))).all()
    )
    result: AddMediaResult
    if any(p.file_unique_id == file_unique_id for p in rows):
        result = AddMediaResult.duplicate
    elif len(rows) >= MAX_MEDIA:
        result = AddMediaResult.full
    elif (
        kind != MediaKind.photo.value
        and len(rows) == MAX_MEDIA - 1
        and not any((p.kind or MediaKind.photo.value) == MediaKind.photo.value for p in rows)
    ):
        result = AddMediaResult.need_photo
    else:
        position = (max(p.position for p in rows) + 1) if rows else 0
        session.add(
            Photo(
                item_id=item.id,
                kind=kind,
                tg_file_id=tg_file_id,
                file_unique_id=file_unique_id,
                position=position,
                duration=duration,
                file_size=file_size,
            )
        )
        await session.commit()
        result = AddMediaResult.added
    await session.refresh(item, ["photos"])
    return result


def photo_media(item: Item) -> list[Photo]:
    """Media of kind photo in display order (the ones that can be the cover)."""
    return [m for m in item.photos if (m.kind or MediaKind.photo.value) == MediaKind.photo.value]


def move_to_front(item: Item, photo: Photo) -> bool:
    """Make `photo` the cover: it becomes the first media, the rest keep their
    order (positions renumbered 0..n-1). False when it already is first.
    Does not commit; refresh `item.photos` after the commit to re-sort."""
    if photo.kind not in (None, MediaKind.photo.value) or photo not in item.photos:
        raise ValueError("only a photo of this item can be the cover")
    ordered = sorted(item.photos, key=lambda m: m.position)
    if ordered[0] is photo:
        return False
    ordered.remove(photo)
    ordered.insert(0, photo)
    for position, media in enumerate(ordered):
        media.position = position
    return True


async def set_cover(session: AsyncSession, item: Item, photo: Photo) -> bool:
    """Wizard: reorder so `photo` is first, commit, reload `item.photos`."""
    changed = move_to_front(item, photo)
    if changed:
        await session.commit()
        await session.refresh(item, ["photos"])
    return changed


async def clear_photos(session: AsyncSession, item: Item) -> None:
    for photo in list(item.photos):
        await session.delete(photo)
    item.photos.clear()
    await session.commit()



def _clear_step(item: Item, step: str) -> None:
    match step:
        case "device_type":
            item.device_type = None
        case "brand":
            item.brand = None
        case "model":
            item.model = None
            item.generation = None
        case "year":
            item.year = None
        case "storage":
            item.storage_gb = None
        case "variant":
            item.variant = None
        case "color":
            item.color = None
        case "category":
            item.category = None
        case "battery":
            item.battery_health = None
        case "kit":
            item.kit_json = None
        case "grade":
            item.grade = None
        case "price":
            item.price = None
        case "note":
            item.note = None
        case _:
            raise ValueError(step)
    _unskip(item, step)


def _clear_device(item: Item) -> None:
    """Forget the device and everything picked below it; the manufacturer stays."""
    for step in _DEVICE_STEPS:
        _clear_step(item, step)
    item.category = None


def _clear_branch(item: Item) -> None:
    """Back to the top of the matryoshka: manufacturer and everything below it."""
    _clear_device(item)
    _clear_step(item, "brand")


def _apply_apple_model(item: Item, spec: catalog.ModelSpec) -> None:
    item.model = spec.name
    item.generation = spec.generation
    category = catalog.get_category(spec.category)
    item.category = spec.category
    item.device_type = category.device_type if category else None
    item.brand = "Apple"
    item.year = None
    # dependent choices become invalid; single-choice options are implied
    for step in _MODEL_OPTION_STEPS:
        _clear_step(item, step)
    if category and category.has_storage and len(spec.storages) == 1:
        item.storage_gb = spec.storages[0]
    if len(spec.variants) == 1:
        item.variant = spec.variants[0]
    if len(spec.colors) == 1:
        item.color = spec.colors[0]


def _fix_battery_skip(item: Item) -> None:
    # battery skip is only valid where the category allows it
    if not battery_skippable(item):
        _unskip(item, "battery")


def _set_brand(item: Item, value: str) -> None:
    brand = catalog.normalize_brand(value)
    if not brand:
        raise ValueError("empty brand")
    # An Apple menu device can't belong to another manufacturer. A manual
    # («Другое») draft keeps its device and typed fields: that is a legacy draft
    # (device type was asked before the manufacturer) resuming at the brand.
    if is_apple_catalog(item) and brand != catalog.APPLE:
        _clear_device(item)
    item.brand = brand
    _fix_battery_skip(item)


def _set_device(item: Item, code: str) -> None:
    """Apple: `code` is a category code or "other" (Apple «Другое», device type
    asked next). Other brands: `code` is a device type, the lot goes to the
    manual branch with that type."""
    if not item.brand:
        raise ValueError("manufacturer first")
    if is_apple(item):
        if code != OTHER_CATEGORY and catalog.get_category(code) is None:
            raise ValueError(f"unknown Apple device {code!r}")
        category, device_type = code, (catalog.get_category(code).device_type if code != OTHER_CATEGORY else None)
    else:
        if catalog.device_type_label(code) is None:
            raise ValueError(f"unknown device type {code!r}")
        category, device_type = OTHER_CATEGORY, code
    if category != item.category or (device_type is not None and device_type != item.device_type):
        _clear_device(item)
    item.category = category
    if device_type is not None:
        item.device_type = device_type
    _fix_battery_skip(item)


async def set_field(session: AsyncSession, item: Item, step: str, value) -> None:
    match step:
        case "brand":
            _set_brand(item, value)
        case "device":
            _set_device(item, value)
        case "model":
            spec = None if is_other(item) else catalog.get_apple_model(value or "")
            if spec is not None:
                _apply_apple_model(item, spec)
            else:
                item.model = value
                item.generation = None
        case "device_type":
            if catalog.device_type_label(value) is None:
                raise ValueError(f"unknown device type {value!r}")
            item.device_type = value
        case "year":
            item.year = int(value)
        case "storage":
            item.storage_gb = int(value)
        case "variant":
            item.variant = value
        case "color":
            item.color = value
        case "battery":
            item.battery_health = int(value)
        case "kit":
            item.kit_json = json.dumps(list(value))
        case "grade":
            item.grade = value
        case "price":
            item.price = int(value)
        case "note":
            item.note = value if value is not None else ""
        case _:
            raise ValueError(step)
    _unskip(item, step)
    await session.commit()


async def skip_field(session: AsyncSession, item: Item, step: str) -> None:
    """Record an explicit skip: the field stays empty and the step counts as done."""
    if step not in skippable_steps(item):
        raise ValueError(f"step {step!r} can't be skipped here")
    if step == "note":
        await set_field(session, item, "note", None)
        return
    _clear_step(item, step)
    _write_skipped(item, skipped_steps(item) | {step})
    await session.commit()


async def reset_field(session: AsyncSession, item: Item, step: str) -> None:
    """Used by 'edit field': empty the field (and everything that depends on it)
    so the wizard asks for it again."""
    match step:
        case "media" | "photos":
            await clear_photos(session, item)
            return
        case "brand":
            _clear_branch(item)
        case "model" if is_apple(item):
            # Apple: pick the device again (the model list depends on it)
            _clear_device(item)
        case _:
            _clear_step(item, step)
    await session.commit()


async def step_back(session: AsyncSession, item: Item, level: str) -> None:
    """«← Назад» in the matryoshka: forget the level above `level` so the
    wizard asks it again. `level` is the step being shown when the button was
    pressed. The series lives in FSM data and is cleared by the wizard."""
    match level:
        case "device":
            _clear_branch(item)
        case "series":
            _clear_device(item)
        case "model" if is_apple_catalog(item):
            # with a series level the wizard only drops the FSM series
            if not catalog.needs_series(item.category):
                _clear_device(item)
        case "model" if is_apple(item):  # Apple «Другое»: back to the device type
            _clear_step(item, "device_type")
            _clear_step(item, "model")
        case "model" | "device_type":
            _clear_device(item)
        case "brand":
            pass
        case _:
            raise ValueError(level)
    await session.commit()


def kit_codes(item: Item) -> list[str]:
    if not item.kit_json:
        return []
    try:
        raw = json.loads(item.kit_json)
    except json.JSONDecodeError:
        return []
    return [c for c in catalog.kit_codes() if c in raw]
