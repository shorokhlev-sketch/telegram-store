"""Admin endpoints for the AdminPage Mini App screen: list lots, mark sold,
edit any lot field (validated like the bot wizard, see `services/lot_edit`),
change the cover photo, reference data for the edit form. Every route requires a valid admin `X-Telegram-Init-Data`
(see `require_admin`): missing/invalid header -> 401, valid but non-admin ->
403.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from aiogram.types import InputMediaPhoto
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_session, require_admin
from app.config import Settings, get_settings
from app.db.models import Item, ItemStatus, MessageKind
from app.services import drafts, lot_edit, post_render, publisher, sales, tags as tags_service
from app.services.publisher import _send_lock, _with_retry

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])

_LIST_STATUSES = {ItemStatus.active.value, ItemStatus.sold.value, ItemStatus.draft.value}


# ---------------------------------------------------------------------------
# Response / request models
# ---------------------------------------------------------------------------


class MeOut(BaseModel):
    ok: bool
    user_id: int


class AdminPhotoOut(BaseModel):
    file_unique_id: str
    preview: str


class AdminItemOut(BaseModel):
    public_id: str
    title: str
    price: int | None
    note: str | None
    status: str
    published_at: datetime | None
    sold_at: datetime | None
    cover: str | None
    lead_count: int
    # Every field the edit form shows (same meaning as the items columns).
    category: str
    device_type: str | None
    brand: str | None
    model: str | None
    year: int | None
    storage_gb: int | None
    variant: str | None
    color: str | None
    battery_health: int | None
    grade: str | None
    kit: list[str]
    # Photos only (videos can't be the cover), display order: the first is the cover.
    photos: list[AdminPhotoOut]


class AdminItemDetailOut(AdminItemOut):
    channel_updated: bool


class CoverIn(BaseModel):
    file_unique_id: str


class SoldOut(BaseModel):
    public_id: str
    already_sold: bool
    failed_message_ids: list[int]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _preview_url(settings: Settings, file_unique_id: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/api/media/{file_unique_id}/preview"


def _cover_url(settings: Settings, item: Item) -> str | None:
    cover = publisher.cover_photo(item)
    return _preview_url(settings, cover.file_unique_id) if cover else None


def _to_admin_item(settings: Settings, item: Item) -> AdminItemOut:
    return AdminItemOut(
        public_id=item.public_id,
        title=post_render.item_title(item),
        price=item.price,
        note=item.note,
        status=item.status,
        published_at=item.published_at,
        sold_at=item.sold_at,
        cover=_cover_url(settings, item),
        lead_count=len(item.leads),
        category=lot_edit.effective_category(item),
        device_type=item.device_type,
        brand=item.brand,
        model=item.model,
        year=item.year,
        storage_gb=item.storage_gb,
        variant=item.variant,
        color=item.color,
        battery_health=item.battery_health,
        grade=item.grade,
        kit=lot_edit.kit_list(item),
        photos=[
            AdminPhotoOut(file_unique_id=p.file_unique_id, preview=_preview_url(settings, p.file_unique_id))
            for p in drafts.photo_media(item)
        ],
    )


async def _get_item(session: AsyncSession, public_id: str) -> Item:
    item = await session.scalar(
        select(Item)
        .where(Item.public_id == public_id)
        .options(
            selectinload(Item.photos),
            selectinload(Item.channel_messages),
            selectinload(Item.leads),
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="lot not found")
    return item


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/me", response_model=MeOut)
async def me(admin: dict = Depends(require_admin)) -> MeOut:
    user = admin.get("user") or {}
    return MeOut(ok=True, user_id=int(user["id"]))


@router.get("/items", response_model=list[AdminItemOut])
async def list_admin_items(
    status: str = Query(default=ItemStatus.active.value),
    session: AsyncSession = Depends(get_session),
) -> list[AdminItemOut]:
    if status not in _LIST_STATUSES:
        raise HTTPException(status_code=422, detail="unknown status")
    settings = get_settings()
    stmt = (
        select(Item)
        .where(Item.status == status)
        .options(
            selectinload(Item.photos),
            selectinload(Item.leads),
        )
        .order_by(Item.id.desc())
    )
    rows = list((await session.scalars(stmt)).all())
    return [_to_admin_item(settings, item) for item in rows]


@router.post("/items/{public_id}/sold", response_model=SoldOut)
async def mark_item_sold(
    public_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> SoldOut:
    settings = get_settings()
    item = await _get_item(session, public_id)
    bot = request.app.state.bot
    try:
        result = await sales.mark_sold(session, bot, settings, item)
    except sales.SalesError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return SoldOut(
        public_id=result.public_id,
        already_sold=result.already_sold,
        failed_message_ids=result.failed_message_ids,
    )


# Cache-friendly: static per deploy except the year limit, which only moves on
# New Year. `private` because the route sits behind the admin header.
CATALOG_CACHE_CONTROL = "private, max-age=3600"


@router.get("/catalog")
async def admin_catalog(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = CATALOG_CACHE_CONTROL
    return lot_edit.catalog_payload()


@router.patch("/items/{public_id}", response_model=AdminItemDetailOut)
async def patch_item(
    public_id: str,
    request: Request,
    body: dict[str, Any] = Body(...),
    session: AsyncSession = Depends(get_session),
):
    """Partial update of an active lot. Body: any of `lot_edit.EDITABLE_FIELDS`.
    Invalid input -> 422 `{"detail": {"field", "message"}}` (Russian message);
    sold/draft -> 409. After commit: tags re-synced, channel caption and
    buttons re-rendered."""
    settings = get_settings()
    item = await _get_item(session, public_id)
    if item.status == ItemStatus.sold.value:
        raise HTTPException(status_code=409, detail="Лот уже продан")
    if item.status != ItemStatus.active.value:
        raise HTTPException(status_code=409, detail="Лот ещё не опубликован")

    try:
        lot_edit.apply_edit(item, body)
    except lot_edit.LotEditError as exc:
        return JSONResponse(status_code=422, content={"detail": {"field": exc.field, "message": exc.message}})
    # Any field can move a tag (model, storage, color, battery band, price band...).
    await tags_service.sync_item_tags(session, item)
    await session.commit()
    await session.refresh(item, ["photos", "channel_messages", "leads"])

    channel_updated = await _update_channel_post(request.app.state.bot, settings, item)
    return AdminItemDetailOut(**_to_admin_item(settings, item).model_dump(), channel_updated=channel_updated)


@router.post("/items/{public_id}/cover", response_model=AdminItemDetailOut)
async def set_cover(
    public_id: str,
    body: CoverIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Make one of the lot's photos the cover (first media). The channel post
    swaps its photo in place (`edit_message_media`, caption and buttons kept)."""
    settings = get_settings()
    item = await _get_item(session, public_id)
    if item.status == ItemStatus.sold.value:
        raise HTTPException(status_code=409, detail="Лот уже продан")
    if item.status != ItemStatus.active.value:
        raise HTTPException(status_code=409, detail="Лот ещё не опубликован")
    media = next((m for m in item.photos if m.file_unique_id == body.file_unique_id), None)
    if media is None:
        return JSONResponse(status_code=422, content={"detail": {"field": "file_unique_id", "message": "У этого лота нет такого фото"}})
    if media not in drafts.photo_media(item):
        return JSONResponse(status_code=422, content={"detail": {"field": "file_unique_id", "message": "Видео не может быть обложкой"}})

    before = publisher.cover_photo(item)
    drafts.move_to_front(item, media)
    await session.commit()
    await session.refresh(item, ["photos", "channel_messages", "leads"])

    channel_updated = True
    if before is None or before.id != media.id:
        channel_updated = await _update_channel_post(request.app.state.bot, settings, item, with_photo=True)
    return AdminItemDetailOut(**_to_admin_item(settings, item).model_dump(), channel_updated=channel_updated)


