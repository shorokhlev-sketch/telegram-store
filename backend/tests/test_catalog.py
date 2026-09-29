import re
from datetime import date

import pytest

from app.services import catalog


def test_models_unique_and_have_options():
    names = catalog.model_names()
    assert len(names) == len(set(names))
    for name in names:
        assert catalog.storages_for(name), name
        assert catalog.colors_for(name), name
        assert catalog.get_model(name).generation


def test_unknown_model_returns_empty():
    assert catalog.get_model("iPhone 99") is None
    assert catalog.storages_for("iPhone 99") == ()
    assert catalog.colors_for("iPhone 99") == ()


def test_kit_and_grades():
    assert catalog.kit_codes() == ["box", "cable", "adapter", "receipt"]
    assert catalog.get_kit_item("box").label == "Коробка"
    assert catalog.grade_codes() == ["A", "B", "C"]
    assert catalog.get_grade("B").description


def test_format_storage():
    assert catalog.format_storage(256) == "256 ГБ"
    assert catalog.format_storage(1024) == "1 ТБ"
    assert catalog.format_storage(2048) == "2 ТБ"


# --- stage 6a: multi-category catalog -------------------------------------------------

# Model callbacks are category-scoped ("iphone.<idx>"), so this list may grow;
# the test only pins the order (newest first) and 2026 additions from apple.com.
IPHONE_NAMES = [
    "iPhone Duo", "iPhone 18 Pro Max", "iPhone 18 Pro",
    "iPhone 17 Pro Max", "iPhone 17 Pro", "iPhone Air", "iPhone 17", "iPhone 17e",
    "iPhone 16 Pro Max", "iPhone 16 Pro", "iPhone 16 Plus", "iPhone 16", "iPhone 16e",
    "iPhone 15 Pro Max", "iPhone 15 Pro", "iPhone 15 Plus", "iPhone 15",
    "iPhone 14 Pro Max", "iPhone 14 Pro", "iPhone 14 Plus", "iPhone 14",
    "iPhone 13 Pro Max", "iPhone 13 Pro", "iPhone 13", "iPhone 13 mini",
    "iPhone 12 Pro Max", "iPhone 12 Pro", "iPhone 12", "iPhone 12 mini",
    "iPhone 11 Pro Max", "iPhone 11 Pro", "iPhone 11",
    "iPhone SE (2022)",
]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def test_model_names_is_iphone_only_newest_first():
    assert catalog.model_names() == IPHONE_NAMES
    assert [m.name for m in catalog.MODELS] == IPHONE_NAMES
    assert all(m.category == "iphone" for m in catalog.MODELS)
    assert catalog.APPLE_MODELS[: len(catalog.MODELS)] == catalog.MODELS


def test_2026_iphones_match_apple_specs():
    # apple.com/iphone-18-pro/specs, /iphone-duo/specs, /iphone-17e/specs (checked 28.09.2026)
    for name in ("iPhone 18 Pro", "iPhone 18 Pro Max"):
        assert catalog.storages_for(name) == (256, 512, 1024, 2048)
        assert catalog.colors_for(name) == ("Black", "Silver", "Glacier", "Burgundy")
    assert catalog.storages_for("iPhone Duo") == (256, 512, 1024, 2048)
    assert catalog.colors_for("iPhone Duo") == ("Night Sky", "Star White")
    assert catalog.storages_for("iPhone 17e") == (256, 512)
    assert catalog.colors_for("iPhone 17e") == ("Black", "White", "Soft Pink")


def test_old_positional_constructor_still_works():
    spec = catalog.ModelSpec("X", "1", (64,), ("Black",))
    assert spec.category == "iphone"
    assert spec.variants == ()


def test_categories_order_and_flags():
    codes = [c.code for c in catalog.categories()]
    assert codes == ["iphone", "ipad", "macbook", "watch", "airpods"]
    device_codes = {code for code, _ in catalog.DEVICE_TYPES}
    for cat in catalog.CATEGORIES:
        assert cat.device_type in device_codes
        assert catalog.models_for(cat.code), cat.code
    assert catalog.get_category("watch").variant_label == "Размер корпуса"
    assert catalog.get_category("watch").has_storage is False
    assert catalog.get_category("macbook").variant_label == "Оперативная память"
    assert catalog.get_category("macbook").has_storage is True
    assert catalog.get_category("airpods").battery_skippable is True
    assert catalog.get_category("airpods").has_storage is False
    assert catalog.get_category("iphone").battery_skippable is False
    assert catalog.get_category("ipad").variant_label is None
    assert catalog.get_category("nope") is None
    assert catalog.models_for("nope") == []


