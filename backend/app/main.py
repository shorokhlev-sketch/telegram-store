import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.admin import router as admin_router
from app.api.leads import router as leads_router
from app.api.public import router as public_router
from app.bot.router import configure_bot_ui, create_bot, create_dispatcher
from app.config import get_settings
from app.db.session import dispose_engine, get_engine, get_session_factory
from app.services import backup, notify

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    get_engine()

    bot = create_bot(settings)
    dp = create_dispatcher(settings, get_session_factory())
    app.state.bot = bot
    app.state.dp = dp

    if settings.set_webhook_on_start:
        await bot.set_webhook(
            url=settings.webhook_url,
            secret_token=settings.webhook_secret,
            allowed_updates=["message", "callback_query"],
        )
        log.info("webhook set to %s", settings.webhook_url)
        await configure_bot_ui(bot, settings)  # menu button «Магазин» + commands; never raises

    backup_task: asyncio.Task | None = None
    if settings.backup_chat_id:
        backup_task = asyncio.create_task(backup.run_forever(bot, settings, get_session_factory()))
        app.state.backup_task = backup_task
        log.info("daily backup scheduled for %02d:00 UTC", settings.backup_hour_utc)

    yield

    if backup_task is not None:
        backup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await backup_task

    await notify.drain(timeout=10)  # let a running subscriber broadcast finish
    await bot.session.close()
    await dispose_engine()


app = FastAPI(title="Apple Store TG", lifespan=lifespan)
app.include_router(public_router)
app.include_router(leads_router)
app.include_router(admin_router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/tg/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> Response:
    settings = get_settings()
    if not settings.webhook_secret or x_telegram_bot_api_secret_token != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="bad secret")
    bot = request.app.state.bot
    dp = request.app.state.dp
    payload = await request.json()
    update = Update.model_validate(payload, context={"bot": bot})
    await dp.feed_update(bot, update)
    return Response(status_code=200)


# ---------------------------------------------------------------------------
# Storefront SPA (web/dist). Lab has no Caddy, so FastAPI serves the build.
#
# Implemented as a 404 handler rather than a catch-all route: routing for
# /api and /tg stays exactly as before (unknown /api/... is still a JSON 404,
# wrong methods still 405), and the build is looked up per request, so a fresh
# deploy of web/dist needs no restart.
# ---------------------------------------------------------------------------

_SPA_EXCLUDED_PREFIXES = ("/api/", "/tg/")
_ASSET_CACHE = "public, max-age=31536000, immutable"


def _spa_response(request: Request) -> Response | None:
    if request.method not in ("GET", "HEAD"):
        return None
    path = request.url.path
    if path in ("/api", "/tg") or path.startswith(_SPA_EXCLUDED_PREFIXES):
        return None
    dist: Path = get_settings().web_dist_dir
    index = dist / "index.html"
    if not index.is_file():
        return None

    root = dist.resolve()
    rel = path.lstrip("/")
    if rel:
        candidate = (root / rel).resolve()
        if candidate.is_relative_to(root) and candidate.is_file() and candidate.name != "index.html":
            cache = _ASSET_CACHE if rel.startswith("assets/") else "public, max-age=3600"
            return FileResponse(candidate, headers={"Cache-Control": cache})
        if rel.startswith("assets/"):
            return None  # a missing hashed asset is a real 404, not the app shell
    # index.html references hashed assets, so it must never be cached stale.
    return FileResponse(index, media_type="text/html", headers={"Cache-Control": "no-cache"})


@app.exception_handler(StarletteHTTPException)
async def _not_found_to_spa(request: Request, exc: StarletteHTTPException) -> Response:
    if exc.status_code == 404:
        spa = _spa_response(request)
        if spa is not None:
            return spa
    return await http_exception_handler(request, exc)
