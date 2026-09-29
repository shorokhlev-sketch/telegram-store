"""Deterministic tag slugs derived from item fields.

Slug format is `kind:value`. No free-form tags exist; `build_tags` is the single
source of truth and is re-run on publish and on every item edit.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ItemTag, Tag, TagKind
from app.services import catalog

# Display order of tag groups (filter chips, /api/tags).
KIND_ORDER: tuple[str, ...] = (
    TagKind.device.value,
    TagKind.brand.value,
    TagKind.model.value,
    TagKind.storage.value,
    TagKind.color.value,
    TagKind.grade.value,
    TagKind.battery_band.value,
    TagKind.kit.value,
    TagKind.price_band.value,
)

# slug prefix -> tag kind. The slug prefix is the public, stable name.
PREFIX_TO_KIND: dict[str, str] = {
    "device": TagKind.device.value,
    "brand": TagKind.brand.value,
    "model": TagKind.model.value,
    "storage": TagKind.storage.value,
    "color": TagKind.color.value,
    "grade": TagKind.grade.value,
    "battery": TagKind.battery_band.value,
    "price": TagKind.price_band.value,
    "kit": TagKind.kit.value,
}
KIND_TO_PREFIX: dict[str, str] = {v: k for k, v in PREFIX_TO_KIND.items()}

BATTERY_BANDS: tuple[tuple[str, str], ...] = (
    ("100", "100%"),
    ("90plus", "90%+"),
    ("85plus", "85%+"),
    ("below85", "ниже 85%"),
)

PRICE_BANDS: tuple[tuple[int, int | None, str, str], ...] = (
    (0, 30_000, "0-30k", "до 30 тыс."),
    (30_000, 60_000, "30-60k", "30–60 тыс."),
    (60_000, 100_000, "60-100k", "60–100 тыс."),
    (100_000, None, "100k-plus", "от 100 тыс."),
)


class ItemLike(Protocol):
    device_type: str | None
    brand: str | None
    model: str | None
    storage_gb: int | None
    color: str | None
    battery_health: int | None
    grade: str | None
    kit_json: str | None
    price: int | None


@dataclass(frozen=True)
class TagDef:
    slug: str
    label: str
    kind: str


# Manual entry ("Другое") is typed in Russian: transliterate instead of dropping
# Cyrillic, otherwise "чёрный" would slugify to "" and break the tag.
_CYRILLIC = dict(
    zip(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюяіїєґ",
        ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
         "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya",
         "i", "yi", "ye", "g"],
        strict=True,
    )
)
_CYRILLIC_TABLE = str.maketrans(_CYRILLIC)


def slugify(value: str) -> str:
    value = value.lower().translate(_CYRILLIC_TABLE)
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-z0-9]+", "-", value.lower())
    return value.strip("-")


def battery_band(health: int) -> str:
    if health >= 100:
        return "100"
    if health >= 90:
        return "90plus"
    if health >= 85:
        return "85plus"
    return "below85"


def price_band(price: int) -> str:
    for lo, hi, code, _ in PRICE_BANDS:
        if price >= lo and (hi is None or price < hi):
            return code
    return PRICE_BANDS[-1][2]


def parse_kit(kit_json: str | None) -> list[str]:
    if not kit_json:
        return []
    try:
        raw = json.loads(kit_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    known = set(catalog.kit_codes())
    return [code for code in catalog.kit_codes() if code in raw and code in known]


def canonical_brand(item: ItemLike) -> str:
    """Normalized manufacturer, "" when unknown (legacy v1 lots have no brand)."""
    return catalog.normalize_brand(getattr(item, "brand", None) or "")


def is_apple(item: ItemLike) -> bool:
    """Apple or brandless legacy lot: the model name alone identifies the device."""
    brand = canonical_brand(item)
    return not brand or brand == "Apple"


def full_model_name(brand: str, model: str) -> str:
    """ "Samsung" + "Galaxy S23" -> "Samsung Galaxy S23"; the brand is not
    repeated when the model already starts with it."""
    model = " ".join(model.split())
    if not brand or model.casefold().startswith(brand.casefold()):
        return model
    return f"{brand} {model}"


def _add(defs: list[TagDef], prefix: str, value_slug: str, label: str, kind: str) -> None:
    if value_slug:  # an unsluggable value (emoji only, etc.) gets no tag rather than "kind:"
        defs.append(TagDef(f"{prefix}:{value_slug}", label, kind))


def model_tag_def(item: ItemLike) -> TagDef | None:
    """The `model:*` tag of a lot, None without a (sluggable) model. Non-Apple
    models carry the brand, so two brands' "Note 12" don't collide; the label is
    the full name ("Samsung Galaxy S24")."""
    model = (item.model or "").strip()
    if not model:
        return None
    name = model if is_apple(item) else full_model_name(canonical_brand(item), model)
    value_slug = slugify(name)
    return TagDef(f"model:{value_slug}", name, TagKind.model.value) if value_slug else None


def build_tag_defs(item: ItemLike) -> list[TagDef]:
    """Tags in KIND_ORDER. Missing fields produce no tag of that kind."""
    defs: list[TagDef] = []
    device_type = getattr(item, "device_type", None)
    if device_type:
        label = catalog.device_type_label(device_type) or device_type
        _add(defs, "device", slugify(device_type), label, TagKind.device.value)
    brand = canonical_brand(item)
    if brand:
        _add(defs, "brand", slugify(brand), brand, TagKind.brand.value)
    model_def = model_tag_def(item)
    if model_def:
        defs.append(model_def)
    if item.storage_gb:
        defs.append(
            TagDef(
                f"storage:{item.storage_gb}",
                catalog.format_storage(item.storage_gb),
                TagKind.storage.value,
            )
        )
    color = (item.color or "").strip()
    if color:
        _add(defs, "color", slugify(color), color, TagKind.color.value)
    if item.grade:
        grade = item.grade.upper()
        defs.append(TagDef(f"grade:{grade.lower()}", f"Состояние {grade}", TagKind.grade.value))
    if item.battery_health is not None:
        code = battery_band(item.battery_health)
        label = dict(BATTERY_BANDS)[code]
        defs.append(TagDef(f"battery:{code}", f"АКБ {label}", TagKind.battery_band.value))
    for kit_code in parse_kit(item.kit_json):
        kit_item = catalog.get_kit_item(kit_code)
        if kit_item:
            defs.append(TagDef(f"kit:{kit_code}", kit_item.label, TagKind.kit.value))
    if item.price is not None:
        code = price_band(item.price)
        label = next(lbl for _, _, c, lbl in PRICE_BANDS if c == code)
        defs.append(TagDef(f"price:{code}", label, TagKind.price_band.value))
    return defs


def build_tags(item: ItemLike) -> list[str]:
    return [d.slug for d in build_tag_defs(item)]


def kind_of(slug: str) -> str | None:
    prefix, _, rest = slug.partition(":")
    if not rest:
        return None
    return PREFIX_TO_KIND.get(prefix)


def group_by_kind(slugs: list[str]) -> dict[str, list[str]]:
    """Group slugs by kind, dropping malformed ones. Used by the API filter
    (AND across kinds, OR inside one kind)."""
    groups: dict[str, list[str]] = {}
    for slug in slugs:
        kind = kind_of(slug)
        if kind is None:
            continue
        groups.setdefault(kind, [])
        if slug not in groups[kind]:
            groups[kind].append(slug)
    return groups


async def sync_item_tags(session: AsyncSession, item) -> list[str]:
    """Recompute item tags and persist the item_tags links. Creates missing
    `tags` rows. Does not commit."""
    defs = build_tag_defs(item)
    slugs = [d.slug for d in defs]

    existing = {
        t.slug: t
        for t in (await session.scalars(select(Tag).where(Tag.slug.in_(slugs)))).all()
    } if slugs else {}
    for d in defs:
        if d.slug not in existing:
            tag = Tag(slug=d.slug, label=d.label, kind=d.kind)
            session.add(tag)
            existing[d.slug] = tag
    await session.flush()

    await session.execute(delete(ItemTag).where(ItemTag.item_id == item.id))
    for slug in slugs:
        session.add(ItemTag(item_id=item.id, tag_id=existing[slug].id))
    await session.flush()
    return slugs
