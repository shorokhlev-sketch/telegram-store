"""Daily off-site backup: SQLite online backup (`sqlite3.Connection.backup`,
the API behind the `sqlite3 .backup` CLI command) -> gzip -> sent as a
Telegram document to `BACKUP_CHAT_ID`.

We use the backup API rather than copying `DB_PATH` because the app runs
SQLite in WAL mode: a plain file copy can grab the main db file mid-write and
miss committed data still sitting in the `-wal` file, producing a torn
snapshot. `Connection.backup()` merges main file + WAL correctly and gives a
consistent point-in-time copy without blocking writers for more than a
moment.

`run_forever` is the background task wired into `app/main.py`'s lifespan: it
sleeps until `BACKUP_HOUR_UTC` (once a day, computed fresh each iteration so
it self-corrects across DST-less UTC midnights) and calls `run_backup`. The
admin `/backup` command (`app/bot/admin.py`) calls `run_backup` directly for
an on-demand backup. Both share the same routine, so scheduled and manual
backups behave identically.

A failed backup is logged and never propagates: neither the scheduler loop
nor the app should ever go down because Telegram or disk I/O hiccuped.
"""

from __future__ import annotations

import asyncio
import gzip
import logging
import sqlite3
import tempfile
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot
from aiogram.types import BufferedInputFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import Item, ItemStatus

log = logging.getLogger(__name__)

FILENAME_FMT = "apple-store-tg-%Y%m%d-%H%M.db.gz"

_STATUS_LABELS = {
    ItemStatus.draft.value: "Черновики",
    ItemStatus.active.value: "Активные",
    ItemStatus.sold.value: "Продано",
}


def seconds_until_next_run(now: datetime, hour_utc: int) -> float:
    """Seconds from `now` (any tz, treated as an instant) until the next
    `hour_utc:00:00` UTC: later today if that hasn't happened yet, otherwise
    tomorrow. Handles the midnight wrap and the exact-hour edge (rolls to
    tomorrow rather than firing twice in the same minute)."""
    now_utc = now.astimezone(timezone.utc)
    target = now_utc.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if target <= now_utc:
        target += timedelta(days=1)
    return (target - now_utc).total_seconds()


def _sqlite_backup_sync(db_path: Path, dest_path: Path) -> None:
    """Blocking online backup; run this via asyncio.to_thread."""
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(dest_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _gzip_file_sync(src_path: Path, dest_path: Path) -> bytes:
    """Blocking: gzip `src_path` and return the compressed bytes (read back
    into memory so the caller can hand them to Telegram after the temp dir
    that held both files is gone)."""
    with open(src_path, "rb") as f_in:
        raw = f_in.read()
    with gzip.open(dest_path, "wb") as f_out:
        f_out.write(raw)
    return dest_path.read_bytes()


async def _status_counts(session: AsyncSession) -> dict[str, int]:
    rows = (await session.execute(select(Item.status, func.count(Item.id)).group_by(Item.status))).all()
    return {status: count for status, count in rows}


def _caption(counts: dict[str, int]) -> str:
    total = sum(counts.values())
    lines = ["Бэкап базы apple-store-tg", f"Всего лотов: {total}"]
    for status in (ItemStatus.draft.value, ItemStatus.active.value, ItemStatus.sold.value):
        lines.append(f"{_STATUS_LABELS[status]}: {counts.get(status, 0)}")
    return "\n".join(lines)


async def run_backup(
    bot: Bot,
    settings: Settings,
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> None:
    """Back up `settings.db_path` and send it to BACKUP_CHAT_ID. No-op when
    backups are disabled (BACKUP_CHAT_ID=0)."""
    if not settings.backup_chat_id:
        return

    moment = now or datetime.now(timezone.utc)
    filename = moment.strftime(FILENAME_FMT)
    counts = await _status_counts(session)
    caption = _caption(counts)

    with tempfile.TemporaryDirectory(prefix="apple-store-tg-backup-") as tmp:
        raw_path = Path(tmp) / "app.db"
        gz_path = Path(tmp) / filename
        await asyncio.to_thread(_sqlite_backup_sync, settings.db_path, raw_path)
        gz_bytes = await asyncio.to_thread(_gzip_file_sync, raw_path, gz_path)
        await bot.send_document(
            chat_id=settings.backup_chat_id,
            document=BufferedInputFile(gz_bytes, filename=filename),
            caption=caption,
        )
    # TemporaryDirectory removes raw_path/gz_path on exit (success or error) -
    # nothing sensitive is ever left on disk.


async def run_forever(
    bot: Bot,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> None:
    """Background loop: sleep until BACKUP_HOUR_UTC, back up, repeat forever.
    Intended to run as an asyncio.Task cancelled on app shutdown (see
    app/main.py); a CancelledError during sleep or backup propagates so the
    task ends promptly, but any other failure is logged and the loop just
    waits for the next scheduled run."""
    while True:
        delay = seconds_until_next_run(now(), settings.backup_hour_utc)
        await sleep(delay)
        try:
            async with session_factory() as session:
                await run_backup(bot, settings, session)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a bad backup must never take the app down
            log.exception("scheduled backup failed")
