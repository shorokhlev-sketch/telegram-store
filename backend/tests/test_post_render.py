import json

from app.db.models import Item
from app.services.post_render import CAPTION_LIMIT, item_title, render_caption


def _item(**kw) -> Item:
    base = dict(
        public_id="ABCDEFGH",
        status="draft",
        created_by=1,
        model="iPhone 15 Pro",
        storage_gb=256,
        color="Black Titanium",
        battery_health=92,
        grade="A",
        kit_json=json.dumps(["box", "cable"]),
        price=75000,
        note="",
    )
    base.update(kw)
    return Item(**base)


def test_caption_contains_all_fields():
    text = render_caption(_item(note="Мелкая царапина на рамке <test>"))
    assert text.startswith("<b>iPhone 15 Pro 256 ГБ, Black Titanium</b>")
    assert "Состояние: A — идеал." in text
    assert "АКБ: 92%" in text
    assert "Комплект: коробка, кабель" in text
    assert "<b>Цена: 75 000 ₽</b>" in text
    assert "&lt;test&gt;" in text  # html escaped
    assert text.rstrip().endswith("Лот ABCDEFGH")


def test_caption_empty_kit_and_no_note():
    text = render_caption(_item(kit_json="[]", note=""))
    assert "Комплект: только устройство" in text
    assert "\n\n\n" not in text


def test_caption_respects_telegram_limit():
    text = render_caption(_item(note="х" * 5000))
    assert len(text) <= CAPTION_LIMIT
    assert "…" in text
    assert text.rstrip().endswith("Лот ABCDEFGH")


def test_item_title_apple_catalog_categories():
    assert item_title(_item()) == "iPhone 15 Pro 256 ГБ, Black Titanium"
    watch = _item(category="watch", device_type="watch", brand="Apple", model="Apple Watch Series 10",
                  storage_gb=None, variant="46 мм", color="Jet Black")
    assert item_title(watch) == "Apple Watch Series 10, 46 мм, Jet Black"
    mac = _item(category="macbook", device_type="laptop", brand="Apple", model="MacBook Air 13 (M3)",
                storage_gb=512, variant="16 ГБ", color="Midnight")
    assert item_title(mac) == "MacBook Air 13 (M3) 512 ГБ, 16 ГБ RAM, Midnight"
    assert item_title(_item(category="macbook", model="MacBook Pro 14 (M4)", storage_gb=1024,
                            variant="24 ГБ RAM", color="Silver")) == "MacBook Pro 14 (M4) 1 ТБ, 24 ГБ RAM, Silver"
    airpods = _item(category="airpods", device_type="headphones", brand="Apple", model="AirPods Pro 3",
                    storage_gb=None, color="White")
    assert item_title(airpods) == "AirPods Pro 3, White"


def test_item_title_non_apple():
    phone = _item(category="other", device_type="phone", brand="Samsung", model="Galaxy S23", year=2023,
                  storage_gb=256, color="Phantom Black")
    assert item_title(phone) == "Samsung Galaxy S23 (2023) 256 ГБ, Phantom Black"
    # brand not repeated, no year/storage/color
    bare = _item(category="other", device_type="other", brand="Sony", model="Sony PlayStation 5", year=None,
                 storage_gb=None, color=None)
    assert item_title(bare) == "Sony PlayStation 5"


def test_caption_apple_non_iphone_has_no_device_line():
    watch = _item(category="watch", device_type="watch", brand="Apple", model="Apple Watch Series 10",
                  storage_gb=None, variant="46 мм", color="Jet Black", battery_health=97)
    text = render_caption(watch)
    assert text.startswith("<b>Apple Watch Series 10, 46 мм, Jet Black</b>")
    assert "Устройство:" not in text
    assert "АКБ: 97%" in text


def test_caption_non_apple_with_year_and_no_battery():
    item = _item(category="other", device_type="laptop", brand="lenovo", model="ThinkPad X1 Carbon", year=2022,
                 storage_gb=512, color="", battery_health=None, note="Родная зарядка")
    text = render_caption(item)
    assert text.startswith("<b>Lenovo ThinkPad X1 Carbon (2022) 512 ГБ</b>")
    assert "Устройство: Ноутбук" in text
    assert "АКБ" not in text
    assert "<b>Цена: 75 000 ₽</b>" in text
    assert "Родная зарядка" in text
    assert text.rstrip().endswith("Лот ABCDEFGH")
