"""Public storefront API: listing with tag filters, a lot's full card, tag
counts for the filter chips, and cached media (photos/video) by
file_unique_id.
"""

from __future__ import annotations

import base64
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_session
from app.config import Settings, get_settings
from app.db.models import Item, ItemStatus, ItemTag, MediaKind, Photo, Tag, TagKind
from app.services import catalog, media, post_render, publisher
from app.services import tags as tags_service

router = APIRouter(prefix="/api")

GROUP_LABELS: dict[str, str] = {
    TagKind.device.value: "Устройство",
    TagKind.brand.value: "Производитель",
    TagKind.model.value: "Модель",
    TagKind.storage.value: "Память",
    TagKind.color.value: "Цвет",
    TagKind.grade.value: "Состояние",
    TagKind.battery_band.value: "Батарея",
    TagKind.kit.value: "Комплект",
    TagKind.price_band.value: "Цена",
}


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ItemCardOut(BaseModel):
    public_id: str
    title: str
    device_type: str | None
    brand: str | None
    model: str | None
    storage_gb: int | None
    variant: str | None
    color: str | None
    battery_health: int | None
    grade: str | None
    price: int | None
    cover: str | None
    has_video: bool
    published_at: datetime | None
    note: str | None
    grade_label: str | None
    seller_url: str | None


class ItemsListOut(BaseModel):
    items: list[ItemCardOut]
    next_cursor: str | None
    total: int


class ItemsCountOut(BaseModel):
    total: int


class KitOut(BaseModel):
    code: str
    label: str


class MediaOut(BaseModel):
    kind: str
    preview: str | None = None
    full: str | None = None
    video: str | None = None
    duration: int | None = None


class ItemDetailOut(ItemCardOut):
    year: int | None
    kit: list[KitOut]
    grade_description: str | None
    media: list[MediaOut]
    miniapp_url: str


class TagOut(BaseModel):
    slug: str
    label: str
    count: int


class TagGroupOut(BaseModel):
    kind: str
    label: str
    tags: list[TagOut]


class PriceBucketOut(BaseModel):
    # Half-open [from, to); the last bucket also includes `to`.
    from_: int = Field(serialization_alias="from")
    to: int
    count: int


