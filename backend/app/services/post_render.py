"""Channel post caption. Telegram caps media captions at 1024 characters."""

from __future__ import annotations

from html import escape

from app.db.models import Item
from app.services import catalog, tags
from app.services.drafts import kit_codes

CAPTION_LIMIT = 1024


def format_price(price: int) -> str:
    return f"{price:,} ₽".replace(",", " ")


def is_manual_entry(item: Item) -> bool:
    """Lot entered by hand ("Другое" branch) rather than picked from the Apple catalog.
    Legacy v1 lots (no category, no brand) are catalog iPhones."""
    if item.category == "other":
        return True
    return item.category is None and not tags.is_apple(item)


def _variant_text(item: Item) -> str:
    variant = (item.variant or "").strip()
    if item.category == "macbook" and variant and "ram" not in variant.casefold():
        return f"{variant} RAM"
    return variant


def item_title(item: Item) -> str:
    """Plain-text lot title (no HTML): caption headline and the text prefilled
    in the "message the seller" chat.

    Catalog:  "iPhone 15 Pro 256 ГБ, Black Titanium", "Apple Watch Series 10, 46 мм, Jet Black".
    Manual:   "Samsung Galaxy S23 (2023) 256 ГБ, Phantom Black".
    """
    color = (item.color or "").strip()
    storage = catalog.format_storage(item.storage_gb) if item.storage_gb else ""
    if is_manual_entry(item):
        title = tags.full_model_name(tags.canonical_brand(item), item.model or "") or "Устройство"
        if item.year:
            title = f"{title} ({item.year})"
        if storage:
            title = f"{title} {storage}"
    else:
        category = catalog.get_category(item.category or "")
        title = (item.model or "").strip() or (category.label if category else "iPhone")
        if storage:
            title = f"{title} {storage}"
        variant = _variant_text(item)
        if variant:
            title = f"{title}, {variant}"
    if color:
        title = f"{title}, {color}"
    return title


def render_caption(item: Item, limit: int = CAPTION_LIMIT) -> str:
    """`limit` leaves room for a prefix (e.g. the subscriber broadcast header)."""
    lines = [f"<b>{escape(item_title(item))}</b>", ""]

    if is_manual_entry(item) and item.device_type:
        device = catalog.device_type_label(item.device_type) or item.device_type
        lines.append(f"Устройство: {escape(device)}")

    if item.grade:
        spec = catalog.get_grade(item.grade)
        grade_text = f"{spec.label}. {spec.description}" if spec else item.grade
        lines.append(f"Состояние: {escape(grade_text)}")
    if item.battery_health is not None:
        lines.append(f"АКБ: {item.battery_health}%")
    codes = kit_codes(item)
    if codes:
        labels = [catalog.get_kit_item(c).label.lower() for c in codes if catalog.get_kit_item(c)]
        lines.append(f"Комплект: {escape(', '.join(labels))}")
    else:
        lines.append("Комплект: только устройство")
    if item.price is not None:
        lines.append(f"<b>Цена: {format_price(item.price)}</b>")

    footer = f"\nЛот {escape(item.public_id)}"
    body = "\n".join(lines)

    note = (item.note or "").strip()
    if note:
        budget = limit - len(body) - len(footer) - 3
        if budget > 0:
            if len(note) > budget:
                note = note[: max(budget - 1, 0)].rstrip() + "…"
            body = f"{body}\n\n{escape(note)}"

    return f"{body}\n{footer}"
