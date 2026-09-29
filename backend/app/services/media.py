"""Media cache: download once from Telegram, resize photos, serve from disk.

`tg_file_id` is stored on `Photo`; the first request for a given size fetches
the source file via the Bot API (`get_file` + a streamed download) and writes
a resized JPEG (photos) or the raw file (videos) into `MEDIA_DIR`. Later
requests find the file already on disk. Writes are atomic (tmp file + os
rename) and a per-target-path asyncio.Lock stops two concurrent requests from
downloading/encoding the same file twice.

Video posters: Telegram's own thumbnail would make a good "preview" for a
video, but we don't store its file_id (only the video's), so a preview
request for a video row is refused by the API layer (404) for now; the
frontend shows a placeholder instead. No DB migration was added for this.
"""

from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path

from aiogram import Bot
from PIL import Image, ImageOps

from app.config import Settings
from app.db.models import Photo

log = logging.getLogger(__name__)

PREVIEW_MAX_SIDE = 400
FULL_MAX_SIDE = 1280
JPEG_QUALITY = 82

_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


class MediaError(Exception):
    """Wraps a Telegram/network failure while fetching source media (-> HTTP 502)."""


async def _lock_for(path: Path) -> asyncio.Lock:
    key = str(path)
    async with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _locks[key] = lock
        return lock


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


async def _download(bot: Bot, tg_file_id: str) -> bytes:
    try:
        file = await bot.get_file(tg_file_id)
        if file.file_path is None:
            raise MediaError("Telegram file has no file_path")
        buf = await bot.download_file(file.file_path)
    except MediaError:
        raise
    except Exception as exc:  # noqa: BLE001 - any Telegram/network error becomes a 502
        raise MediaError(str(exc)) from exc
    if buf is None:
        raise MediaError("empty download")
    return buf.read()


def _resize_jpeg(data: bytes, max_side: int) -> bytes:
    with Image.open(io.BytesIO(data)) as img:
        img = ImageOps.exif_transpose(img) or img
        img = img.convert("RGB")
        img.thumbnail((max_side, max_side), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=JPEG_QUALITY)
        return out.getvalue()


async def ensure_photo(bot: Bot, settings: Settings, photo: Photo, size: str) -> Path:
    """Path to the cached preview/full JPEG for `photo`, downloading+resizing on first use."""
    if size not in ("preview", "full"):
        raise ValueError(f"unknown photo size {size!r}")
    max_side = PREVIEW_MAX_SIDE if size == "preview" else FULL_MAX_SIDE
    path = settings.media_dir / f"{photo.file_unique_id}_{size}.jpg"
    if path.exists():
        return path
    lock = await _lock_for(path)
    async with lock:
        if path.exists():  # another request filled it while we waited for the lock
            return path
        raw = await _download(bot, photo.tg_file_id)
        jpeg = await asyncio.to_thread(_resize_jpeg, raw, max_side)
        await asyncio.to_thread(_atomic_write, path, jpeg)
        return path


async def ensure_video(bot: Bot, settings: Settings, photo: Photo) -> Path:
    """Path to the cached video file for `photo`, downloading (no transcoding) on first use."""
    path = settings.media_dir / f"{photo.file_unique_id}.mp4"
    if path.exists():
        return path
    lock = await _lock_for(path)
    async with lock:
        if path.exists():
            return path
        raw = await _download(bot, photo.tg_file_id)
        await asyncio.to_thread(_atomic_write, path, raw)
        return path
