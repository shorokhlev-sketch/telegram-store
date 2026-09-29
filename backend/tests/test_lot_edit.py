"""Unit tests for services/lot_edit: wizard-equivalent validation of admin edits."""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.db.models import Item
from app.services import catalog, lot_edit
from app.services.lot_edit import LotEditError, apply_edit, plan_edit


def _iphone(**overrides) -> Item:
    fields = dict(
        public_id="EDIT0001",
        status="active",
        created_by=111,
        category="iphone",
        device_type="phone",
        brand="Apple",
        model="iPhone 15 Pro",
        generation="15",
        storage_gb=256,
        color="Black Titanium",
        battery_health=92,
        grade="A",
        kit_json=json.dumps(["box"]),
        price=75_000,
        note="",
    )
    fields.update(overrides)
    return Item(**fields)


def _other(**overrides) -> Item:
    fields = dict(
        public_id="EDIT0002",
        status="active",
        created_by=111,
        category="other",
        device_type="phone",
        brand="Samsung",
        model="Galaxy S23",
        year=2023,
        storage_gb=256,
        color="черный",
        battery_health=None,
        grade="B",
        kit_json="[]",
        price=40_000,
        note="",
        skipped_json=json.dumps(["battery"]),
    )
    fields.update(overrides)
    return Item(**fields)


def _err(item: Item, patch: dict) -> LotEditError:
    with pytest.raises(LotEditError) as exc:
        plan_edit(item, patch)
    return exc.value


# ---------------------------------------------------------------------------
# Simple fields
# ---------------------------------------------------------------------------


def test_price_note_grade_kit_battery():
    item = _iphone()
    changed = apply_edit(
        item,
        {"price": 69_000, "note": "  Торг  ", "grade": "b", "kit": ["receipt", "box", "box"], "battery_health": 88},
    )
    assert item.price == 69_000
    assert item.note == "Торг"
    assert item.grade == "B"
    assert json.loads(item.kit_json) == ["box", "receipt"]  # catalog order, deduped
    assert item.battery_health == 88
    assert set(changed) == {"price", "note", "grade", "kit_json", "battery_health"}


def test_empty_patch_changes_nothing():
    item = _iphone()
    assert apply_edit(item, {}) == []


def test_kit_can_be_emptied():
    item = _iphone()
    apply_edit(item, {"kit": []})
    assert item.kit_json == "[]"


@pytest.mark.parametrize(
    "patch, field",
    [
        ({"price": 0}, "price"),
        ({"price": -5}, "price"),
        ({"price": "69000"}, "price"),
        ({"price": 100_000_001}, "price"),
        ({"price": True}, "price"),
        ({"note": "x" * 501}, "note"),
        ({"grade": "D"}, "grade"),
        ({"grade": None}, "grade"),
        ({"kit": ["charger"]}, "kit"),
        ({"kit": "box"}, "kit"),
        ({"battery_health": 101}, "battery_health"),
        ({"battery_health": -1}, "battery_health"),
        ({"battery_health": 9.5}, "battery_health"),
        ({"public_id": "X"}, "public_id"),
        ({"status": "sold"}, "status"),
    ],
)
def test_invalid_common_fields(patch, field):
    err = _err(_iphone(), patch)
    assert err.field == field
    assert err.message  # Russian text for the form


def test_battery_not_skippable_for_iphone():
    err = _err(_iphone(), {"battery_health": None})
    assert err.field == "battery_health"
    assert err.message == "Укажите ёмкость аккумулятора"


def test_battery_skippable_for_airpods_marks_skip():
    item = _iphone(category="airpods", device_type="headphones", model="AirPods Pro 3", storage_gb=None, color="White")
    apply_edit(item, {"battery_health": None})
    assert item.battery_health is None
    assert json.loads(item.skipped_json) == ["battery"]


# ---------------------------------------------------------------------------
# Apple branch
# ---------------------------------------------------------------------------


def test_storage_and_color_must_belong_to_model():
    assert _err(_iphone(), {"storage_gb": 64}).field == "storage_gb"
    assert _err(_iphone(), {"color": "Pink"}).field == "color"
    item = _iphone()
    apply_edit(item, {"storage_gb": 1024, "color": "Blue Titanium"})
    assert (item.storage_gb, item.color) == (1024, "Blue Titanium")


def test_model_must_exist_in_category():
    err = _err(_iphone(), {"model": "Galaxy S23"})
    assert err.field == "model"
    assert err.message == "Нет такой модели в категории iPhone"
    # an iPad name is not an iPhone model
    assert _err(_iphone(), {"model": "iPad (A16)"}).field == "model"


def test_model_change_resets_options_unless_provided():
    err = _err(_iphone(), {"model": "iPhone 17"})
    assert err.field == "storage_gb"  # 256/512: must be chosen again
    item = _iphone()
    apply_edit(item, {"model": "iPhone 17", "storage_gb": 512, "color": "Sage"})
    assert (item.model, item.generation, item.storage_gb, item.color) == ("iPhone 17", "17", 512, "Sage")


def test_model_change_keeps_nothing_stale():
    # Black Titanium exists for 15 Pro, not for iPhone 17: color must be re-sent.
    err = _err(_iphone(), {"model": "iPhone 17", "storage_gb": 256})
    assert err.field == "color"


def test_single_option_values_are_auto_filled():
    item = _iphone(category="watch", device_type="watch", model="Apple Watch Series 10", storage_gb=None, variant="42 мм", color="Silver")
    apply_edit(item, {"model": "Apple Watch Ultra"})  # 49 мм only, Natural Titanium only
    assert (item.variant, item.color, item.generation) == ("49 мм", "Natural Titanium", "2022")


