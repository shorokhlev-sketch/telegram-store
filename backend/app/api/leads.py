"""POST /api/items/{public_id}/lead: buyer requests to be put in touch with
the seller, from either the Mini App (Telegram initData) or the plain site
(name + contact form)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session, optional_tg_user
from app.config import get_settings
from app.db.models import LeadSource
from app.services import leads as leads_service

router = APIRouter(prefix="/api")

SUCCESS_MESSAGE = "Заявка отправлена, продавец свяжется с вами"
RATE_LIMIT_MESSAGE = "Слишком много заявок, попробуйте позже"


class LeadIn(BaseModel):
    name: str | None = None
    contact: str | None = None
    phone: str | None = None
    website: str | None = None  # honeypot: real users never fill this


class LeadOut(BaseModel):
    ok: bool
    message: str


def _client_ip(request: Request) -> str:
    # uvicorn --proxy-headers already resolves the real client from the trusted
    # proxy's X-Forwarded-For; reading the header ourselves would let a client
    # spoof its first hop and dodge the rate limit.
    return request.client.host if request.client else "unknown"


@router.post("/items/{public_id}/lead", response_model=LeadOut)
async def create_lead(
    public_id: str,
    body: LeadIn,
    request: Request,
    tg_user: dict | None = Depends(optional_tg_user),
    session: AsyncSession = Depends(get_session),
) -> LeadOut:
    item = await leads_service.find_item(session, public_id)
    if item is None:
        raise HTTPException(status_code=404, detail="lot not found")
    if not leads_service.is_open(item):
        raise HTTPException(status_code=410, detail="lot is sold")

    settings = get_settings()
    bot = request.app.state.bot

    if tg_user is not None:
        await leads_service.create_app_lead(
            session, bot, settings, item, user=tg_user.get("user") or {}, phone=body.phone
        )
        return LeadOut(ok=True, message=SUCCESS_MESSAGE)

    website = (body.website or "").strip()
    if website:
        # Honeypot tripped: pretend success, store and send nothing.
        return LeadOut(ok=True, message=SUCCESS_MESSAGE)

    if not leads_service.check_site_rate_limit(_client_ip(request)):
        raise HTTPException(status_code=429, detail=RATE_LIMIT_MESSAGE)

    name = (body.name or "").strip()
    contact = (body.contact or "").strip()
    if not (1 <= len(name) <= 128) or not (3 <= len(contact) <= 128):
        raise HTTPException(status_code=422, detail="Укажите имя и контакт (телефон или @ник)")

    await leads_service.create_lead(
        session, bot, settings, item, source=LeadSource.site.value, name=name, contact=contact
    )
    return LeadOut(ok=True, message=SUCCESS_MESSAGE)
