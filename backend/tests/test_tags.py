import json
from dataclasses import dataclass

import pytest
from sqlalchemy import select

from app.db.models import Item, ItemTag, Tag
from app.services import tags
from tests.db_utils import make_session_factory


@dataclass
class FakeItem:
    device_type: str | None = None
    brand: str | None = None
    model: str | None = None
    storage_gb: int | None = None
    color: str | None = None
    battery_health: int | None = None
    grade: str | None = None
    kit_json: str | None = None
    price: int | None = None


def full_item() -> FakeItem:
    return FakeItem(
        model="iPhone 15 Pro",
        storage_gb=256,
        color="Black Titanium",
        battery_health=92,
        grade="A",
        kit_json=json.dumps(["box", "cable", "adapter", "receipt"]),
        price=75_000,
    )


def test_build_tags_full_item_matches_spec_slugs():
    assert tags.build_tags(full_item()) == [
        "model:iphone-15-pro",
        "storage:256",
        "color:black-titanium",
        "grade:a",
        "battery:90plus",
        "kit:box",
        "kit:cable",
        "kit:adapter",
        "kit:receipt",
        "price:60-100k",
    ]


def test_build_tags_is_deterministic_and_skips_missing_fields():
    item = FakeItem(model="iPhone 12 mini")
    assert tags.build_tags(item) == tags.build_tags(item) == ["model:iphone-12-mini"]
    assert tags.build_tags(FakeItem()) == []


@pytest.mark.parametrize(
    "health,expected",
    [(100, "100"), (99, "90plus"), (90, "90plus"), (89, "85plus"), (85, "85plus"), (84, "below85"), (0, "below85")],
)
def test_battery_bands(health, expected):
    assert tags.battery_band(health) == expected
    assert tags.build_tags(FakeItem(battery_health=health)) == [f"battery:{expected}"]


@pytest.mark.parametrize(
    "price,expected",
    [(0, "0-30k"), (29_999, "0-30k"), (30_000, "30-60k"), (59_999, "30-60k"),
     (60_000, "60-100k"), (99_999, "60-100k"), (100_000, "100k-plus"), (250_000, "100k-plus")],
)
def test_price_bands(price, expected):
    assert tags.price_band(price) == expected


def test_kit_parsing_ignores_garbage_and_keeps_catalog_order():
    assert tags.parse_kit(None) == []
    assert tags.parse_kit("not json") == []
    assert tags.parse_kit('{"a": 1}') == []
    assert tags.parse_kit(json.dumps(["receipt", "box", "unicorn"])) == ["box", "receipt"]


def test_slugify_handles_spaces_case_and_punctuation():
    assert tags.slugify("iPhone SE (2022)") == "iphone-se-2022"
    assert tags.slugify("Deep Purple") == "deep-purple"
    assert tags.slugify("Ultramarine") == "ultramarine"


def test_tag_labels_are_russian_facing():
    labels = {d.slug: d.label for d in tags.build_tag_defs(full_item())}
    assert labels["storage:256"] == "256 ГБ"
    assert labels["grade:a"] == "Состояние A"
    assert labels["battery:90plus"] == "АКБ 90%+"
    assert labels["price:60-100k"] == "60–100 тыс."
    assert labels["kit:box"] == "Коробка"


def test_group_by_kind_for_api_filter():
    groups = tags.group_by_kind(
        ["model:iphone-15", "model:iphone-16", "storage:256", "bogus", "nocolon", "model:iphone-15"]
    )
    assert groups == {"model": ["model:iphone-15", "model:iphone-16"], "storage": ["storage:256"]}
    assert tags.kind_of("battery:100") == "battery_band"
    assert tags.kind_of("price:0-30k") == "price_band"
    assert tags.kind_of("garbage") is None


def _v2(**kw) -> FakeItem:
    base = dict(grade="B", kit_json="[]", price=50_000)
    base.update(kw)
    return FakeItem(**base)


