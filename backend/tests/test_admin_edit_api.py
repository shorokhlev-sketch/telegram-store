"""PATCH /api/admin/items/{public_id} (full lot edit) and GET /api/admin/catalog."""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import Item, Photo
from app.db.session import get_session_factory
from tests.test_admin_api import (  # noqa: F401 - fixtures
    CHAT_ID,
    PUBLIC_ID,
    _add_item,
    _admin_headers,
    _no_webhook,
    _tags_for,
    client,
)
from tests.test_init_data import NON_ADMIN_USER, make_init_data


async def _patch(c, body: dict, headers: dict | None = None, public_id: str = PUBLIC_ID):
    return await c.patch(f"/api/admin/items/{public_id}", json=body, headers=_admin_headers() if headers is None else headers)


async def _db_item(public_id: str = PUBLIC_ID) -> Item:
    async with get_session_factory()() as session:
        return await session.scalar(select(Item).where(Item.public_id == public_id))


# ---------------------------------------------------------------------------
# Access and status
# ---------------------------------------------------------------------------


async def test_patch_non_admin_is_403(client):
    c, bot = client
    await _add_item()
    r = await _patch(c, {"model": "iPhone 16 Pro"}, headers={"X-Telegram-Init-Data": make_init_data(NON_ADMIN_USER)})
    assert r.status_code == 403
    assert bot.edited_captions == []


async def test_patch_sold_is_409(client):
    c, bot = client
    await _add_item(status="sold")
    r = await _patch(c, {"grade": "B"})
    assert r.status_code == 409
    assert r.json()["detail"] == "Лот уже продан"
    assert bot.edited_captions == []


async def test_patch_draft_is_409(client):
    c, _bot = client
    await _add_item(status="draft", with_channel_message=False)
    r = await _patch(c, {"grade": "B"})
    assert r.status_code == 409


async def test_patch_unknown_lot_is_404(client):
    c, _bot = client
    r = await _patch(c, {"grade": "B"}, public_id="NOSUCH01")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Field paths
# ---------------------------------------------------------------------------


async def test_list_carries_edit_fields(client):
    c, _bot = client
    await _add_item()
    r = await c.get("/api/admin/items", headers=_admin_headers())
    row = r.json()[0]
    assert row["category"] == "iphone"
    assert row["model"] == "iPhone 15 Pro"
    assert row["storage_gb"] == 256
    assert row["color"] == "Black Titanium"
    assert row["battery_health"] == 92
    assert row["grade"] == "A"
    assert row["kit"] == ["box"]
    assert row["year"] is None and row["variant"] is None


