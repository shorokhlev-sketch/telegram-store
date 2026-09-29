from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from aiogram import Bot
from aiogram.types import File
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select

from app.config import get_settings
from app.db.base import Base
from app.db.models import Item, Photo
from app.db.session import get_engine, get_session_factory
from app.main import app
from app.services import tags as tags_service


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


class FakeMediaBot(Bot):
    """Minimal fake for media.py: serves fixed bytes per tg_file_id, never
    touches the network."""

    def __init__(self, content_by_file_id: dict[str, bytes]) -> None:
        super().__init__(token="123456:TEST")
        self._content = content_by_file_id
        self.get_file_calls: list[str] = []
        self.download_calls: list[str] = []

    async def get_file(self, file_id: str, request_timeout: int | None = None) -> File:
        self.get_file_calls.append(file_id)
        return File(file_id=file_id, file_unique_id=file_id, file_path=f"remote/{file_id}")

    async def download_file(self, file_path, destination=None, timeout=30, chunk_size=65536, seek=True):
        self.download_calls.append(file_path)
        file_id = str(file_path).split("/", 1)[1]
        return io.BytesIO(self._content[file_id])


def _jpeg_bytes(color=(200, 50, 50), size=(800, 600)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _no_webhook(monkeypatch):
    monkeypatch.setenv("SET_WEBHOOK_ON_START", "0")
    get_settings.cache_clear()


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c


async def _session():
    factory = get_session_factory()
    return factory()


async def _add_item(
    session,
    *,
    public_id: str,
    status: str = "active",
    model: str = "iPhone 15 Pro",
    device_type: str = "phone",
    brand: str = "Apple",
    price: int | None = 75_000,
    storage_gb: int | None = 256,
    color: str | None = "Black Titanium",
    grade: str | None = "A",
    battery_health: int | None = 92,
    published_at: datetime | None = None,
    photos: list[tuple[str, str]] | None = None,
    category: str | None = None,
    variant: str | None = None,
) -> Item:
    item = Item(
        public_id=public_id,
        status=status,
        created_by=111,
        category=category or ("iphone" if brand == "Apple" else "other"),
        device_type=device_type,
        brand=brand,
        model=model,
        storage_gb=storage_gb,
        variant=variant,
        color=color,
        battery_health=battery_health,
        grade=grade,
        kit_json=json.dumps(["box"]),
        price=price,
        note="",
        # Active/sold lots always carry a real timestamp in production (set at
        # publish time); default to "now" here so cursor encoding and the new
        # published_at card field have something to work with.
        published_at=(published_at or datetime.now(timezone.utc)) if status != "draft" else None,
    )
    session.add(item)
    await session.flush()
    for i, (kind, uid) in enumerate(photos or [("photo", f"{public_id}-p0")]):
        session.add(Photo(item_id=item.id, kind=kind, tg_file_id=uid, file_unique_id=uid, position=i))
    await session.flush()
    await session.refresh(item, ["photos"])
    await tags_service.sync_item_tags(session, item)
    await session.commit()
    return item


# ---------------------------------------------------------------------------
# /api/items listing, cursor pagination
# ---------------------------------------------------------------------------


async def test_listing_orders_by_published_at_desc_and_paginates_by_cursor(client):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with await _session() as session:
        for i in range(5):
            await _add_item(session, public_id=f"ITEM000{i}", published_at=base + timedelta(minutes=i))

    seen: list[str] = []
    cursor = None
    for _ in range(10):
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        r = await client.get("/api/items", params=params)
        assert r.status_code == 200
        body = r.json()
        seen.extend(it["public_id"] for it in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    # newest (highest minute offset) first, no duplicates/omissions
    assert seen == ["ITEM0004", "ITEM0003", "ITEM0002", "ITEM0001", "ITEM0000"]


async def test_listing_only_returns_active_items(client):
    async with await _session() as session:
        await _add_item(session, public_id="ACTIVE01")
        await _add_item(session, public_id="DRAFT001", status="draft")
        await _add_item(session, public_id="SOLD0001", status="sold")

    r = await client.get("/api/items")
    assert r.status_code == 200
    ids = [it["public_id"] for it in r.json()["items"]]
    assert ids == ["ACTIVE01"]


async def test_item_card_fields(client):
    async with await _session() as session:
        await _add_item(
            session,
            public_id="CARD0001",
            model="iPhone 15 Pro",
            storage_gb=256,
            color="Black Titanium",
            grade="A",
            photos=[("photo", "cover-uid"), ("video", "clip-uid")],
        )
        item = await session.scalar(select(Item).where(Item.public_id == "CARD0001"))
        item.note = "Царапина на корпусе"
        await session.commit()

    r = await client.get("/api/items")
    card = r.json()["items"][0]
    assert card["public_id"] == "CARD0001"
    assert card["title"] == "iPhone 15 Pro 256 ГБ, Black Titanium"
    assert card["device_type"] == "phone"
    assert card["brand"] == "Apple"
    assert card["storage_gb"] == 256
    assert card["color"] == "Black Titanium"
    assert card["price"] == 75_000
    assert card["has_video"] is True
    assert card["cover"] == "https://example.test/api/media/cover-uid/preview"
    # fields added so cards render without a per-card detail fetch
    assert card["published_at"] is not None
    assert card["note"] == "Царапина на корпусе"
    assert card["grade_label"].startswith("A")
    assert card["seller_url"].startswith("https://t.me/seller_test?text=")


# ---------------------------------------------------------------------------
# /api/items total + /api/items/count
# ---------------------------------------------------------------------------


async def test_items_total_reflects_tag_filter(client):
    async with await _session() as session:
        await _add_item(session, public_id="TOTALA01", device_type="phone", grade="A")
        await _add_item(session, public_id="TOTALB01", device_type="phone", grade="B")
        await _add_item(session, public_id="TOTALC01", device_type="tablet", model="iPad Air", grade="A")
        await _add_item(session, public_id="TOTALS01", status="sold")

    r = await client.get("/api/items", params={"limit": 1})
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1  # limit caps the page, not the total
    assert body["total"] == 3  # sold lot excluded

    r = await client.get("/api/items", params={"tags": "device:phone"})
    assert r.json()["total"] == 2

    r = await client.get("/api/items", params={"tags": "device:phone,grade:a"})
    assert r.json()["total"] == 1


async def test_items_count_endpoint_matches_total_and_ignores_cursor(client):
    async with await _session() as session:
        await _add_item(session, public_id="CNTA0001", device_type="phone")
        await _add_item(session, public_id="CNTA0002", device_type="tablet", model="iPad Air")

    r = await client.get("/api/items/count")
    assert r.status_code == 200
    assert r.json() == {"total": 2}

    r = await client.get("/api/items/count", params={"tags": "device:phone"})
    assert r.json() == {"total": 1}

    r = await client.get("/api/items/count", params={"tags": "brand:does-not-exist"})
    assert r.json() == {"total": 0}


# ---------------------------------------------------------------------------
# Tag filtering: AND across kinds, OR within a kind, unknown tag
# ---------------------------------------------------------------------------


async def test_tag_filter_and_across_kinds_or_within_kind(client):
    async with await _session() as session:
        await _add_item(session, public_id="PHONEA01", device_type="phone", brand="Apple", grade="A")
        await _add_item(session, public_id="PHONEB01", device_type="phone", brand="Apple", grade="B")
        await _add_item(session, public_id="TABLETA1", device_type="tablet", brand="Apple", model="iPad Air", grade="A")

    # OR within "grade": both phone lots (A and B) match.
    r = await client.get("/api/items", params={"tags": "device:phone,grade:a,grade:b"})
    ids = {it["public_id"] for it in r.json()["items"]}
    assert ids == {"PHONEA01", "PHONEB01"}

    # AND across "device" and "grade": only the phone with grade A.
    r = await client.get("/api/items", params={"tags": "device:phone,grade:a"})
    ids = {it["public_id"] for it in r.json()["items"]}
    assert ids == {"PHONEA01"}


async def test_unknown_tag_yields_empty_result_not_error(client):
    async with await _session() as session:
        await _add_item(session, public_id="KNOWN001")

    r = await client.get("/api/items", params={"tags": "brand:does-not-exist"})
    assert r.status_code == 200
    assert r.json()["items"] == []


# ---------------------------------------------------------------------------
# /api/items/{public_id}
# ---------------------------------------------------------------------------


async def test_item_detail_sold_is_410_and_draft_is_404(client):
    async with await _session() as session:
        await _add_item(session, public_id="SOLDLOT1", status="sold")
        await _add_item(session, public_id="DRAFTLO1", status="draft")

    r = await client.get("/api/items/SOLDLOT1")
    assert r.status_code == 410
    r = await client.get("/api/items/DRAFTLO1")
    assert r.status_code == 404
    r = await client.get("/api/items/DOES-NOT-EXIST")
    assert r.status_code == 404


async def test_item_detail_full_card(client):
    async with await _session() as session:
        await _add_item(
            session,
            public_id="DETAIL01",
            photos=[("photo", "det-photo"), ("video", "det-video")],
        )

    r = await client.get("/api/items/DETAIL01")
    assert r.status_code == 200
    body = r.json()
    assert body["public_id"] == "DETAIL01"
    assert body["grade_label"].startswith("A")
    assert body["kit"] == [{"code": "box", "label": "Коробка"}]
    assert body["seller_url"].startswith("https://t.me/seller_test?text=")
    assert body["miniapp_url"] == "https://t.me/test_bot/shop?startapp=item_DETAIL01"
    media_kinds = [m["kind"] for m in body["media"]]
    assert media_kinds == ["photo", "video"]
    assert body["media"][0]["preview"] == "https://example.test/api/media/det-photo/preview"
    assert body["media"][0]["full"] == "https://example.test/api/media/det-photo/full"
    assert body["media"][1]["video"] == "https://example.test/api/media/det-video/video"


# ---------------------------------------------------------------------------
# /api/tags
# ---------------------------------------------------------------------------


async def test_tags_endpoint_counts_order_and_zero_omission(client):
    async with await _session() as session:
        await _add_item(session, public_id="TAGSA001", device_type="phone", grade="A")
        await _add_item(session, public_id="TAGSA002", device_type="phone", grade="A")
        await _add_item(session, public_id="TAGSB001", device_type="phone", grade="B")
        # sold lot: its tags must not be counted, and if it's the only holder
        # of a tag value, that value must not appear at all.
        await _add_item(session, public_id="SOLDTAG1", device_type="watch", model="Apple Watch Series 10", grade="C", status="sold")

    r = await client.get("/api/tags")
    assert r.status_code == 200
    groups = {g["kind"]: g for g in r.json()}

    # KIND_ORDER: device, brand, model, storage, color, grade, battery_band, kit, price_band
    order = [g["kind"] for g in r.json()]
    assert order == sorted(order, key=lambda k: tags_service.KIND_ORDER.index(k))

    device_tags = {t["slug"]: t["count"] for t in groups["device"]["tags"]}
    assert device_tags == {"device:phone": 3}  # "device:watch" omitted: only the sold lot had it

    grade_tags = {t["slug"]: t["count"] for t in groups["grade"]["tags"]}
    assert grade_tags == {"grade:a": 2, "grade:b": 1}  # grade:c omitted (sold-only)
    assert groups["grade"]["label"] == "Состояние"
    assert groups["device"]["label"] == "Устройство"


async def test_tags_endpoint_orders_storage_numerically_and_price_by_band(client):
    async with await _session() as session:
        await _add_item(session, public_id="STOR0064", storage_gb=64, price=20_000)
        await _add_item(session, public_id="STOR0512", storage_gb=512, price=150_000)
        await _add_item(session, public_id="STOR0128", storage_gb=128, price=45_000)

    r = await client.get("/api/tags")
    groups = {g["kind"]: g for g in r.json()}
    storage_slugs = [t["slug"] for t in groups["storage"]["tags"]]
    assert storage_slugs == ["storage:64", "storage:128", "storage:512"]
    price_slugs = [t["slug"] for t in groups["price_band"]["tags"]]
    assert price_slugs == ["price:0-30k", "price:30-60k", "price:100k-plus"]


# ---------------------------------------------------------------------------
# /api/media
# ---------------------------------------------------------------------------


async def test_photo_media_is_generated_once_and_cached(client):
    jpeg = _jpeg_bytes()
    async with await _session() as session:
        await _add_item(session, public_id="MEDIA001", photos=[("photo", "photo-uid-1")])

    bot = FakeMediaBot({"photo-uid-1": jpeg})
    app.state.bot = bot

    r1 = await client.get("/api/media/photo-uid-1/preview")
    assert r1.status_code == 200
    assert r1.headers["content-type"] == "image/jpeg"
    assert r1.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert bot.get_file_calls == ["photo-uid-1"]
    assert bot.download_calls == ["remote/photo-uid-1"]

    r2 = await client.get("/api/media/photo-uid-1/preview")
    assert r2.status_code == 200
    assert r2.content == r1.content
    # second request served from disk: no extra Bot API calls
    assert bot.get_file_calls == ["photo-uid-1"]
    assert bot.download_calls == ["remote/photo-uid-1"]

    # a distinct size (full) does trigger a fresh generation
    r3 = await client.get("/api/media/photo-uid-1/full")
    assert r3.status_code == 200
    assert bot.get_file_calls == ["photo-uid-1", "photo-uid-1"]


async def test_video_media_supports_range_requests(client):
    video_bytes = bytes(i % 256 for i in range(5000))
    async with await _session() as session:
        await _add_item(session, public_id="MEDIA002", photos=[("video", "video-uid-1")])

    bot = FakeMediaBot({"video-uid-1": video_bytes})
    app.state.bot = bot

    full = await client.get("/api/media/video-uid-1/video")
    assert full.status_code == 200
    assert full.headers["content-type"] == "video/mp4"
    assert full.content == video_bytes

    ranged = await client.get("/api/media/video-uid-1/video", headers={"Range": "bytes=100-199"})
    assert ranged.status_code == 206
    assert ranged.headers["content-range"] == f"bytes 100-199/{len(video_bytes)}"
    assert ranged.content == video_bytes[100:200]


async def test_video_preview_is_404_not_implemented(client):
    async with await _session() as session:
        await _add_item(session, public_id="MEDIA003", photos=[("video", "video-uid-2")])

    r = await client.get("/api/media/video-uid-2/preview")
    assert r.status_code == 404


async def test_unknown_file_unique_id_is_404(client):
    r = await client.get("/api/media/does-not-exist/preview")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Free price range, faceted tag counts, price histogram (stage 8c)
# ---------------------------------------------------------------------------


async def test_price_range_filters_items_and_count_combined_with_tags(client):
    async with await _session() as session:
        await _add_item(session, public_id="PRICE020", price=20_000, device_type="phone")
        await _add_item(session, public_id="PRICE045", price=45_000, device_type="phone")
        await _add_item(session, public_id="PRICE070", price=70_000, device_type="tablet", model="iPad Air")
        await _add_item(session, public_id="PRICE150", price=150_000, device_type="phone")

    r = await client.get("/api/items", params={"price_min": 30_000, "price_max": 100_000})
    assert r.status_code == 200
    body = r.json()
    assert {it["public_id"] for it in body["items"]} == {"PRICE045", "PRICE070"}
    assert body["total"] == 2

    # bounds are inclusive
    r = await client.get("/api/items", params={"price_min": 45_000, "price_max": 45_000})
    assert [it["public_id"] for it in r.json()["items"]] == ["PRICE045"]

    # AND with tags
    r = await client.get("/api/items/count", params={"tags": "device:phone", "price_min": 30_000})
    assert r.json() == {"total": 2}
    r = await client.get("/api/items/count", params={"tags": "device:phone", "price_max": 30_000})
    assert r.json() == {"total": 1}

    # one-sided and absent params keep the old behaviour
    r = await client.get("/api/items/count")
    assert r.json() == {"total": 4}

    r = await client.get("/api/items", params={"price_min": -1})
    assert r.status_code == 422


async def test_tags_facets_keep_zero_count_values_and_ignore_own_kind(client):
    async with await _session() as session:
        await _add_item(session, public_id="FACETA01", device_type="phone", grade="A", price=20_000)
        await _add_item(session, public_id="FACETB01", device_type="phone", grade="B", price=50_000)
        await _add_item(session, public_id="FACETT01", device_type="tablet", model="iPad Air", grade="B", price=70_000)
        await _add_item(session, public_id="FACETS01", device_type="watch", model="Apple Watch Series 10", status="sold")

    r = await client.get("/api/tags", params={"tags": "grade:a"})
    assert r.status_code == 200
    groups = {g["kind"]: {t["slug"]: t["count"] for t in g["tags"]} for g in r.json()}
    # tablet has no grade A lot: listed with 0 (dimmed in UI), not omitted
    assert groups["device"] == {"device:phone": 1, "device:tablet": 0}
    # the grade group ignores its own selection, so siblings keep real counts
    assert groups["grade"] == {"grade:a": 1, "grade:b": 2}
    # sold-only values never appear
    assert "device:watch" not in groups["device"]

    # price range narrows other kinds but not the price bands themselves
    r = await client.get("/api/tags", params={"price_min": 40_000})
    groups = {g["kind"]: {t["slug"]: t["count"] for t in g["tags"]} for g in r.json()}
    assert groups["device"] == {"device:phone": 1, "device:tablet": 1}
    assert groups["grade"] == {"grade:a": 0, "grade:b": 2}
    assert groups["price_band"] == {"price:0-30k": 1, "price:30-60k": 1, "price:60-100k": 1}

    # no params: unchanged plain counts
    r = await client.get("/api/tags")
    groups = {g["kind"]: {t["slug"]: t["count"] for t in g["tags"]} for g in r.json()}
    assert groups["device"] == {"device:phone": 2, "device:tablet": 1}


async def test_price_histogram_buckets_follow_other_filters(client):
    async with await _session() as session:
        await _add_item(session, public_id="HISTP001", device_type="phone", price=14_000)
        await _add_item(session, public_id="HISTP002", device_type="phone", price=38_000)
        await _add_item(session, public_id="HISTP003", device_type="phone", price=165_000)
        await _add_item(session, public_id="HISTT001", device_type="tablet", model="iPad Air", price=52_000)
        await _add_item(session, public_id="HISTS001", device_type="phone", price=999_000, status="sold")

    r = await client.get("/api/items/price-histogram")
    assert r.status_code == 200
    body = r.json()
    assert body["min"] == 14_000 and body["max"] == 165_000
    buckets = body["buckets"]
    assert 10 <= len(buckets) <= 12
    assert set(buckets[0]) == {"from", "to", "count"}
    assert buckets[0]["from"] <= 14_000 and buckets[-1]["to"] >= 165_000
    assert sum(b["count"] for b in buckets) == 4
    steps = {b["to"] - b["from"] for b in buckets}
    assert len(steps) == 1  # equal buckets
    for a, b in zip(buckets, buckets[1:]):
        assert a["to"] == b["from"]

    # other filters apply; price tags are ignored (the slider owns the price)
    r = await client.get("/api/items/price-histogram", params={"tags": "device:phone,price:0-30k"})
    body = r.json()
    assert body["min"] == 14_000 and body["max"] == 165_000
    assert sum(b["count"] for b in body["buckets"]) == 3

    r = await client.get("/api/items/price-histogram", params={"tags": "device:laptop"})
    assert r.json() == {"min": None, "max": None, "buckets": []}

    # a single price still yields a usable range
    r = await client.get("/api/items/price-histogram", params={"tags": "device:tablet"})
    body = r.json()
    assert body["min"] == body["max"] == 52_000
    assert sum(b["count"] for b in body["buckets"]) == 1


# ---------------------------------------------------------------------------
# /api/menu (catalog palette)
# ---------------------------------------------------------------------------


async def test_menu_groups_active_lots_by_category_with_model_stats(client):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with await _session() as session:
        await _add_item(session, public_id="IP15A", model="iPhone 15 Pro", price=72_000, storage_gb=128, grade="B",
                        color="Natural Titanium", published_at=base)
        await _add_item(session, public_id="IP15B", model="iPhone 15 Pro", price=84_000, storage_gb=512, grade="A",
                        color="White Titanium", published_at=base + timedelta(minutes=2),
                        photos=[("video", "IP15B-v"), ("photo", "IP15B-p")])
        await _add_item(session, public_id="IP17", model="iPhone 17 Pro", price=118_000, storage_gb=256, grade="A",
                        published_at=base + timedelta(minutes=1))
        await _add_item(session, public_id="MBA", model="MacBook Air 13 M4", device_type="laptop", category="macbook",
                        price=90_000, storage_gb=256, variant="16 ГБ", grade="A")
        await _add_item(session, public_id="WTCH", model="Apple Watch Ultra 2", device_type="watch", category="watch",
                        price=54_000, storage_gb=None, variant="49 мм", grade="C")
        await _add_item(session, public_id="S24", brand="Samsung", model="Galaxy S24", price=48_000, grade="B")
        await _add_item(session, public_id="SOLD1", model="iPhone 13", status="sold")
        await _add_item(session, public_id="DRFT1", model="iPhone 13", status="draft")

    r = await client.get("/api/menu")
    assert r.status_code == 200
    cats = r.json()["categories"]
    # Fixed order, empty sections (ipad, airpods) omitted, sold/draft ignored.
    assert [c["code"] for c in cats] == ["iphone", "macbook", "watch", "other"]
    assert [c["label"] for c in cats] == ["iPhone", "Mac", "Watch", "Другие устройства"]
    assert [c["device_type"] for c in cats] == ["phone", "laptop", "watch", None]

    iphone = cats[0]
    assert iphone["count"] == 3
    assert iphone["grades"] == {"A": 2, "B": 1, "C": 0}
    # Catalog order: newest model first.
    assert [m["label"] for m in iphone["models"]] == ["iPhone 17 Pro", "iPhone 15 Pro"]
    pro15 = iphone["models"][1]
    assert pro15["slug"] == "model:iphone-15-pro"
    assert pro15["count"] == 2
    assert (pro15["price_min"], pro15["price_max"]) == (72_000, 84_000)
    assert pro15["storages"] == [128, 512]
    assert pro15["grades"] == {"A": 1, "B": 1, "C": 0}
    # Newest lot of the model: its first photo (a leading video is skipped) and colour.
    assert pro15["color"] == "White Titanium"
    assert pro15["cover"].endswith("/api/media/IP15B-p/preview")
    assert pro15["device_type"] == "phone"

    mac = cats[1]["models"][0]
    assert (mac["storages"], mac["variants"]) == ([256], ["16 ГБ"])
    watch = cats[2]["models"][0]
    assert (watch["storages"], watch["variants"]) == ([], ["49 мм"])

    other = cats[3]
    assert other["models"][0]["label"] == "Samsung Galaxy S24"
    assert other["models"][0]["slug"] == "model:samsung-galaxy-s24"


async def test_menu_model_slugs_filter_to_the_same_counts(client):
    async with await _session() as session:
        await _add_item(session, public_id="A1", model="iPhone 15 Pro")
        await _add_item(session, public_id="A2", model="iPhone 15 Pro")
        await _add_item(session, public_id="A3", model="iPhone 15")
        await _add_item(session, public_id="S1", brand="Samsung", model="Samsung Galaxy S24")

    cats = (await client.get("/api/menu")).json()["categories"]
    for cat in cats:
        for m in cat["models"]:
            r = await client.get("/api/items/count", params={"tags": m["slug"]})
            assert r.json()["total"] == m["count"], m["slug"]
    # The brand is not doubled when the typed model already starts with it.
    assert cats[-1]["models"][0]["label"] == "Samsung Galaxy S24"


async def test_menu_empty_catalog(client):
    r = await client.get("/api/menu")
    assert r.status_code == 200
    assert r.json() == {"categories": []}
