"""Editing a published lot from the Mini App admin.

`plan_edit(item, patch)` validates a partial update exactly like the bot wizard
validates input and returns the full set of column values to write; nothing is
touched on error, so a 422 never leaves a half-edited row behind.

Rules (mirrors `bot/wizard.py` + `services/drafts.py`):
- Apple categories: the model must exist in the catalog for that category;
  storage / variant / color must be options of that model. Options with a
  single choice are filled automatically. Device type and brand follow from
  the category; year is not asked.
- "Другое": device type from `catalog.DEVICE_TYPES`, brand normalized
  (`catalog.normalize_brand`), model free text, year validated
  (`catalog.validate_year`), storage optional (fixed list), color optional text.
- Changing the category clears the whole branch, changing the model clears its
  options; values sent in the same request are applied on top. The result must
  still be a complete lot (the lot is live in the channel), otherwise 422 on
  the first missing field.
- Battery may be empty only where the wizard lets it be skipped (AirPods,
  "Другое").
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from app.db.models import Item
from app.services import catalog, drafts, tags

EDITABLE_FIELDS: tuple[str, ...] = (
    "category",
    "device_type",
    "brand",
    "model",
    "year",
    "storage_gb",
    "variant",
    "color",
    "battery_health",
    "grade",
    "kit",
    "price",
    "note",
)

# Columns `plan_edit` returns (always all of them).
COLUMNS: tuple[str, ...] = (
    "category",
    "device_type",
    "brand",
    "model",
    "generation",
    "year",
    "storage_gb",
    "variant",
    "color",
    "battery_health",
    "grade",
    "kit_json",
    "price",
    "note",
    "skipped_json",
)

_BRANCH_COLUMNS: tuple[str, ...] = (
    "device_type",
    "brand",
    "model",
    "generation",
    "year",
    "storage_gb",
    "variant",
    "color",
)

OTHER = drafts.OTHER_CATEGORY
TEXT_MAX = drafts.TEXT_FIELD_MAX
NOTE_MAX = 500
PRICE_MAX = 100_000_000

# "Выберите …" for the category-specific option (variant).
_VARIANT_PROMPTS: dict[str, str] = {
    "Оперативная память": "Выберите оперативную память",
    "Размер корпуса": "Выберите размер корпуса",
}


class LotEditError(ValueError):
    """Invalid edit: `field` is the request field, `message` is shown to the admin."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def effective_category(item: Item) -> str:
    """Category of the lot; legacy rows without one are inferred from the model/brand."""
    if item.category:
        return item.category
    spec = catalog.get_apple_model(item.model or "")
    if spec is not None:
        return spec.category
    return "iphone" if tags.is_apple(item) else OTHER


def battery_skippable(category: str) -> bool:
    if category == OTHER:
        return True
    spec = catalog.get_category(category)
    return bool(spec and spec.battery_skippable)


def _clean_text(field: str, value: Any, *, required_message: str | None) -> str | None:
    """Collapse whitespace; empty -> None (or 422 when required); 64-char cap like the wizard."""
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = " ".join(value.split())
    else:
        raise LotEditError(field, "Нужен текст")
    if not text:
        if required_message:
            raise LotEditError(field, required_message)
        return None
    if len(text) > TEXT_MAX:
        raise LotEditError(field, f"Слишком длинно: максимум {TEXT_MAX} символа")
    return text