async def test_patch_model_moves_model_tag_and_edits_caption_with_buttons(client):
    c, bot = client
    await _add_item()
    assert "model:iphone-15-pro" in await _tags_for()

    r = await _patch(c, {"model": "iPhone 16 Pro", "storage_gb": 512, "color": "Desert Titanium"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["channel_updated"] is True
    assert body["model"] == "iPhone 16 Pro"
    assert body["title"] == "iPhone 16 Pro 512 ГБ, Desert Titanium"

    tags = await _tags_for()
    assert {"model:iphone-16-pro", "storage:512", "color:desert-titanium"} <= tags
    assert not tags & {"model:iphone-15-pro", "storage:256", "color:black-titanium"}

    item = await _db_item()
    assert item.generation == "16"

    assert len(bot.edited_captions) == 1
    call = bot.edited_captions[0]
    assert (call["chat_id"], call["message_id"], call["parse_mode"]) == (CHAT_ID, 5001, "HTML")
    assert "iPhone 16 Pro 512 ГБ, Desert Titanium" in call["caption"]
    buttons = [b for row in call["reply_markup"].inline_keyboard for b in row]
    assert [b.text for b in buttons] == ["Написать продавцу", "Открыть в приложении"]
    assert "iPhone%2016%20Pro" in buttons[0].url or "iPhone+16+Pro" in buttons[0].url


async def test_patch_grade_battery_kit_update_tags(client):
    c, _bot = client
    await _add_item()
    r = await _patch(c, {"grade": "C", "battery_health": 80, "kit": ["cable", "adapter"]})
    assert r.status_code == 200, r.text
    assert r.json()["kit"] == ["cable", "adapter"]
    tags = await _tags_for()
    assert {"grade:c", "battery:below85", "kit:cable", "kit:adapter"} <= tags
    assert not tags & {"grade:a", "battery:90plus", "kit:box"}


async def test_patch_category_to_other(client):
    c, bot = client
    await _add_item()
    r = await _patch(
        c,
        {"category": "other", "device_type": "phone", "brand": "самсунг", "model": "Galaxy S23", "year": 2023, "storage_gb": 256, "color": "чёрный", "battery_health": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["category"], body["brand"], body["model"], body["year"]) == ("other", "Samsung", "Galaxy S23", 2023)
    assert body["battery_health"] is None
    tags = await _tags_for()
    assert {"brand:samsung", "model:samsung-galaxy-s23", "color:chernyy", "device:phone"} <= tags
    assert not any(t.startswith("battery:") for t in tags)
    assert "brand:apple" not in tags
    assert "Samsung Galaxy S23 (2023) 256 ГБ" in bot.edited_captions[0]["caption"]


async def test_patch_category_resets_dependents(client):
    c, bot = client
    await _add_item()
    r = await _patch(c, {"category": "macbook"})
    assert r.status_code == 422
    assert r.json() == {"detail": {"field": "model", "message": "Выберите модель"}}

    r = await _patch(c, {"category": "macbook", "model": "MacBook Air 13 (M2)", "storage_gb": 256, "color": "Midnight"})
    assert r.status_code == 422
    assert r.json()["detail"] == {"field": "variant", "message": "Выберите оперативную память"}

    r = await _patch(c, {"category": "macbook", "model": "MacBook Air 13 (M2)", "storage_gb": 256, "variant": "16 ГБ", "color": "Midnight"})
    assert r.status_code == 200, r.text
    assert "device:laptop" in await _tags_for()
    # failed attempts never touched the row or the channel
    assert len(bot.edited_captions) == 1


async def test_patch_validation_errors_are_field_level_and_russian(client):
    c, bot = client
    await _add_item()
    cases = [
        ({"storage_gb": 64}, "storage_gb", "Нет такого объёма памяти для iPhone 15 Pro"),
        ({"color": "Pink"}, "color", "Нет такого цвета для iPhone 15 Pro"),
        ({"model": "Galaxy S23"}, "model", "Нет такой модели в категории iPhone"),
        ({"battery_health": None}, "battery_health", "Укажите ёмкость аккумулятора"),
        ({"battery_health": 150}, "battery_health", "Нужно целое число от 0 до 100"),
        ({"grade": "Z"}, "grade", "Состояние: A, B или C"),
        ({"kit": ["charger"]}, "kit", "Нет такого пункта комплекта"),
        ({"price": "дорого"}, "price", "Нужно целое число больше нуля"),
        ({"year": 2020}, "year", "Год указывается только в категории «Другое»"),
        ({"category": "fridge"}, "category", "Нет такой категории"),
    ]
    for patch, field, message in cases:
        r = await _patch(c, patch)
        assert r.status_code == 422, patch
        assert r.json()["detail"] == {"field": field, "message": message}, patch
    item = await _db_item()
    assert (item.model, item.storage_gb, item.color, item.grade, item.price) == ("iPhone 15 Pro", 256, "Black Titanium", "A", 75_000)
    assert bot.edited_captions == []


async def test_patch_other_lot_year_validation(client):
    c, _bot = client
    await _add_item()
    r = await _patch(c, {"category": "other", "device_type": "laptop", "brand": "Lenovo", "model": "ThinkPad X1", "year": 1999})
    assert r.status_code == 422
    assert r.json()["detail"]["field"] == "year"


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


async def test_catalog_requires_admin(client):
    c, _bot = client
    assert (await c.get("/api/admin/catalog")).status_code == 401
    r = await c.get("/api/admin/catalog", headers={"X-Telegram-Init-Data": make_init_data(NON_ADMIN_USER)})
    assert r.status_code == 403


async def test_catalog_payload(client):
    c, _bot = client
    r = await c.get("/api/admin/catalog", headers=_admin_headers())
    assert r.status_code == 200
    assert "max-age" in r.headers["cache-control"]
    data = r.json()
    assert set(data) == {"categories", "models", "device_types", "kit", "grades", "other_storages", "limits"}
    other = data["categories"][-1]
    assert other["code"] == "other" and other["manual"] is True and other["battery_skippable"] is True
    airpods = next(cat for cat in data["categories"] if cat["code"] == "airpods")
    assert airpods["has_storage"] is False and airpods["battery_skippable"] is True
    iphone15 = next(m for m in data["models"]["iphone"] if m["name"] == "iPhone 15 Pro")
    assert iphone15["storages"] == [128, 256, 512, 1024]
    assert "Black Titanium" in iphone15["colors"]
    assert data["other_storages"][0] == 32


# ---------------------------------------------------------------------------
# Cover
# ---------------------------------------------------------------------------


async def _add_media(public_id: str = PUBLIC_ID, spec=(("p0", "photo"), ("v0", "video"), ("p1", "photo"), ("p2", "photo"))) -> None:
    async with get_session_factory()() as session:
        item = await session.scalar(select(Item).where(Item.public_id == public_id))
        for position, (uid, kind) in enumerate(spec):
            session.add(Photo(item_id=item.id, kind=kind, tg_file_id=f"file_{uid}", file_unique_id=uid, position=position))
        await session.commit()


async def _positions(public_id: str = PUBLIC_ID) -> list[str]:
    async with get_session_factory()() as session:
        item = await session.scalar(select(Item).where(Item.public_id == public_id))
        rows = (await session.scalars(select(Photo).where(Photo.item_id == item.id).order_by(Photo.position))).all()
        return [p.file_unique_id for p in rows]


async def _cover(c, file_unique_id: str, headers: dict | None = None, public_id: str = PUBLIC_ID):
    return await c.post(
        f"/api/admin/items/{public_id}/cover",
        json={"file_unique_id": file_unique_id},
        headers=_admin_headers() if headers is None else headers,
    )


async def test_list_carries_photos_in_order(client):
    c, _bot = client
    await _add_item()
    await _add_media()
    row = (await c.get("/api/admin/items", headers=_admin_headers())).json()[0]
    assert [p["file_unique_id"] for p in row["photos"]] == ["p0", "p1", "p2"]  # videos left out
    assert row["photos"][0]["preview"] == "https://example.test/api/media/p0/preview"
    assert row["cover"] == row["photos"][0]["preview"]


async def test_cover_reorders_and_swaps_channel_photo(client):
    c, bot = client
    await _add_item()
    await _add_media()
    r = await _cover(c, "p2")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["channel_updated"] is True
    assert [p["file_unique_id"] for p in body["photos"]] == ["p2", "p0", "p1"]
    assert body["cover"].endswith("/p2/preview")
    assert await _positions() == ["p2", "p0", "v0", "p1"]

    assert len(bot.edited_media) == 1
    call = bot.edited_media[0]
    assert (call["chat_id"], call["message_id"]) == (CHAT_ID, 5001)
    assert call["media"].media == "file_p2"
    assert call["media"].parse_mode == "HTML"
    assert "iPhone 15 Pro 256 ГБ, Black Titanium" in call["media"].caption
    buttons = [b.text for row in call["reply_markup"].inline_keyboard for b in row]
    assert buttons == ["Написать продавцу", "Открыть в приложении"]
    assert bot.edited_captions == []


async def test_cover_already_first_is_a_no_op(client):
    c, bot = client
    await _add_item()
    await _add_media()
    r = await _cover(c, "p0")
    assert r.status_code == 200
    assert r.json()["channel_updated"] is True
    assert bot.edited_media == []
    assert await _positions() == ["p0", "v0", "p1", "p2"]


async def test_cover_not_modified_is_not_an_error(client):
    c, bot = client
    await _add_item()
    await _add_media()
    bot.not_modified_ids.add(5001)
    r = await _cover(c, "p1")
    assert r.status_code == 200
    assert r.json()["channel_updated"] is True


async def test_cover_telegram_failure_keeps_db_change(client):
    c, bot = client
    await _add_item()
    await _add_media()
    bot.broken_ids.add(5001)
    r = await _cover(c, "p1")
    assert r.status_code == 200
    assert r.json()["channel_updated"] is False
    assert await _positions() == ["p1", "p0", "v0", "p2"]


async def test_cover_rejects_video_and_foreign_ids(client):
    c, bot = client
    await _add_item()
    await _add_media()
    await _add_item("OTHERLOT")
    await _add_media("OTHERLOT", spec=(("x0", "photo"), ("x1", "photo")))

    r = await _cover(c, "v0")
    assert r.status_code == 422
    assert r.json()["detail"] == {"field": "file_unique_id", "message": "Видео не может быть обложкой"}
    r = await _cover(c, "x1")
    assert r.status_code == 422
    assert r.json()["detail"] == {"field": "file_unique_id", "message": "У этого лота нет такого фото"}
    r = await c.post(f"/api/admin/items/{PUBLIC_ID}/cover", json={}, headers=_admin_headers())
    assert r.status_code == 422
    assert await _positions() == ["p0", "v0", "p1", "p2"]
    assert bot.edited_media == []


async def test_cover_sold_is_409_and_non_admin_403(client):
    c, bot = client
    await _add_item(status="sold")
    await _add_media()
    assert (await _cover(c, "p1")).status_code == 409
    r = await _cover(c, "p1", headers={"X-Telegram-Init-Data": make_init_data(NON_ADMIN_USER)})
    assert r.status_code == 403
    assert (await c.post(f"/api/admin/items/{PUBLIC_ID}/cover", json={"file_unique_id": "p1"})).status_code == 401
    assert await _positions() == ["p0", "v0", "p1", "p2"]
    assert bot.edited_media == []