def test_kind_order_matches_spec():
    assert tags.KIND_ORDER == (
        "device", "brand", "model", "storage", "color", "grade", "battery_band", "kit", "price_band"
    )


def test_build_tags_v2_iphone_in_kind_order():
    item = FakeItem(**vars(full_item()) | {"device_type": "phone", "brand": "Apple"})
    slugs = tags.build_tags(item)
    assert slugs[:3] == ["device:phone", "brand:apple", "model:iphone-15-pro"]
    kinds = [tags.kind_of(s) for s in slugs]
    assert kinds == sorted(kinds, key=tags.KIND_ORDER.index)
    labels = {d.slug: d.label for d in tags.build_tag_defs(item)}
    assert labels["device:phone"] == "Телефон"
    assert labels["brand:apple"] == "Apple"
    assert labels["model:iphone-15-pro"] == "iPhone 15 Pro"


@pytest.mark.parametrize(
    "item,expected",
    [
        (
            _v2(device_type="tablet", brand="Apple", model="iPad Air 11 (M2)", storage_gb=256, color="Blue",
                battery_health=88),
            ["device:tablet", "brand:apple", "model:ipad-air-11-m2", "storage:256", "color:blue", "grade:b",
             "battery:85plus", "price:30-60k"],
        ),
        (
            # variant (RAM) is shown in the title but is not a tag
            _v2(device_type="laptop", brand="Apple", model="MacBook Air 13 (M3)", storage_gb=512, color="Midnight",
                battery_health=100),
            ["device:laptop", "brand:apple", "model:macbook-air-13-m3", "storage:512", "color:midnight", "grade:b",
             "battery:100", "price:30-60k"],
        ),
        (
            # Watch: case size instead of storage -> no storage tag
            _v2(device_type="watch", brand="Apple", model="Apple Watch Series 10", color="Jet Black",
                battery_health=95),
            ["device:watch", "brand:apple", "model:apple-watch-series-10", "color:jet-black", "grade:b",
             "battery:90plus", "price:30-60k"],
        ),
        (
            # AirPods: no storage, battery skipped
            _v2(device_type="headphones", brand="Apple", model="AirPods Pro 2 (USB-C)", color="White", price=15_000),
            ["device:headphones", "brand:apple", "model:airpods-pro-2-usb-c", "color:white", "grade:b",
             "price:0-30k"],
        ),
    ],
    ids=["ipad", "macbook", "watch", "airpods"],
)
def test_build_tags_v2_apple_categories(item, expected):
    assert tags.build_tags(item) == expected


def test_build_tags_non_apple_normalizes_brand_and_prefixes_model():
    item = _v2(device_type="phone", brand="  samsung electronics ", model="Galaxy S23", storage_gb=256,
               color="Phantom Black", battery_health=None)
    assert tags.build_tags(item) == [
        "device:phone", "brand:samsung", "model:samsung-galaxy-s23", "storage:256", "color:phantom-black",
        "grade:b", "price:30-60k",
    ]
    labels = {d.slug: d.label for d in tags.build_tag_defs(item)}
    assert labels["brand:samsung"] == "Samsung"
    assert labels["model:samsung-galaxy-s23"] == "Samsung Galaxy S23"

    # model already starts with the brand -> no "samsung-samsung-..."
    same = _v2(device_type="phone", brand="Samsung", model="samsung Galaxy S23")
    assert "model:samsung-galaxy-s23" in tags.build_tags(same)

    # same model name from another brand does not collide
    other = _v2(device_type="phone", brand="xiaomi", model="Galaxy S23")
    assert "model:xiaomi-galaxy-s23" in tags.build_tags(other)


def test_build_tags_non_apple_without_storage_battery_color():
    item = _v2(device_type="other", brand="Sony", model="PlayStation 5", color="", battery_health=None,
               storage_gb=None)
    slugs = tags.build_tags(item)
    assert slugs == ["device:other", "brand:sony", "model:sony-playstation-5", "grade:b", "price:30-60k"]
    assert not any(s.startswith(("storage:", "battery:", "color:")) for s in slugs)
    labels = {d.slug: d.label for d in tags.build_tag_defs(item)}
    assert labels["device:other"] == "Другое"


