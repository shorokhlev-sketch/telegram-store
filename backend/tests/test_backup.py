import gzip
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.bot.router import create_dispatcher
from app.config import get_settings
from app.db.models import Item
from app.services import backup
from app.services.drafts import now_utc
from tests.bot_utils import ADMIN_ID, FakeBot, feed, text_update
from tests.db_utils import make_session_factory


# ---------------------------------------------------------------------------
# seconds_until_next_run
# ---------------------------------------------------------------------------


def test_seconds_until_next_run_same_day():
    now = datetime(2026, 9, 28, 1, 0, tzinfo=timezone.utc)
    assert backup.seconds_until_next_run(now, 3) == pytest.approx(2 * 3600)


def test_seconds_until_next_run_across_midnight():
    now = datetime(2026, 9, 28, 23, 30, tzinfo=timezone.utc)
    delay = backup.seconds_until_next_run(now, 3)
    assert delay == pytest.approx(3.5 * 3600)
    target = now + timedelta(seconds=delay)
    assert target == datetime(2026, 9, 29, 3, 0, tzinfo=timezone.utc)


def test_seconds_until_next_run_exact_hour_rolls_to_tomorrow():
    now = datetime(2026, 9, 28, 3, 0, tzinfo=timezone.utc)
    delay = backup.seconds_until_next_run(now, 3)
    assert delay == pytest.approx(24 * 3600)


def test_seconds_until_next_run_accepts_non_utc_input():
    # 23:00 MSK (UTC+3) on the 28th == 20:00 UTC on the 28th.
    msk = timezone(timedelta(hours=3))
    now = datetime(2026, 9, 28, 23, 0, tzinfo=msk)
    delay = backup.seconds_until_next_run(now, 3)  # next 03:00 UTC is the 29th, 7h away
    assert delay == pytest.approx(7 * 3600)


# ---------------------------------------------------------------------------
# run_backup
# ---------------------------------------------------------------------------


async def _seed_items(factory) -> None:
    async with factory() as session:
        session.add_all(
            [
                Item(public_id="AAAAAAA1", status="active", created_by=ADMIN_ID, model="iPhone 15", price=1000),
                Item(public_id="AAAAAAA2", status="active", created_by=ADMIN_ID, model="iPhone 14", price=900),
                Item(public_id="AAAAAAA3", status="sold", created_by=ADMIN_ID, model="iPad Air", price=800),
                Item(public_id="AAAAAAA4", status="draft", created_by=ADMIN_ID, model=None, price=None),
            ]
        )
        await session.commit()


async def test_run_backup_sends_valid_gzip_with_rows_and_caption():
    settings = get_settings()
    factory = await make_session_factory(settings.db_path)
    await _seed_items(factory)
    bot = FakeBot()

    async with factory() as session:
        await backup.run_backup(bot, settings, session, now=datetime(2026, 9, 28, 3, 0, tzinfo=timezone.utc))

    docs = bot.documents()
    assert len(docs) == 1
    doc = docs[0]
    assert doc.chat_id == settings.backup_chat_id
    assert doc.document.filename == "apple-store-tg-20260928-0300.db.gz"

    raw_db = gzip.decompress(doc.document.data)
    tmp_db = Path(settings.db_path).parent / "restored.db"
    tmp_db.write_bytes(raw_db)
    try:
        conn = sqlite3.connect(str(tmp_db))
        rows = conn.execute("SELECT public_id, status FROM items ORDER BY public_id").fetchall()
        conn.close()
    finally:
        tmp_db.unlink()
    assert rows == [
        ("AAAAAAA1", "active"),
        ("AAAAAAA2", "active"),
        ("AAAAAAA3", "sold"),
        ("AAAAAAA4", "draft"),
    ]

    caption = doc.caption
    assert "Всего лотов: 4" in caption
    assert "Активные: 2" in caption
    assert "Продано: 1" in caption
    assert "Черновики: 1" in caption

    await factory.kw["bind"].dispose()


async def test_run_backup_noop_when_disabled():
    settings = get_settings().model_copy(update={"backup_chat_id": 0})
    factory = await make_session_factory(settings.db_path)
    bot = FakeBot()

    async with factory() as session:
        await backup.run_backup(bot, settings, session)

    assert bot.documents() == []
    await factory.kw["bind"].dispose()


# ---------------------------------------------------------------------------
# /backup admin command
# ---------------------------------------------------------------------------


@pytest.fixture
async def env(tmp_path):
    factory = await make_session_factory(tmp_path / "backup_cmd.db")
    settings = get_settings()
    bot = FakeBot()
    dp = create_dispatcher(settings, factory)
    yield settings, factory, bot, dp
    await factory.kw["bind"].dispose()


async def test_backup_command_sends_document(env):
    settings, factory, bot, dp = env
    async with factory() as session:
        session.add(Item(public_id="CMDBACK1", status="active", created_by=ADMIN_ID, model="iPhone 15", price=1000))
        await session.commit()

    await feed(dp, bot, text_update("/backup"))

    texts = bot.sent_texts()
    assert "Делаю бэкап…" in texts
    assert "Бэкап отправлен." in texts
    docs = bot.documents()
    assert len(docs) == 1
    assert docs[0].chat_id == settings.backup_chat_id


async def test_backup_command_reports_disabled(env, monkeypatch):
    _, factory, bot, dp = env
    monkeypatch.setenv("BACKUP_CHAT_ID", "")
    get_settings.cache_clear()
    dp = create_dispatcher(get_settings(), factory)

    await feed(dp, bot, text_update("/backup"))

    assert bot.last_text() == "BACKUP_CHAT_ID не настроен, бэкап отправлять некуда."
    assert bot.documents() == []