def _plan_apple(values: dict[str, Any], patch: dict[str, Any], category_code: str, reset_options: bool) -> None:
    category = catalog.get_category(category_code)
    assert category is not None

    if patch.get("year") is not None:
        raise LotEditError("year", "Год указывается только в категории «Другое»")
    if "device_type" in patch and patch["device_type"] != category.device_type:
        raise LotEditError("device_type", "Тип устройства задаётся категорией")
    if "brand" in patch and catalog.normalize_brand(patch["brand"] if isinstance(patch["brand"], str) else "") != "Apple":
        raise LotEditError("brand", "В этой категории производитель — Apple")
    values["device_type"] = category.device_type
    values["brand"] = "Apple"
    values["year"] = None

    if "model" in patch:
        name = patch["model"]
        spec = catalog.get_apple_model(name) if isinstance(name, str) else None
        if spec is None or spec.category != category_code:
            raise LotEditError("model", f"Нет такой модели в категории {category.label}")
        if name != values["model"]:
            reset_options = True
        values["model"] = name
    if not values["model"]:
        raise LotEditError("model", "Выберите модель")

    spec = catalog.get_apple_model(values["model"])
    if spec is None or spec.category != category_code:
        # Legacy lot whose model left the catalog: keep its options as they are.
        for field in ("storage_gb", "variant", "color"):
            if field in patch:
                raise LotEditError("model", "Модели нет в справочнике, выберите модель заново")
        return

    has_storage = category.has_storage and bool(spec.storages)
    if reset_options:
        values["generation"] = spec.generation
        values["storage_gb"] = spec.storages[0] if has_storage and len(spec.storages) == 1 else None
        values["variant"] = spec.variants[0] if len(spec.variants) == 1 else None
        values["color"] = spec.colors[0] if len(spec.colors) == 1 else None

    if "storage_gb" in patch:
        value = patch["storage_gb"]
        if not has_storage:
            if value is not None:
                raise LotEditError("storage_gb", f"У {spec.name} нет выбора памяти")
            values["storage_gb"] = None
        elif not _is_int(value) or value not in spec.storages:
            raise LotEditError("storage_gb", f"Нет такого объёма памяти для {spec.name}")
        else:
            values["storage_gb"] = value
    if "variant" in patch:
        value = patch["variant"]
        if not spec.variants:
            if value is not None:
                raise LotEditError("variant", f"У {spec.name} нет такого выбора")
            values["variant"] = None
        elif value not in spec.variants:
            raise LotEditError("variant", f"Нет такого варианта для {spec.name}")
        else:
            values["variant"] = value
    if "color" in patch:
        value = patch["color"]
        if value not in spec.colors:
            raise LotEditError("color", f"Нет такого цвета для {spec.name}")
        values["color"] = value

    if has_storage and values["storage_gb"] is None:
        raise LotEditError("storage_gb", "Выберите память")
    if spec.variants and values["variant"] is None:
        raise LotEditError("variant", _VARIANT_PROMPTS.get(category.variant_label or "", "Выберите вариант"))
    if spec.colors and values["color"] is None:
        raise LotEditError("color", "Выберите цвет")


def _plan_other(values: dict[str, Any], patch: dict[str, Any]) -> None:
    values["generation"] = None
    if patch.get("variant") not in (None, ""):
        raise LotEditError("variant", "В категории «Другое» этого поля нет")
    values["variant"] = None

    if "device_type" in patch:
        value = patch["device_type"]
        if not isinstance(value, str) or catalog.device_type_label(value) is None:
            raise LotEditError("device_type", "Нет такого типа устройства")
        values["device_type"] = value
    if "brand" in patch:
        text = _clean_text("brand", patch["brand"], required_message="Укажите производителя")
        values["brand"] = catalog.normalize_brand(text or "")
    if "model" in patch:
        values["model"] = _clean_text("model", patch["model"], required_message="Укажите модель")
    if "year" in patch:
        value = patch["year"]
        if not _is_int(value) or not catalog.validate_year(value):
            raise LotEditError("year", f"Нужен год от {catalog.MIN_YEAR} до {date.today().year}")
        values["year"] = value
    if "storage_gb" in patch:
        value = patch["storage_gb"]
        if value is not None and (not _is_int(value) or value not in drafts.OTHER_STORAGES):
            raise LotEditError("storage_gb", "Нет такого объёма памяти")
        values["storage_gb"] = value
    if "color" in patch:
        values["color"] = _clean_text("color", patch["color"], required_message=None)

    if not values["device_type"] or catalog.device_type_label(values["device_type"]) is None:
        raise LotEditError("device_type", "Выберите тип устройства")
    if not values["brand"]:
        raise LotEditError("brand", "Укажите производителя")
    if not values["model"]:
        raise LotEditError("model", "Укажите модель")
    if values["year"] is None:
        raise LotEditError("year", "Укажите год выпуска")


def _skipped_json(values: dict[str, Any]) -> str | None:
    """Explicit skip marks for the fields left empty (keeps the draft engine consistent)."""
    skipped: list[str] = []
    other = values["category"] == OTHER
    if other and values["storage_gb"] is None:
        skipped.append("storage")
    if other and values["color"] is None:
        skipped.append("color")
    if values["battery_health"] is None and battery_skippable(values["category"]):
        skipped.append("battery")
    ordered = [s for s in drafts.ALL_STEPS if s in skipped]
    return json.dumps(ordered) if ordered else None


