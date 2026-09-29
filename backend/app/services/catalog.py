"""Static reference data: Apple models by category, options, kit items, grades.

Everything the wizard shows as buttons comes from here. Slugs are derived
deterministically by `services.tags`, so labels here must stay stable.

`MODELS` and `model_names()` are the iPhone list only. The wizard's model
callbacks are category-scoped ("iphone.3"), so any list may grow; stale buttons
from an older list are rejected by the wizard, never silently remapped. The other
categories live in `IPAD_MODELS`, `MACBOOK_MODELS`, `WATCH_MODELS`,
`AIRPODS_MODELS`; `APPLE_MODELS` is the union. Model names are unique across
the union, so `get_model` / `storages_for` / `colors_for` search all Apple
models: iPhone lookups behave exactly as before, and the same helpers work for
any category.

Lines marked `# verify` are best knowledge that a human should double-check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ModelSpec:
    name: str
    generation: str
    storages: tuple[int, ...]
    colors: tuple[str, ...]
    category: str = "iphone"
    # Category-specific option instead of / in addition to storage:
    # Watch case size ("44 мм"), MacBook unified memory ("16 ГБ").
    variants: tuple[str, ...] = ()


@dataclass(frozen=True)
class CategorySpec:
    code: str
    label: str
    device_type: str
    variant_label: str | None
    has_storage: bool
    battery_skippable: bool


@dataclass(frozen=True)
class KitItem:
    code: str
    label: str


@dataclass(frozen=True)
class GradeSpec:
    code: str
    label: str
    description: str


_STD = (128, 256, 512)
_STD_1TB = (128, 256, 512, 1024)
_PRO_1TB = (256, 512, 1024)

_PRO_2TB = _PRO_1TB + (2048,)

# 2026 additions verified on apple.com spec pages (28.09.2026):
#   https://www.apple.com/iphone-duo/specs/
#   https://www.apple.com/iphone-18-pro/specs/
#   https://www.apple.com/iphone-17e/specs/
MODELS: tuple[ModelSpec, ...] = (
    ModelSpec("iPhone Duo", "18", _PRO_2TB, ("Night Sky", "Star White")),
    ModelSpec("iPhone 18 Pro Max", "18", _PRO_2TB, ("Black", "Silver", "Glacier", "Burgundy")),
    ModelSpec("iPhone 18 Pro", "18", _PRO_2TB, ("Black", "Silver", "Glacier", "Burgundy")),
    ModelSpec("iPhone 17 Pro Max", "17", _PRO_1TB + (2048,), ("Silver", "Cosmic Orange", "Deep Blue")),
    ModelSpec("iPhone 17 Pro", "17", _PRO_1TB, ("Silver", "Cosmic Orange", "Deep Blue")),
    ModelSpec("iPhone Air", "17", _PRO_1TB, ("Space Black", "Cloud White", "Light Gold", "Sky Blue")),
    ModelSpec("iPhone 17", "17", (256, 512), ("Black", "White", "Mist Blue", "Sage", "Lavender")),
    ModelSpec("iPhone 17e", "17", (256, 512), ("Black", "White", "Soft Pink")),
    ModelSpec("iPhone 16 Pro Max", "16", _PRO_1TB, ("Black Titanium", "White Titanium", "Natural Titanium", "Desert Titanium")),
    ModelSpec("iPhone 16 Pro", "16", _STD_1TB, ("Black Titanium", "White Titanium", "Natural Titanium", "Desert Titanium")),
    ModelSpec("iPhone 16 Plus", "16", _STD, ("Black", "White", "Pink", "Teal", "Ultramarine")),
    ModelSpec("iPhone 16", "16", _STD, ("Black", "White", "Pink", "Teal", "Ultramarine")),
    ModelSpec("iPhone 16e", "16", _STD, ("Black", "White")),
    ModelSpec("iPhone 15 Pro Max", "15", _PRO_1TB, ("Black Titanium", "White Titanium", "Blue Titanium", "Natural Titanium")),
    ModelSpec("iPhone 15 Pro", "15", _STD_1TB, ("Black Titanium", "White Titanium", "Blue Titanium", "Natural Titanium")),
    ModelSpec("iPhone 15 Plus", "15", _STD, ("Black", "Blue", "Green", "Yellow", "Pink")),
    ModelSpec("iPhone 15", "15", _STD, ("Black", "Blue", "Green", "Yellow", "Pink")),
    ModelSpec("iPhone 14 Pro Max", "14", _STD_1TB, ("Space Black", "Silver", "Gold", "Deep Purple")),
    ModelSpec("iPhone 14 Pro", "14", _STD_1TB, ("Space Black", "Silver", "Gold", "Deep Purple")),
    ModelSpec("iPhone 14 Plus", "14", _STD, ("Midnight", "Starlight", "Blue", "Purple", "Yellow", "Red")),
    ModelSpec("iPhone 14", "14", _STD, ("Midnight", "Starlight", "Blue", "Purple", "Yellow", "Red")),
    ModelSpec("iPhone 13 Pro Max", "13", _STD_1TB, ("Graphite", "Gold", "Silver", "Sierra Blue", "Alpine Green")),
    ModelSpec("iPhone 13 Pro", "13", _STD_1TB, ("Graphite", "Gold", "Silver", "Sierra Blue", "Alpine Green")),
    ModelSpec("iPhone 13", "13", _STD, ("Midnight", "Starlight", "Blue", "Pink", "Red", "Green")),
    ModelSpec("iPhone 13 mini", "13", _STD, ("Midnight", "Starlight", "Blue", "Pink", "Red", "Green")),
    ModelSpec("iPhone 12 Pro Max", "12", _STD, ("Graphite", "Silver", "Gold", "Pacific Blue")),
    ModelSpec("iPhone 12 Pro", "12", _STD, ("Graphite", "Silver", "Gold", "Pacific Blue")),
    ModelSpec("iPhone 12", "12", (64, 128, 256), ("Black", "White", "Red", "Green", "Blue", "Purple")),
    ModelSpec("iPhone 12 mini", "12", (64, 128, 256), ("Black", "White", "Red", "Green", "Blue", "Purple")),
    ModelSpec("iPhone 11 Pro Max", "11", (64, 256, 512), ("Space Gray", "Silver", "Gold", "Midnight Green")),
    ModelSpec("iPhone 11 Pro", "11", (64, 256, 512), ("Space Gray", "Silver", "Gold", "Midnight Green")),
    ModelSpec("iPhone 11", "11", (64, 128, 256), ("Black", "White", "Red", "Green", "Yellow", "Purple")),
    ModelSpec("iPhone SE (2022)", "SE3", (64, 128, 256), ("Midnight", "Starlight", "Red")),
)

# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

DEVICE_TYPES: tuple[tuple[str, str], ...] = (
    ("phone", "Телефон"),
    ("tablet", "Планшет"),
    ("laptop", "Ноутбук"),
    ("watch", "Часы"),
    ("headphones", "Наушники"),
    ("other", "Другое"),
)
_DEVICE_TYPE_LABELS: dict[str, str] = dict(DEVICE_TYPES)

CATEGORIES: tuple[CategorySpec, ...] = (
    CategorySpec("iphone", "iPhone", "phone", None, has_storage=True, battery_skippable=False),
    # iPad: connectivity (Wi-Fi / Cellular) deliberately not asked to keep the wizard short.
    CategorySpec("ipad", "iPad", "tablet", None, has_storage=True, battery_skippable=False),
    CategorySpec("macbook", "MacBook", "laptop", "Оперативная память", has_storage=True, battery_skippable=False),
    CategorySpec("watch", "Apple Watch", "watch", "Размер корпуса", has_storage=False, battery_skippable=False),
    CategorySpec("airpods", "AirPods", "headphones", None, has_storage=False, battery_skippable=True),
)
_CATEGORIES_BY_CODE: dict[str, CategorySpec] = {c.code: c for c in CATEGORIES}

# ---------------------------------------------------------------------------
# Non-iPhone models. Newest first (by release date) within each category.
# Convention for `generation` in ALL non-iPhone categories: release year, "2025".
# (iPhones keep their series number, "17", for backward compatibility.)
# Storage in GB, 1024 = 1 TB. Colors are official Apple case/finish names.
# ---------------------------------------------------------------------------

_TB = 1024


def _gb(*values: int) -> tuple[str, ...]:
    return tuple(f"{v} ГБ" for v in values)


def _mm(*values: int) -> tuple[str, ...]:
    return tuple(f"{v} мм" for v in values)


def _ipad(name: str, year: str, storages: tuple[int, ...], colors: tuple[str, ...]) -> ModelSpec:
    return ModelSpec(name, year, storages, colors, category="ipad")


_IPAD_PRO_M4 = (256, 512, _TB, 2 * _TB)
_IPAD_PRO_M1 = (128, 256, 512, _TB, 2 * _TB)
_IPAD_AIR_NEW = (128, 256, 512, _TB)
_IPAD_AIR_COLORS = ("Space Gray", "Blue", "Purple", "Starlight")

IPAD_MODELS: tuple[ModelSpec, ...] = (
    _ipad("iPad Air 13 (M4)", "2026", _IPAD_AIR_NEW, _IPAD_AIR_COLORS),
    _ipad("iPad Air 11 (M4)", "2026", _IPAD_AIR_NEW, _IPAD_AIR_COLORS),
    _ipad("iPad Pro 13 (M5)", "2025", _IPAD_PRO_M4, ("Space Black", "Silver")),
    _ipad("iPad Pro 11 (M5)", "2025", _IPAD_PRO_M4, ("Space Black", "Silver")),
    _ipad("iPad Air 13 (M3)", "2025", _IPAD_AIR_NEW, _IPAD_AIR_COLORS),
    _ipad("iPad Air 11 (M3)", "2025", _IPAD_AIR_NEW, _IPAD_AIR_COLORS),
    _ipad("iPad (A16)", "2025", (128, 256, 512), ("Silver", "Blue", "Pink", "Yellow")),
    _ipad("iPad mini (A17 Pro)", "2024", (128, 256, 512), ("Space Gray", "Blue", "Purple", "Starlight")),
    _ipad("iPad Pro 13 (M4)", "2024", _IPAD_PRO_M4, ("Space Black", "Silver")),
    _ipad("iPad Pro 11 (M4)", "2024", _IPAD_PRO_M4, ("Space Black", "Silver")),
    _ipad("iPad Air 13 (M2)", "2024", _IPAD_AIR_NEW, _IPAD_AIR_COLORS),
    _ipad("iPad Air 11 (M2)", "2024", _IPAD_AIR_NEW, _IPAD_AIR_COLORS),
    _ipad("iPad Pro 12.9 (M2)", "2022", _IPAD_PRO_M1, ("Space Gray", "Silver")),
    _ipad("iPad Pro 11 (M2)", "2022", _IPAD_PRO_M1, ("Space Gray", "Silver")),
    _ipad("iPad (2022)", "2022", (64, 256), ("Silver", "Blue", "Pink", "Yellow")),
    _ipad("iPad Air (M1)", "2022", (64, 256), ("Space Gray", "Starlight", "Pink", "Purple", "Blue")),
    _ipad("iPad mini (2021)", "2021", (64, 256), ("Space Gray", "Pink", "Purple", "Starlight")),
    _ipad("iPad (2021)", "2021", (64, 256), ("Space Gray", "Silver")),
    _ipad("iPad Pro 12.9 (M1)", "2021", _IPAD_PRO_M1, ("Space Gray", "Silver")),
    _ipad("iPad Pro 11 (M1)", "2021", _IPAD_PRO_M1, ("Space Gray", "Silver")),
    _ipad("iPad Air (2020)", "2020", (64, 256), ("Space Gray", "Silver", "Rose Gold", "Green", "Sky Blue")),
    _ipad("iPad (2020)", "2020", (32, 128), ("Space Gray", "Silver", "Gold")),
    _ipad("iPad Pro 12.9 (2020)", "2020", (128, 256, 512, _TB), ("Space Gray", "Silver")),
    _ipad("iPad Pro 11 (2020)", "2020", (128, 256, 512, _TB), ("Space Gray", "Silver")),
)


def _mac(
    name: str, year: str, storages: tuple[int, ...], memory: tuple[str, ...], colors: tuple[str, ...]
) -> ModelSpec:
    return ModelSpec(name, year, storages, colors, category="macbook", variants=memory)


# MacBook Pro Pro/Max chip tiers are merged into one entry per size and generation
# ("M4 Pro/Max") to keep the list readable; memory/storage are the union of both tiers.
_AIR_M4_COLORS = ("Sky Blue", "Silver", "Starlight", "Midnight")
_AIR_M2_COLORS = ("Midnight", "Starlight", "Space Gray", "Silver")
_PRO_BLACK = ("Space Black", "Silver")
_PRO_GRAY = ("Space Gray", "Silver")
_MAC_256_2TB = (256, 512, _TB, 2 * _TB)
_MAC_512_2TB = (512, _TB, 2 * _TB)
_MAC_512_8TB = (512, _TB, 2 * _TB, 4 * _TB, 8 * _TB)

MACBOOK_MODELS: tuple[ModelSpec, ...] = (
    _mac("MacBook Pro 16 (M5 Pro/Max)", "2026", (_TB, 2 * _TB, 4 * _TB, 8 * _TB), _gb(24, 36, 48, 64, 128), _PRO_BLACK),
    _mac("MacBook Pro 14 (M5 Pro/Max)", "2026", (_TB, 2 * _TB, 4 * _TB, 8 * _TB), _gb(24, 36, 48, 64, 128), _PRO_BLACK),
    _mac("MacBook Air 15 (M5)", "2026", (512, _TB, 2 * _TB, 4 * _TB), _gb(16, 24, 32), _AIR_M4_COLORS),  # verify: 15" assumed same as 13"
    _mac("MacBook Air 13 (M5)", "2026", (512, _TB, 2 * _TB, 4 * _TB), _gb(16, 24, 32), _AIR_M4_COLORS),
    _mac("MacBook Neo", "2026", (256, 512), _gb(8), ("Silver", "Blush", "Citrus", "Indigo")),
    _mac("MacBook Pro 14 (M5)", "2025", (512, _TB, 2 * _TB, 4 * _TB), _gb(16, 24, 32), _PRO_BLACK),
    _mac("MacBook Air 15 (M4)", "2025", _MAC_256_2TB, _gb(16, 24, 32), _AIR_M4_COLORS),
    _mac("MacBook Air 13 (M4)", "2025", _MAC_256_2TB, _gb(16, 24, 32), _AIR_M4_COLORS),
    _mac("MacBook Pro 16 (M4 Pro/Max)", "2024", _MAC_512_8TB, _gb(24, 36, 48, 64, 128), _PRO_BLACK),
    _mac("MacBook Pro 14 (M4 Pro/Max)", "2024", _MAC_512_8TB, _gb(24, 36, 48, 64, 128), _PRO_BLACK),
    _mac("MacBook Pro 14 (M4)", "2024", _MAC_512_2TB, _gb(16, 24, 32), _PRO_BLACK),
    _mac("MacBook Air 15 (M3)", "2024", _MAC_256_2TB, _gb(8, 16, 24), _AIR_M2_COLORS),
    _mac("MacBook Air 13 (M3)", "2024", _MAC_256_2TB, _gb(8, 16, 24), _AIR_M2_COLORS),
    _mac("MacBook Pro 16 (M3 Pro/Max)", "2023", _MAC_512_8TB, _gb(18, 36, 48, 64, 96, 128), _PRO_BLACK),
    _mac("MacBook Pro 14 (M3 Pro/Max)", "2023", _MAC_512_8TB, _gb(18, 36, 48, 64, 96, 128), _PRO_BLACK),
    _mac("MacBook Pro 14 (M3)", "2023", _MAC_512_2TB, _gb(8, 16, 24), _PRO_GRAY),
    _mac("MacBook Air 15 (M2)", "2023", _MAC_256_2TB, _gb(8, 16, 24), _AIR_M2_COLORS),
    _mac("MacBook Pro 16 (M2 Pro/Max)", "2023", _MAC_512_8TB, _gb(16, 32, 64, 96), _PRO_GRAY),
    _mac("MacBook Pro 14 (M2 Pro/Max)", "2023", _MAC_512_8TB, _gb(16, 32, 64, 96), _PRO_GRAY),
    _mac("MacBook Air 13 (M2)", "2022", _MAC_256_2TB, _gb(8, 16, 24), _AIR_M2_COLORS),
    _mac("MacBook Pro 13 (M2)", "2022", _MAC_256_2TB, _gb(8, 16, 24), _PRO_GRAY),
    _mac("MacBook Pro 16 (M1 Pro/Max)", "2021", _MAC_512_8TB, _gb(16, 32, 64), _PRO_GRAY),
    _mac("MacBook Pro 14 (M1 Pro/Max)", "2021", _MAC_512_8TB, _gb(16, 32, 64), _PRO_GRAY),
    _mac("MacBook Air 13 (M1)", "2020", _MAC_256_2TB, _gb(8, 16), ("Space Gray", "Gold", "Silver")),
    _mac("MacBook Pro 13 (M1)", "2020", _MAC_256_2TB, _gb(8, 16), _PRO_GRAY),
)


def _watch(name: str, year: str, sizes: tuple[str, ...], colors: tuple[str, ...]) -> ModelSpec:
    return ModelSpec(name, year, (), colors, category="watch", variants=sizes)


# Case colors only (no bands). Aluminium finishes use the bare color name; other
# materials carry the material in the name so they never collide ("Silver" vs
# "Silver Stainless Steel").
_ULTRA_COLORS = ("Natural Titanium", "Black Titanium")
_S10_TITANIUM = ("Natural Titanium", "Gold Titanium", "Slate Titanium")
_STEEL = ("Silver Stainless Steel", "Graphite Stainless Steel", "Gold Stainless Steel")

WATCH_MODELS: tuple[ModelSpec, ...] = (
    _watch("Apple Watch Ultra 4", "2026", _mm(49), _ULTRA_COLORS),
    # Ceramic cases physically measure 43/47 mm but are sold as the 42/46 mm sizes.
    _watch(
        "Apple Watch Series 12",
        "2026",
        _mm(42, 46),
        (
            "Black", "Space Gray", "Light Gold", "Dark Bronze",
            "Natural Titanium", "Radiant Gold Titanium",
            "Pearl White Ceramic", "Night Blue Ceramic",
        ),
    ),
    _watch("Apple Watch Ultra 3", "2025", _mm(49), _ULTRA_COLORS),
    _watch(
        "Apple Watch Series 11",
        "2025",
        _mm(42, 46),
        ("Jet Black", "Space Gray", "Silver", "Rose Gold") + _S10_TITANIUM,
    ),
    _watch("Apple Watch SE 3", "2025", _mm(40, 44), ("Midnight", "Starlight")),
    _watch("Apple Watch Series 10", "2024", _mm(42, 46), ("Jet Black", "Silver", "Rose Gold") + _S10_TITANIUM),
    _watch("Apple Watch Ultra 2", "2023", _mm(49), _ULTRA_COLORS),  # Black Titanium since 2024
    _watch("Apple Watch Series 9", "2023", _mm(41, 45), ("Midnight", "Starlight", "Silver", "Pink", "Red") + _STEEL),
    _watch("Apple Watch Ultra", "2022", _mm(49), ("Natural Titanium",)),  # verify: Apple listed it as just "Titanium"
    _watch("Apple Watch Series 8", "2022", _mm(41, 45), ("Midnight", "Starlight", "Silver", "Red") + _STEEL),
    _watch("Apple Watch SE (2022)", "2022", _mm(40, 44), ("Midnight", "Starlight", "Silver")),
    _watch(
        "Apple Watch Series 7",
        "2021",
        _mm(41, 45),
        ("Midnight", "Starlight", "Green", "Blue", "Red") + _STEEL + ("Titanium", "Space Black Titanium"),  # verify: titanium (Edition) names
    ),
    _watch(
        "Apple Watch Series 6",
        "2020",
        _mm(40, 44),
        ("Space Gray", "Silver", "Gold", "Blue", "Red") + _STEEL + ("Titanium", "Space Black Titanium"),  # verify: titanium (Edition) names
    ),
    _watch("Apple Watch SE (2020)", "2020", _mm(40, 44), ("Space Gray", "Silver", "Gold")),
)


def _airpods(name: str, year: str, colors: tuple[str, ...] = ("White",)) -> ModelSpec:
    return ModelSpec(name, year, (), colors, category="airpods")


_MAX_USBC_COLORS = ("Midnight", "Starlight", "Blue", "Purple", "Orange")

AIRPODS_MODELS: tuple[ModelSpec, ...] = (
    _airpods("AirPods 5 (Wireless Charging)", "2026"),  # verify: naming of "AirPods 5 with Wireless Charging Case"
    _airpods("AirPods 5", "2026"),
    _airpods("AirPods Max 2", "2026", _MAX_USBC_COLORS),
    _airpods("AirPods Pro 3", "2025"),
    _airpods("AirPods 4 (ANC)", "2024"),
    _airpods("AirPods 4", "2024"),
    _airpods("AirPods Max (USB-C)", "2024", _MAX_USBC_COLORS),
    _airpods("AirPods Pro 2 (USB-C)", "2023"),
    _airpods("AirPods Pro 2 (Lightning)", "2022"),
    _airpods("AirPods 3", "2021"),
    _airpods("AirPods Max (Lightning)", "2020", ("Space Gray", "Silver", "Sky Blue", "Green", "Pink")),
    # Pre-2020 but still common on the used market.
    _airpods("AirPods Pro", "2019"),
    _airpods("AirPods 2", "2019"),
)

APPLE_MODELS: tuple[ModelSpec, ...] = MODELS + IPAD_MODELS + MACBOOK_MODELS + WATCH_MODELS + AIRPODS_MODELS

_MODELS_BY_NAME: dict[str, ModelSpec] = {m.name: m for m in APPLE_MODELS}
if len(_MODELS_BY_NAME) != len(APPLE_MODELS):  # pragma: no cover - guarded by tests
    raise RuntimeError("duplicate model names in catalog")

KIT_ITEMS: tuple[KitItem, ...] = (
    KitItem("box", "Коробка"),
    KitItem("cable", "Кабель"),
    KitItem("adapter", "Адаптер"),
    KitItem("receipt", "Чек"),
)
_KIT_BY_CODE: dict[str, KitItem] = {k.code: k for k in KIT_ITEMS}

GRADES: tuple[GradeSpec, ...] = (
    GradeSpec("A", "A — идеал", "Без следов использования, как новый."),
    GradeSpec("B", "B — хорошее", "Мелкие потёртости, царапины не видны на расстоянии."),
    GradeSpec("C", "C — заметные следы", "Заметные царапины или сколы, всё работает."),
)
_GRADE_BY_CODE: dict[str, GradeSpec] = {g.code: g for g in GRADES}


def get_model(name: str) -> ModelSpec | None:
    """Look up any Apple model by exact name (all categories).

    Names are unique across `APPLE_MODELS`, so iPhone lookups are unchanged.
    """
    return _MODELS_BY_NAME.get(name)


def get_apple_model(name: str) -> ModelSpec | None:
    return _MODELS_BY_NAME.get(name)


def model_names() -> list[str]:
    """iPhone names only, newest first."""
    return [m.name for m in MODELS]


def storages_for(model_name: str) -> tuple[int, ...]:
    spec = get_model(model_name)
    return spec.storages if spec else ()


def colors_for(model_name: str) -> tuple[str, ...]:
    spec = get_model(model_name)
    return spec.colors if spec else ()


def get_kit_item(code: str) -> KitItem | None:
    return _KIT_BY_CODE.get(code)


def kit_codes() -> list[str]:
    return [k.code for k in KIT_ITEMS]


def get_grade(code: str) -> GradeSpec | None:
    return _GRADE_BY_CODE.get(code)


def grade_codes() -> list[str]:
    return [g.code for g in GRADES]


def format_storage(storage_gb: int) -> str:
    if storage_gb >= 1024 and storage_gb % 1024 == 0:
        return f"{storage_gb // 1024} ТБ"
    return f"{storage_gb} ГБ"


def variants_for(model_name: str) -> tuple[str, ...]:
    spec = get_model(model_name)
    return spec.variants if spec else ()


# ---------------------------------------------------------------------------
# Categories and device types
# ---------------------------------------------------------------------------


def categories() -> list[CategorySpec]:
    return list(CATEGORIES)


def get_category(code: str) -> CategorySpec | None:
    return _CATEGORIES_BY_CODE.get(code)


def models_for(category: str) -> list[ModelSpec]:
    return [m for m in APPLE_MODELS if m.category == category]


def device_type_label(code: str) -> str | None:
    return _DEVICE_TYPE_LABELS.get(code)


# ---------------------------------------------------------------------------
# Wizard "matryoshka": manufacturer -> device -> series -> model
# ---------------------------------------------------------------------------

APPLE = "Apple"
# Manufacturer buttons of the /new wizard, in button order. Anything else is
# typed after «Другой» and goes through `normalize_brand`.
WIZARD_BRANDS: tuple[str, ...] = (
    APPLE, "Samsung", "Xiaomi", "Huawei", "Honor", "Google", "OnePlus", "Nothing", "Sony", "Lenovo",
)
# Apple device buttons: category code -> short label (CategorySpec.label is the
# long one used in captions and the API).
APPLE_DEVICES: tuple[tuple[str, str], ...] = (
    ("iphone", "iPhone"),
    ("ipad", "iPad"),
    ("macbook", "Mac"),
    ("watch", "Watch"),
    ("airpods", "AirPods"),
)
# A category gets the «Серия» level only when its model list is longer than this.
SERIES_MIN_MODELS = 8
# Family order for name-based series; families not listed follow in catalog order.
_FAMILY_ORDER: dict[str, tuple[str, ...]] = {
    "ipad": ("iPad", "iPad Air", "iPad mini", "iPad Pro"),
    "macbook": ("MacBook Air", "MacBook Pro"),
    "watch": ("Apple Watch Series", "Apple Watch Ultra", "Apple Watch SE"),
}
_FAMILY_LABEL_PREFIX: dict[str, str] = {"watch": "Apple Watch "}


def _family(name: str) -> str:
    """Model name up to the first size/number/chip token:
    "iPad Air 13 (M4)" -> "iPad Air", "Apple Watch SE (2022)" -> "Apple Watch SE"."""
    words: list[str] = []
    for word in name.split():
        if word[0].isdigit() or word.startswith("("):
            break
        words.append(word)
    return " ".join(words) or name


def series_of(spec: ModelSpec) -> tuple[str, str]:
    """(key, label) of the series a model belongs to. Keys are short and
    callback-safe: "15", "se", "air", "ultra"."""
    if spec.category == "iphone":
        if spec.generation.upper().startswith("SE"):
            return "se", "iPhone SE"
        return spec.generation.lower(), f"iPhone {spec.generation}"
    family = _family(spec.name)
    key = family.split()[-1].lower()
    label = family.removeprefix(_FAMILY_LABEL_PREFIX.get(spec.category, ""))
    return key, label


def series_for(category: str) -> list[tuple[str, str]]:
    """Series of a category as (key, label), [] where the category has none
    (AirPods). iPhone: by generation, newest first. iPad / Mac / Watch: by
    family in `_FAMILY_ORDER`, other families after them in catalog order."""
    if category not in ("iphone", *_FAMILY_ORDER):
        return []
    seen: dict[str, str] = {}
    for spec in models_for(category):
        key, label = series_of(spec)
        seen.setdefault(key, label)
    series = list(seen.items())
    order = _FAMILY_ORDER.get(category)
    if order:
        prefix = _FAMILY_LABEL_PREFIX.get(category, "")
        rank = {family.removeprefix(prefix): i for i, family in enumerate(order)}
        series.sort(key=lambda kv: rank.get(kv[1], len(rank)))  # stable: unknown families keep catalog order
    return series


def needs_series(category: str) -> bool:
    return len(models_for(category)) > SERIES_MIN_MODELS and bool(series_for(category))


def models_in_series(category: str, key: str) -> list[ModelSpec]:
    return [m for m in models_for(category) if series_of(m)[0] == key]


# ---------------------------------------------------------------------------
# Manual entry ("Другое"): brand normalization and year validation
# ---------------------------------------------------------------------------

_KNOWN_BRANDS: tuple[str, ...] = (
    "Apple", "Samsung", "Xiaomi", "Redmi", "POCO", "Huawei", "Honor", "Google",
    "OnePlus", "Sony", "Nothing", "Realme", "Oppo", "Vivo", "Asus", "Lenovo",
    "HP", "Dell", "Acer", "MSI", "Microsoft", "Garmin", "JBL", "Marshall",
    "LG", "ZTE", "Motorola", "Nokia", "Tecno", "Infinix",
)

_BRAND_SYNONYMS: dict[str, str] = {
    # Cyrillic spellings and common variants
    "эпл": "Apple", "эппл": "Apple", "апл": "Apple", "аппл": "Apple",
    "самсунг": "Samsung", "самсунк": "Samsung",
    "сяоми": "Xiaomi", "ксиоми": "Xiaomi", "ксяоми": "Xiaomi", "шаоми": "Xiaomi", "шиоми": "Xiaomi",
    "редми": "Redmi", "поко": "POCO", "хуавей": "Huawei", "хуавэй": "Huawei", "хуавеи": "Huawei",
    "хонор": "Honor", "гугл": "Google", "гугл пиксель": "Google", "google pixel": "Google",
    "one plus": "OnePlus", "ванплюс": "OnePlus", "уанплюс": "OnePlus",
    "сони": "Sony", "нотинг": "Nothing", "нафинг": "Nothing", "реалми": "Realme", "оппо": "Oppo",
    "виво": "Vivo", "асус": "Asus", "леново": "Lenovo", "hewlett packard": "HP",
    "hewlett-packard": "HP", "эйчпи": "HP", "делл": "Dell", "асер": "Acer", "эйсер": "Acer",
    "мси": "MSI", "майкрософт": "Microsoft", "гармин": "Garmin", "джибиэль": "JBL",
    "маршалл": "Marshall", "маршал": "Marshall", "моторола": "Motorola", "нокиа": "Nokia",
    "техно": "Tecno", "инфиникс": "Infinix",
}
_BRAND_LOOKUP: dict[str, str] = {b.casefold(): b for b in _KNOWN_BRANDS} | _BRAND_SYNONYMS

# Corporate suffixes dropped (from the end) when looking a brand up: "Samsung Electronics Co., Ltd."
_BRAND_SUFFIXES = frozenset(
    {"inc", "ltd", "llc", "co", "corp", "corporation", "company", "electronics", "technologies",
     "technology", "mobile", "group", "gmbh", "ag", "limited"}
)


def _brand_key(text: str) -> str:
    return " ".join(re.sub(r"[.,]", " ", text).casefold().split())


def _title_token(token: str) -> str:
    letters = [ch for ch in token if ch.isalpha()]
    if token.isupper() and len(letters) <= 4:
        return token  # HP, MSI, LG, ZTE
    return token[:1].upper() + token[1:].lower()


def normalize_brand(text: str) -> str:
    """Canonical brand name for a manually typed manufacturer.

    Trims and collapses whitespace, maps known brands and synonyms (incl.
    Cyrillic spellings and corporate suffixes) to canonical casing. Unknown
    brands are title-cased per word, keeping short all-caps tokens as typed.
    """
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return ""
    key = _brand_key(cleaned)
    tokens = key.split()
    while tokens:
        hit = _BRAND_LOOKUP.get(" ".join(tokens))
        if hit:
            return hit
        if tokens[-1] not in _BRAND_SUFFIXES:
            break
        tokens.pop()
    return " ".join(_title_token(t) for t in cleaned.split(" "))


MIN_YEAR = 2000


def validate_year(value: int) -> bool:
    """Release year for manual entry: 2000..current year inclusive."""
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return MIN_YEAR <= value <= date.today().year