def test_device_types():
    assert [c for c, _ in catalog.DEVICE_TYPES] == ["phone", "tablet", "laptop", "watch", "headphones", "other"]
    assert catalog.device_type_label("laptop") == "Ноутбук"
    assert catalog.device_type_label("other") == "Другое"
    assert catalog.device_type_label("tv") is None


def test_apple_model_names_and_slugs_unique():
    names = [m.name for m in catalog.APPLE_MODELS]
    assert len(names) == len(set(names))
    slugs = [_slug(n) for n in names]
    assert len(slugs) == len(set(slugs))
    assert all(n.isascii() for n in names)


def test_every_model_is_well_formed():
    category_codes = {c.code for c in catalog.CATEGORIES}
    for m in catalog.APPLE_MODELS:
        assert m.category in category_codes, m.name
        assert m.generation and len(m.generation) <= 16, m.name  # items.generation is String(16)
        assert m.colors, m.name
        assert len({_slug(c) for c in m.colors}) == len(m.colors), m.name
        assert list(m.storages) == sorted(m.storages), m.name
        assert len(set(m.storages)) == len(m.storages), m.name
        assert all(s > 0 for s in m.storages), m.name
        assert len(set(m.variants)) == len(m.variants), m.name
        cat = catalog.get_category(m.category)
        assert bool(m.storages) == cat.has_storage, m.name
        assert bool(m.variants) == (cat.variant_label is not None), m.name


def test_category_specific_rules():
    for m in catalog.models_for("watch") + catalog.models_for("airpods"):
        assert m.storages == (), m.name
    for m in catalog.models_for("watch"):
        assert m.variants and all(v.endswith(" мм") for v in m.variants), m.name
    for m in catalog.models_for("macbook"):
        assert m.storages and m.variants, m.name
        assert all(v.endswith(" ГБ") for v in m.variants), m.name
        gbs = [int(v.split()[0]) for v in m.variants]
        assert gbs == sorted(gbs), m.name
    for m in catalog.models_for("airpods"):
        assert m.variants == (), m.name
        if not m.name.startswith("AirPods Max"):
            assert m.colors == ("White",), m.name
    for code in ("ipad", "macbook", "watch", "airpods"):
        assert len(catalog.models_for(code)) <= 25, code


def test_non_iphone_generation_is_year_and_newest_first():
    for code in ("ipad", "macbook", "watch", "airpods"):
        years = [int(m.generation) for m in catalog.models_for(code)]
        assert years == sorted(years, reverse=True), code
        assert all(2019 <= y <= date.today().year for y in years), code