def test_cyrillic_values_are_transliterated_not_dropped():
    assert tags.slugify("Чёрный графит") == "chernyy-grafit"
    item = _v2(device_type="laptop", brand="Хуавей", model="MateBook D 16", color="Серебристый")
    slugs = tags.build_tags(item)
    assert "brand:huawei" in slugs and "model:huawei-matebook-d-16" in slugs
    assert "color:serebristyy" in slugs
    # nothing sluggable -> no tag instead of a malformed "color:"
    assert not any(s.startswith("color:") for s in tags.build_tags(_v2(color="🔥")))


def test_group_by_kind_handles_v2_prefixes():
    groups = tags.group_by_kind(["device:phone", "device:laptop", "brand:apple", "model:iphone-15"])
    assert groups == {
        "device": ["device:phone", "device:laptop"],
        "brand": ["brand:apple"],
        "model": ["model:iphone-15"],
    }


async def test_sync_item_tags_creates_links_and_recomputes(tmp_path):
    factory = await make_session_factory(tmp_path / "t.db")
    async with factory() as session:
        item = Item(public_id="ABCDEFGH", status="draft", created_by=1, **vars(full_item()))
        session.add(item)
        await session.flush()

        slugs = await tags.sync_item_tags(session, item)
        await session.commit()
        assert slugs == tags.build_tags(item)

        linked = (
            await session.scalars(
                select(Tag.slug).join(ItemTag, ItemTag.tag_id == Tag.id).where(ItemTag.item_id == item.id)
            )
        ).all()
        assert set(linked) == set(slugs)

        # edit: price band changes, kit shrinks -> old links dropped, tag rows reused
        item.price = 120_000
        item.kit_json = json.dumps(["box"])
        slugs2 = await tags.sync_item_tags(session, item)
        await session.commit()

        linked2 = set(
            (
                await session.scalars(
                    select(Tag.slug).join(ItemTag, ItemTag.tag_id == Tag.id).where(ItemTag.item_id == item.id)
                )
            ).all()
        )
        assert linked2 == set(slugs2)
        assert "price:100k-plus" in linked2 and "price:60-100k" not in linked2
        assert "kit:cable" not in linked2

        all_tag_rows = (await session.scalars(select(Tag.slug))).all()
        assert len(all_tag_rows) == len(set(all_tag_rows))  # no duplicate tag rows
    await factory.kw["bind"].dispose()


async def test_cli_retag_adds_v2_tags_to_active_lots(tmp_path, monkeypatch):
    import json

    from sqlalchemy import select

    from app import cli
    from app.config import get_settings
    from app.db import session as db_session
    from app.db.models import Item, ItemTag, Tag
    from tests.db_utils import make_session_factory

    factory = await make_session_factory(tmp_path / "retag.db")
    monkeypatch.setattr(db_session, "get_session_factory", lambda: factory)
    monkeypatch.setattr(cli, "get_session_factory", lambda: factory)
    get_settings.cache_clear()
    async with factory() as s:
        s.add(Item(public_id="OLD00001", status="active", created_by=1, category="iphone", device_type="phone",
                   brand="Apple", model="iPhone Air", storage_gb=512, color="Space Black", battery_health=98,
                   grade="B", kit_json=json.dumps(["receipt"]), price=60000))
        s.add(Item(public_id="DRAFT001", status="draft", created_by=1))
        await s.commit()

    assert await cli.retag() == 1
    async with factory() as s:
        slugs = set((await s.scalars(select(Tag.slug).join(ItemTag, ItemTag.tag_id == Tag.id))).all())
    assert {"device:phone", "brand:apple", "model:iphone-air"} <= slugs