class PriceHistogramOut(BaseModel):
    min: int | None
    max: int | None
    buckets: list[PriceBucketOut]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _media_base_url(settings: Settings, file_unique_id: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/api/media/{file_unique_id}"


def _cover_url(settings: Settings, item: Item) -> str | None:
    cover = publisher.cover_photo(item)
    if cover is None:
        return None
    return f"{_media_base_url(settings, cover.file_unique_id)}/preview"


def _has_video(item: Item) -> bool:
    return any((p.kind or MediaKind.photo.value) == MediaKind.video.value for p in item.photos)


def _grade_label(item: Item) -> str | None:
    grade_spec = catalog.get_grade(item.grade) if item.grade else None
    return grade_spec.label if grade_spec else None


def _seller_url(settings: Settings, item: Item) -> str | None:
    return settings.seller_chat_url(publisher.seller_message_text(item))


def _to_card(settings: Settings, item: Item) -> ItemCardOut:
    return ItemCardOut(
        public_id=item.public_id,
        title=post_render.item_title(item),
        device_type=item.device_type,
        brand=item.brand,
        model=item.model,
        storage_gb=item.storage_gb,
        variant=item.variant,
        color=item.color,
        battery_health=item.battery_health,
        grade=item.grade,
        price=item.price,
        cover=_cover_url(settings, item),
        has_video=_has_video(item),
        published_at=item.published_at,
        note=item.note,
        grade_label=_grade_label(item),
        seller_url=_seller_url(settings, item),
    )


def _encode_cursor(published_at: datetime, item_id: int) -> str:
    raw = f"{published_at.isoformat()}|{item_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        iso, id_str = raw.rsplit("|", 1)
        return datetime.fromisoformat(iso), int(id_str)
    except Exception as exc:  # noqa: BLE001 - any malformed cursor is a 400
        raise HTTPException(status_code=400, detail="bad cursor") from exc


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _parse_tags(tags: str | None) -> list[str]:
    return [s.strip() for s in tags.split(",") if s.strip()] if tags else []


def _active_tag_conditions(
    tags: str | None,
    price_min: int | None = None,
    price_max: int | None = None,
) -> list:
    """WHERE conditions for "active items matching this tag filter and price
    range", shared by the listing's `total` and the standalone count endpoint.
    Tags: AND across kinds, OR within a kind; the price range is ANDed on top."""
    conditions = [Item.status == ItemStatus.active.value]
    groups = tags_service.group_by_kind(_parse_tags(tags))
    for kind_slugs in groups.values():
        subq = select(ItemTag.item_id).join(Tag, Tag.id == ItemTag.tag_id).where(Tag.slug.in_(kind_slugs))
        conditions.append(Item.id.in_(subq))
    if price_min is not None:
        conditions.append(Item.price >= price_min)
    if price_max is not None:
        conditions.append(Item.price <= price_max)
    return conditions


async def _count_active(
    session: AsyncSession,
    tags: str | None,
    price_min: int | None = None,
    price_max: int | None = None,
) -> int:
    stmt = select(func.count()).select_from(Item).where(*_active_tag_conditions(tags, price_min, price_max))
    return int(await session.scalar(stmt) or 0)


# Free price range (₽), optional on every listing/count/facet endpoint.
PriceMin = Query(default=None, ge=0)
PriceMax = Query(default=None, ge=0)


@router.get("/items", response_model=ItemsListOut)
async def list_items(
    tags: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    price_min: int | None = PriceMin,
    price_max: int | None = PriceMax,
    session: AsyncSession = Depends(get_session),
) -> ItemsListOut:
    settings = get_settings()
    conditions = _active_tag_conditions(tags, price_min, price_max)
    stmt = select(Item).where(*conditions)

    if cursor:
        pub_dt, cid = _decode_cursor(cursor)
        stmt = stmt.where(
            or_(
                Item.published_at < pub_dt,
                and_(Item.published_at == pub_dt, Item.id < cid),
            )
        )

    stmt = (
        stmt.order_by(Item.published_at.desc(), Item.id.desc())
        .options(selectinload(Item.photos))
        .limit(limit + 1)
    )
    rows = list((await session.scalars(stmt)).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = _encode_cursor(rows[-1].published_at, rows[-1].id) if has_more and rows else None
    total = await _count_active(session, tags, price_min, price_max)
    return ItemsListOut(items=[_to_card(settings, item) for item in rows], next_cursor=next_cursor, total=total)


@router.get("/items/count", response_model=ItemsCountOut)
async def count_items(
    tags: str | None = Query(default=None),
    price_min: int | None = PriceMin,
    price_max: int | None = PriceMax,
    session: AsyncSession = Depends(get_session),
) -> ItemsCountOut:
    """Cheap COUNT for the filter sheet preview («Показать N предложений»),
    sharing the filter-building code with `/items`'s `total`."""
    return ItemsCountOut(total=await _count_active(session, tags, price_min, price_max))


HISTOGRAM_BUCKETS = 12
_NICE_STEPS = (1_000, 2_000, 2_500, 5_000)


def _nice_step(span: int, buckets: int) -> int:
    """Smallest 1/2/2.5/5 x 10^n step (>= 1 000 ₽) so `buckets` steps cover `span`."""
    magnitude = 1
    while True:
        for base in _NICE_STEPS:
            step = base * magnitude
            if step * buckets >= span:
                return step
        magnitude *= 10


@router.get("/items/price-histogram", response_model=PriceHistogramOut, response_model_by_alias=True)
async def price_histogram(
    tags: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> PriceHistogramOut:
    """Price distribution of active lots matching the *other* filters (price
    tags in `tags` are ignored), for the histogram over the price slider.
    `min`/`max` are the real extreme prices; buckets are 12 equal "nice" steps
    starting at `min` rounded down to the step."""
    slugs = [s for s in _parse_tags(tags) if tags_service.kind_of(s) != TagKind.price_band.value]
    conditions = _active_tag_conditions(",".join(slugs))
    prices = [
        int(p)
        for p in (await session.scalars(select(Item.price).where(*conditions, Item.price.is_not(None)))).all()
    ]
    if not prices:
        return PriceHistogramOut(min=None, max=None, buckets=[])
    lo_price, hi_price = min(prices), max(prices)
    step = _nice_step(hi_price - lo_price, HISTOGRAM_BUCKETS)
    start = (lo_price // step) * step
    counts = [0] * HISTOGRAM_BUCKETS
    for p in prices:
        counts[min(HISTOGRAM_BUCKETS - 1, (p - start) // step)] += 1
    buckets = [
        PriceBucketOut(from_=start + i * step, to=start + (i + 1) * step, count=c) for i, c in enumerate(counts)
    ]
    return PriceHistogramOut(min=lo_price, max=hi_price, buckets=buckets)


@router.get("/items/{public_id}", response_model=ItemDetailOut)
async def get_item(public_id: str, session: AsyncSession = Depends(get_session)) -> ItemDetailOut:
    settings = get_settings()
    item = await session.scalar(
        select(Item).where(Item.public_id == public_id).options(selectinload(Item.photos))
    )
    if item is None or item.status == ItemStatus.draft.value:
        raise HTTPException(status_code=404, detail="lot not found")
    if item.status == ItemStatus.sold.value:
        raise HTTPException(status_code=410, detail="lot is sold")

    base_url = settings.public_base_url.rstrip("/")
    media_items: list[MediaOut] = []
    for p in item.photos:
        file_base = f"{base_url}/api/media/{p.file_unique_id}"
        if (p.kind or MediaKind.photo.value) == MediaKind.video.value:
            media_items.append(MediaOut(kind="video", video=f"{file_base}/video", duration=p.duration))
        else:
            media_items.append(MediaOut(kind="photo", preview=f"{file_base}/preview", full=f"{file_base}/full"))

    kit_items = [
        KitOut(code=code, label=catalog.get_kit_item(code).label)
        for code in tags_service.parse_kit(item.kit_json)
        if catalog.get_kit_item(code)
    ]
    grade_spec = catalog.get_grade(item.grade) if item.grade else None

    card = _to_card(settings, item)
    return ItemDetailOut(
        **card.model_dump(),
        year=item.year,
        kit=kit_items,
        grade_description=grade_spec.description if grade_spec else None,
        media=media_items,
        miniapp_url=settings.miniapp_item_url(item.public_id),
    )


@router.get("/tags", response_model=list[TagGroupOut])
async def list_tags(
    tags: str | None = Query(default=None),
    price_min: int | None = PriceMin,
    price_max: int | None = PriceMax,
    session: AsyncSession = Depends(get_session),
) -> list[TagGroupOut]:
    """Tag groups with counts of active lots.

    Without parameters: plain counts, tags with zero active lots omitted.
    With `tags` and/or `price_min`/`price_max`: faceted counts for the current
    selection. Every tag that has at least one active lot is still listed (so
    the UI can dim instead of hide), and its count is the number of lots that
    match the selection in all *other* kinds (a tag's own kind is ignored, as
    OR inside a kind would otherwise zero out siblings). The price range
    counts as the `price_band` kind.
    """
    stmt = (
        select(ItemTag.item_id, Tag.slug, Tag.label, Tag.kind)
        .join(Tag, Tag.id == ItemTag.tag_id)
        .join(Item, Item.id == ItemTag.item_id)
        .where(Item.status == ItemStatus.active.value)
    )
    rows = (await session.execute(stmt)).all()

    faceted = bool(_parse_tags(tags)) or price_min is not None or price_max is not None
    pools: dict[str, set[int] | None] = {}
    tag_info: dict[str, tuple[str, str]] = {}
    holders: dict[str, set[int]] = {}
    for item_id, slug, label, kind in rows:
        tag_info[slug] = (label, kind)
        holders.setdefault(slug, set()).add(item_id)

    if faceted:
        # Items passing each selected kind (and the price range) on their own.
        passing: dict[str, set[int]] = {}
        for kind, slugs in tags_service.group_by_kind(_parse_tags(tags)).items():
            passing[kind] = set().union(*(holders.get(s, set()) for s in slugs))
        if price_min is not None or price_max is not None:
            price_ok = set(
                (
                    await session.scalars(
                        select(Item.id).where(*_active_tag_conditions(None, price_min, price_max))
                    )
                ).all()
            )
            passing[TagKind.price_band.value] = passing.get(TagKind.price_band.value, price_ok) & price_ok

        def _facet_pool(kind: str) -> set[int] | None:
            pool: set[int] | None = None
            for k, ids in passing.items():
                if k == kind:
                    continue
                pool = set(ids) if pool is None else pool & ids
            return pool

        pools = {kind: _facet_pool(kind) for kind in tags_service.KIND_ORDER}

    by_kind: dict[str, list[TagOut]] = {}
    for slug, ids in holders.items():
        label, kind = tag_info[slug]
        pool = pools.get(kind)
        count = len(ids if pool is None else ids & pool)
        by_kind.setdefault(kind, []).append(TagOut(slug=slug, label=label, count=count))

    price_order = {code: i for i, (_, _, code, _) in enumerate(tags_service.PRICE_BANDS)}
    # Sorting by count would reshuffle rows as filters change; faceted lists
    # keep the global (unfiltered) popularity order instead.
    popularity = {slug: len(ids) for slug, ids in holders.items()}

    def _sorted(kind: str, items: list[TagOut]) -> list[TagOut]:
        if kind == TagKind.storage.value:
            return sorted(items, key=lambda t: int(t.slug.split(":", 1)[1]))
        if kind == TagKind.price_band.value:
            return sorted(items, key=lambda t: price_order.get(t.slug.split(":", 1)[1], len(price_order)))
        return sorted(items, key=lambda t: (-popularity[t.slug], t.label))

    result: list[TagGroupOut] = []
    for kind in tags_service.KIND_ORDER:
        items = by_kind.get(kind, [])
        if not items:
            continue
        result.append(TagGroupOut(kind=kind, label=GROUP_LABELS[kind], tags=_sorted(kind, items)))
    return result


# ---------------------------------------------------------------------------
# /api/menu: catalog palette (categories -> models with counts and prices)
# ---------------------------------------------------------------------------

# Storefront sections in fixed order: Apple catalog categories, then the rest.
MENU_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("iphone", "iPhone"),
    ("ipad", "iPad"),
    ("macbook", "Mac"),
    ("watch", "Watch"),
    ("airpods", "AirPods"),
    ("other", "Другие устройства"),
)
_GRADES = ("A", "B", "C")


class MenuModelOut(BaseModel):
    slug: str  # == the `model:*` tag slug the filters use
    label: str
    count: int
    price_min: int | None
    price_max: int | None
    storages: list[int]
    variants: list[str]
    grades: dict[str, int]
    # Newest lot of the model: its cover (photo thumb) and device/colour (silhouette fallback).
    device_type: str | None
    color: str | None
    cover: str | None


class MenuCategoryOut(BaseModel):
    code: str
    label: str
    device_type: str | None
    count: int
    models: list[MenuModelOut]
    grades: dict[str, int]


class MenuOut(BaseModel):
    categories: list[MenuCategoryOut]


def _variant_key(v: str) -> tuple[float, str]:
    head = v.split()[0].replace(",", ".") if v.split() else ""
    try:
        return (float(head), v)
    except ValueError:
        return (float("inf"), v)


@router.get("/menu", response_model=MenuOut)
async def menu(session: AsyncSession = Depends(get_session)) -> MenuOut:
    """Palette data: per section (items.category, fixed order, empty ones
    omitted) the active lot count, condition split and models with count,
    price range, storage/variant options and condition split. Two queries:
    active lots, then the covers of each model's newest lot."""
    settings = get_settings()
    rows = (
        await session.scalars(
            select(Item)
            .where(Item.status == ItemStatus.active.value)
            .order_by(Item.published_at.desc(), Item.id.desc())
        )
    ).all()

    codes = {code for code, _ in MENU_CATEGORIES}
    cats: dict[str, dict] = {}
    models: dict[str, dict[str, dict]] = {}
    for item in rows:
        code = item.category if item.category in codes else "other"
        cat = cats.setdefault(code, {"count": 0, "grades": dict.fromkeys(_GRADES, 0)})
        cat["count"] += 1
        grade = (item.grade or "").upper()
        if grade in cat["grades"]:
            cat["grades"][grade] += 1
        tag = tags_service.model_tag_def(item)
        if tag is None:
            continue
        m = models.setdefault(code, {}).get(tag.slug)
        if m is None:
            # Rows come newest first: the first lot seen is the model's newest.
            m = models[code][tag.slug] = {
                "slug": tag.slug, "label": tag.label, "count": 0, "prices": [], "storages": set(),
                "variants": set(), "grades": dict.fromkeys(_GRADES, 0), "newest": item.id,
                "device_type": item.device_type, "color": item.color, "name": (item.model or "").strip(),
            }
        m["count"] += 1
        if item.price is not None:
            m["prices"].append(item.price)
        if item.storage_gb:
            m["storages"].add(item.storage_gb)
        if item.variant:
            m["variants"].add(item.variant.strip())
        if grade in m["grades"]:
            m["grades"][grade] += 1

    newest_ids = [m["newest"] for by_slug in models.values() for m in by_slug.values()]
    covers: dict[int, str] = {}
    if newest_ids:
        photo_rows = (
            await session.execute(
                select(Photo.item_id, Photo.file_unique_id, Photo.kind)
                .where(Photo.item_id.in_(newest_ids))
                .order_by(Photo.item_id, Photo.position)
            )
        ).all()
        for item_id, uid, kind in photo_rows:
            if item_id not in covers and (kind or MediaKind.photo.value) == MediaKind.photo.value:
                covers[item_id] = f"{_media_base_url(settings, uid)}/preview"

    result: list[MenuCategoryOut] = []
    for code, label in MENU_CATEGORIES:
        cat = cats.get(code)
        if not cat:
            continue
        spec = catalog.get_category(code)
        order = {m.name: i for i, m in enumerate(catalog.models_for(code))} if spec else {}
        by_slug = list(models.get(code, {}).values())
        if order:
            # Apple: catalog order (newest model first), unknown names after.
            by_slug.sort(key=lambda m: (order.get(m["name"], len(order)), m["label"]))
        else:
            by_slug.sort(key=lambda m: (-m["count"], m["label"]))
        result.append(
            MenuCategoryOut(
                code=code,
                label=label,
                device_type=spec.device_type if spec else None,
                count=cat["count"],
                grades=cat["grades"],
                models=[
                    MenuModelOut(
                        slug=m["slug"],
                        label=m["label"],
                        count=m["count"],
                        price_min=min(m["prices"]) if m["prices"] else None,
                        price_max=max(m["prices"]) if m["prices"] else None,
                        storages=sorted(m["storages"]),
                        variants=sorted(m["variants"], key=_variant_key),
                        grades=m["grades"],
                        device_type=m["device_type"],
                        color=m["color"],
                        cover=covers.get(m["newest"]),
                    )
                    for m in by_slug
                ],
            )
        )
    return MenuOut(categories=result)


@router.get("/media/{file_unique_id}/{size}")
async def get_media(
    file_unique_id: str,
    size: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    if size not in ("preview", "full", "video"):
        raise HTTPException(status_code=404, detail="unknown media size")

    photo = await session.scalar(select(Photo).where(Photo.file_unique_id == file_unique_id))
    if photo is None:
        raise HTTPException(status_code=404, detail="media not found")

    settings = get_settings()
    bot = request.app.state.bot
    kind = photo.kind or MediaKind.photo.value
    headers = {"Cache-Control": "public, max-age=31536000, immutable"}

    if kind == MediaKind.photo.value:
        if size == "video":
            raise HTTPException(status_code=404, detail="a photo has no video size")
        try:
            path = await media.ensure_photo(bot, settings, photo, size)
        except media.MediaError as exc:
            raise HTTPException(status_code=502, detail="failed to fetch media from Telegram") from exc
        return FileResponse(path, media_type="image/jpeg", headers=headers)

    # kind == video: only the "video" size is served. A video's poster/preview
    # isn't implemented yet (see services/media.py docstring) -> 404.
    if size != "video":
        raise HTTPException(status_code=404, detail="video preview is not available")
    try:
        path = await media.ensure_video(bot, settings, photo)
    except media.MediaError as exc:
        raise HTTPException(status_code=502, detail="failed to fetch media from Telegram") from exc
    return FileResponse(path, media_type="video/mp4", headers=headers)