async def _update_channel_post(bot, settings: Settings, item: Item, *, with_photo: bool = False) -> bool:
    """Re-render the channel post of `item` in place: caption (and the photo
    when `with_photo`), buttons passed again or Telegram drops them. "message is
    not modified" is success; any other Telegram error -> False (DB stays)."""
    caption = post_render.render_caption(item)
    keyboard = publisher.post_keyboard(settings, item)
    cover = publisher.cover_photo(item)
    ok = True
    for cm in item.channel_messages:
        if cm.kind != MessageKind.media.value:
            continue
        if with_photo and cover is not None:
            call = lambda cm=cm: bot.edit_message_media(  # noqa: E731
                chat_id=cm.chat_id,
                message_id=cm.message_id,
                media=InputMediaPhoto(media=cover.tg_file_id, caption=caption, parse_mode="HTML"),
                reply_markup=keyboard,
            )
        else:
            call = lambda cm=cm: bot.edit_message_caption(  # noqa: E731
                chat_id=cm.chat_id,
                message_id=cm.message_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        try:
            async with _send_lock:
                await _with_retry(call)
        except Exception as exc:  # noqa: BLE001 - Telegram errors vary by version/transport
            if "message is not modified" in str(exc).lower():
                continue
            log.warning("channel post update failed for item %s message %s: %s", item.public_id, cm.message_id, exc)
            ok = False
    return ok