def test_lookup_helpers_work_across_categories():
    assert catalog.get_apple_model("iPad Air 11 (M2)").category == "ipad"
    assert catalog.get_model("MacBook Air 13 (M3)") is catalog.get_apple_model("MacBook Air 13 (M3)")
    assert catalog.storages_for("MacBook Air 13 (M3)") == (256, 512, 1024, 2048)
    assert "16 ГБ" in catalog.variants_for("MacBook Air 13 (M3)")
    assert catalog.variants_for("Apple Watch Series 10") == ("42 мм", "46 мм")
    assert catalog.storages_for("Apple Watch Series 10") == ()
    assert catalog.colors_for("AirPods Pro 2 (USB-C)") == ("White",)
    assert catalog.variants_for("iPhone 15 Pro") == ()
    assert catalog.variants_for("iPhone 99") == ()
    assert catalog.get_apple_model("iPhone 99") is None
    assert catalog.get_model("iPhone 15 Pro").category == "iphone"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("samsung", "Samsung"),
        ("  SAMSUNG  ", "Samsung"),
        ("Samsung Electronics", "Samsung"),
        ("samsung electronics co., ltd.", "Samsung"),
        ("самсунг", "Samsung"),
        ("Самсунг", "Samsung"),
        ("сяоми", "Xiaomi"),
        ("Xiaomi Inc", "Xiaomi"),
        ("xiaomi inc.", "Xiaomi"),
        ("poco", "POCO"),
        ("oneplus", "OnePlus"),
        ("One   Plus", "OnePlus"),
        ("hp", "HP"),
        ("Hewlett-Packard", "HP"),
        ("msi", "MSI"),
        ("jbl", "JBL"),
        ("realme", "Realme"),
        ("apple", "Apple"),
        ("Apple Inc.", "Apple"),
        ("nothing", "Nothing"),
        ("marshall", "Marshall"),
        # unknown brands
        ("doogee", "Doogee"),
        ("DOOGEE", "Doogee"),
        ("  blackview   pro ", "Blackview Pro"),
        ("TCL mobile", "TCL Mobile"),
        ("BQ", "BQ"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalize_brand(raw, expected):
    assert catalog.normalize_brand(raw) == expected


def test_validate_year_boundaries():
    this_year = date.today().year
    assert catalog.validate_year(2000)
    assert catalog.validate_year(this_year)
    assert not catalog.validate_year(1999)
    assert not catalog.validate_year(this_year + 1)
    assert not catalog.validate_year(True)


# --- wizard matryoshka: manufacturer -> device -> series -> model -----------------------


def test_wizard_brands_are_canonical():
    assert catalog.WIZARD_BRANDS == (
        "Apple", "Samsung", "Xiaomi", "Huawei", "Honor", "Google", "OnePlus", "Nothing", "Sony", "Lenovo",
    )
    for brand in catalog.WIZARD_BRANDS:
        assert catalog.normalize_brand(brand) == brand
    assert [code for code, _ in catalog.APPLE_DEVICES] == [c.code for c in catalog.CATEGORIES]


def test_series_labels_and_order():
    assert catalog.series_for("iphone") == [
        ("18", "iPhone 18"), ("17", "iPhone 17"), ("16", "iPhone 16"), ("15", "iPhone 15"),
        ("14", "iPhone 14"), ("13", "iPhone 13"), ("12", "iPhone 12"), ("11", "iPhone 11"),
        ("se", "iPhone SE"),
    ]
    assert catalog.series_for("ipad") == [("ipad", "iPad"), ("air", "iPad Air"), ("mini", "iPad mini"), ("pro", "iPad Pro")]
    assert catalog.series_for("macbook") == [("air", "MacBook Air"), ("pro", "MacBook Pro"), ("neo", "MacBook Neo")]
    assert catalog.series_for("watch") == [("series", "Series"), ("ultra", "Ultra"), ("se", "SE")]
    assert catalog.series_for("airpods") == []
    assert catalog.series_for("nope") == []


def test_series_membership():
    # iPhone Air / Duo / 17e sit with their generation
    assert [m.name for m in catalog.models_in_series("iphone", "17")] == [
        "iPhone 17 Pro Max", "iPhone 17 Pro", "iPhone Air", "iPhone 17", "iPhone 17e"
    ]
    assert [m.name for m in catalog.models_in_series("iphone", "18")] == [
        "iPhone Duo", "iPhone 18 Pro Max", "iPhone 18 Pro"
    ]
    assert [m.name for m in catalog.models_in_series("iphone", "se")] == ["iPhone SE (2022)"]
    assert [m.name for m in catalog.models_in_series("ipad", "mini")] == ["iPad mini (A17 Pro)", "iPad mini (2021)"]
    assert [m.name for m in catalog.models_in_series("ipad", "ipad")] == [
        "iPad (A16)", "iPad (2022)", "iPad (2021)", "iPad (2020)"
    ]
    assert [m.name for m in catalog.models_in_series("macbook", "neo")] == ["MacBook Neo"]
    assert all(m.name.startswith("Apple Watch Ultra") for m in catalog.models_in_series("watch", "ultra"))
    assert catalog.models_in_series("iphone", "99") == []


def test_every_apple_model_belongs_to_exactly_one_series():
    for category in catalog.CATEGORIES:
        series = catalog.series_for(category.code)
        if not series:
            continue
        assert 2 <= len(series) <= 10, category.code  # the series keyboard stays small
        keys = [k for k, _ in series]
        labels = [label for _, label in series]
        assert len(set(keys)) == len(keys) and len(set(labels)) == len(labels), category.code
        members = [m.name for key in keys for m in catalog.models_in_series(category.code, key)]
        assert sorted(members) == sorted(m.name for m in catalog.models_for(category.code)), category.code
        assert len(members) == len(set(members)), category.code
        for key in keys:
            assert catalog.models_in_series(category.code, key), (category.code, key)
            assert len(f"{category.code}.{key}") <= 20  # callback value stays short


def test_needs_series_only_for_long_categories():
    for category in catalog.CATEGORIES:
        expected = len(catalog.models_for(category.code)) > catalog.SERIES_MIN_MODELS and bool(
            catalog.series_for(category.code)
        )
        assert catalog.needs_series(category.code) is expected
    assert catalog.needs_series("iphone") and catalog.needs_series("ipad")
    assert catalog.needs_series("macbook") and catalog.needs_series("watch")
    assert not catalog.needs_series("airpods")
    assert not catalog.needs_series("other")