def plan_edit(item: Item, patch: dict[str, Any]) -> dict[str, Any]:
    """Validate `patch` against `item` and return every column in `COLUMNS` with its new value."""
    for key in patch:
        if key not in EDITABLE_FIELDS:
            raise LotEditError(key, "Это поле нельзя изменить")

    values: dict[str, Any] = {column: getattr(item, column) for column in COLUMNS}
    current = effective_category(item)
    category = current
    if "category" in patch:
        value = patch["category"]
        if not isinstance(value, str) or (value != OTHER and catalog.get_category(value) is None):
            raise LotEditError("category", "Нет такой категории")
        category = value
    category_changed = category != current
    values["category"] = category
    if category_changed:
        for column in _BRANCH_COLUMNS:
            values[column] = None

    if category == OTHER:
        _plan_other(values, patch)
    else:
        _plan_apple(values, patch, category, reset_options=category_changed)

    if "battery_health" in patch:
        value = patch["battery_health"]
        if value is None:
            if not battery_skippable(category):
                raise LotEditError("battery_health", "Укажите ёмкость аккумулятора")
        elif not _is_int(value) or not 0 <= value <= 100:
            raise LotEditError("battery_health", "Нужно целое число от 0 до 100")
        values["battery_health"] = value
    if values["battery_health"] is None and not battery_skippable(category):
        raise LotEditError("battery_health", "Укажите ёмкость аккумулятора")

    if "grade" in patch:
        value = patch["grade"]
        code = value.strip().upper() if isinstance(value, str) else ""
        if code not in catalog.grade_codes():
            raise LotEditError("grade", "Состояние: A, B или C")
        values["grade"] = code
    if values["grade"] is None:
        raise LotEditError("grade", "Выберите состояние")

    if "kit" in patch:
        value = patch["kit"]
        if not isinstance(value, list) or any(not isinstance(c, str) or catalog.get_kit_item(c) is None for c in value):
            raise LotEditError("kit", "Нет такого пункта комплекта")
        values["kit_json"] = json.dumps([c for c in catalog.kit_codes() if c in value])

    if "price" in patch:
        value = patch["price"]
        if not _is_int(value) or value <= 0:
            raise LotEditError("price", "Нужно целое число больше нуля")
        if value > PRICE_MAX:
            raise LotEditError("price", "Слишком большая цена")
        values["price"] = value
    if values["price"] is None:
        raise LotEditError("price", "Укажите цену")

    if "note" in patch:
        value = patch["note"]
        if value is not None and not isinstance(value, str):
            raise LotEditError("note", "Нужен текст")
        text = (value or "").strip()
        if len(text) > NOTE_MAX:
            raise LotEditError("note", f"Слишком длинно: максимум {NOTE_MAX} символов")
        values["note"] = text

    values["skipped_json"] = _skipped_json(values)
    return values


def apply_edit(item: Item, patch: dict[str, Any]) -> list[str]:
    """Validate and write `patch`; returns the columns that actually changed. Does not commit."""
    values = plan_edit(item, patch)
    changed = [column for column in COLUMNS if getattr(item, column) != values[column]]
    for column in changed:
        setattr(item, column, values[column])
    return changed


def kit_list(item: Item) -> list[str]:
    return drafts.kit_codes(item)


def catalog_payload() -> dict[str, Any]:
    """Reference data for the admin edit form (`GET /api/admin/catalog`)."""
    categories = [
        {
            "code": c.code,
            "label": c.label,
            "device_type": c.device_type,
            "variant_label": c.variant_label,
            "has_storage": c.has_storage,
            "battery_skippable": c.battery_skippable,
            "manual": False,
        }
        for c in catalog.categories()
    ]
    categories.append(
        {
            "code": OTHER,
            "label": drafts.OTHER_CATEGORY_LABEL,
            "device_type": None,
            "variant_label": None,
            "has_storage": True,
            "battery_skippable": True,
            "manual": True,
        }
    )
    models = {
        c.code: [
            {
                "name": m.name,
                "storages": list(m.storages) if c.has_storage else [],
                "variants": list(m.variants),
                "colors": list(m.colors),
            }
            for m in catalog.models_for(c.code)
        ]
        for c in catalog.categories()
    }
    return {
        "categories": categories,
        "models": models,
        "device_types": [{"code": code, "label": label} for code, label in catalog.DEVICE_TYPES],
        "kit": [{"code": k.code, "label": k.label} for k in catalog.KIT_ITEMS],
        "grades": [{"code": g.code, "label": g.label, "description": g.description} for g in catalog.GRADES],
        "other_storages": list(drafts.OTHER_STORAGES),
        "limits": {
            "text_max": TEXT_MAX,
            "note_max": NOTE_MAX,
            "price_max": PRICE_MAX,
            "year_min": catalog.MIN_YEAR,
            "year_max": date.today().year,
        },
    }