def test_watch_has_no_storage():
    item = _iphone(category="watch", device_type="watch", model="Apple Watch Series 10", storage_gb=None, variant="42 мм", color="Silver")
    assert _err(item, {"storage_gb": 64}).field == "storage_gb"
    assert _err(item, {"variant": "45 мм"}).field == "variant"
    apply_edit(item, {"variant": "46 мм"})
    assert item.variant == "46 мм"


def test_variant_prompt_for_macbook():
    item = _iphone(category="macbook", device_type="laptop", model="MacBook Air 13 (M2)", storage_gb=256, variant="8 ГБ", color="Midnight")
    err = _err(item, {"model": "MacBook Air 13 (M4)", "storage_gb": 512, "color": "Sky Blue"})
    assert (err.field, err.message) == ("variant", "Выберите оперативную память")


def test_category_change_resets_branch():
    err = _err(_iphone(), {"category": "ipad"})
    assert (err.field, err.message) == ("model", "Выберите модель")
    item = _iphone()
    apply_edit(item, {"category": "ipad", "model": "iPad (A16)", "storage_gb": 128, "color": "Blue"})
    assert (item.category, item.device_type, item.brand, item.model, item.generation) == ("ipad", "tablet", "Apple", "iPad (A16)", "2025")
    assert item.year is None


def test_category_change_to_non_skippable_needs_battery():
    item = _iphone(category="airpods", device_type="headphones", model="AirPods Pro 3", storage_gb=None, color="White", battery_health=None)
    err = _err(item, {"category": "iphone", "model": "iPhone 16", "storage_gb": 128, "color": "Pink"})
    assert err.field == "battery_health"
    apply_edit(item, {"category": "iphone", "model": "iPhone 16", "storage_gb": 128, "color": "Pink", "battery_health": 90})
    assert item.skipped_json is None


def test_apple_rejects_manual_fields():
    assert _err(_iphone(), {"year": 2023}).field == "year"
    assert _err(_iphone(), {"device_type": "laptop"}).field == "device_type"
    assert _err(_iphone(), {"brand": "Samsung"}).field == "brand"
    apply_edit(_iphone(), {"brand": "apple", "device_type": "phone", "year": None})


def test_unknown_category():
    assert _err(_iphone(), {"category": "vacuum"}).field == "category"


def test_legacy_lot_without_category_is_inferred():
    item = _iphone(category=None, device_type=None, brand=None)
    assert lot_edit.effective_category(item) == "iphone"
    apply_edit(item, {"price": 70_000})
    assert (item.category, item.device_type, item.brand) == ("iphone", "phone", "Apple")


def test_legacy_model_outside_catalog_keeps_options():
    item = _iphone(model="iPhone X", storage_gb=64, color="Silver")
    apply_edit(item, {"price": 20_000})
    assert (item.storage_gb, item.color) == (64, "Silver")
    assert _err(item, {"storage_gb": 256}).field == "model"


# ---------------------------------------------------------------------------
# "Другое"
# ---------------------------------------------------------------------------


def test_other_text_fields_are_normalized():
    item = _other()
    apply_edit(item, {"brand": "  samsung electronics ", "model": " Galaxy   S24 ", "color": "  ", "storage_gb": None})
    assert (item.brand, item.model, item.color, item.storage_gb) == ("Samsung", "Galaxy S24", None, None)
    assert json.loads(item.skipped_json) == ["storage", "color", "battery"]


def test_other_validation():
    item = _other()
    assert _err(item, {"device_type": "fridge"}).field == "device_type"
    assert _err(item, {"brand": ""}).message == "Укажите производителя"
    assert _err(item, {"model": "   "}).message == "Укажите модель"
    assert _err(item, {"model": "x" * 65}).field == "model"
    assert _err(item, {"year": 1999}).field == "year"
    assert _err(item, {"year": date.today().year + 1}).field == "year"
    assert _err(item, {"year": "2023"}).field == "year"
    assert _err(item, {"storage_gb": 100}).field == "storage_gb"
    assert _err(item, {"variant": "16 ГБ"}).field == "variant"


def test_switch_apple_to_other_requires_manual_fields():
    item = _iphone()
    err = _err(item, {"category": "other"})
    assert err.field == "device_type"
    err = _err(item, {"category": "other", "device_type": "phone", "brand": "Xiaomi", "model": "13T"})
    assert (err.field, err.message) == ("year", "Укажите год выпуска")
    apply_edit(item, {"category": "other", "device_type": "phone", "brand": "сяоми", "model": "13T", "year": 2023})
    assert (item.category, item.brand, item.model, item.year) == ("other", "Xiaomi", "13T", 2023)
    assert item.generation is None and item.storage_gb is None and item.color is None


def test_switch_other_to_apple_clears_manual_fields():
    item = _other(battery_health=90)
    apply_edit(item, {"category": "iphone", "model": "iPhone 16e", "storage_gb": 128, "color": "White"})
    assert (item.brand, item.device_type, item.year) == ("Apple", "phone", None)


def test_catalog_payload_shape():
    payload = lot_edit.catalog_payload()
    codes = [c["code"] for c in payload["categories"]]
    assert codes == ["iphone", "ipad", "macbook", "watch", "airpods", "other"]
    watch = next(m for m in payload["models"]["watch"] if m["name"] == "Apple Watch Ultra")
    assert watch == {"name": "Apple Watch Ultra", "storages": [], "variants": ["49 мм"], "colors": ["Natural Titanium"]}
    assert len(payload["models"]["iphone"]) == len(catalog.MODELS)
    assert [d["code"] for d in payload["device_types"]][0] == "phone"
    assert [k["code"] for k in payload["kit"]] == catalog.kit_codes()
    assert [g["code"] for g in payload["grades"]] == ["A", "B", "C"]
    assert payload["limits"]["year_max"] == date.today().year
