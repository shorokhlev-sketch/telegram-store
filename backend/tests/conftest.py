import os
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _test_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_TOKEN", "123456:TEST")
    monkeypatch.setenv("BOT_USERNAME", "test_bot")
    monkeypatch.setenv("MINIAPP_SHORT_NAME", "shop")
    monkeypatch.setenv("CHANNEL_ID", "-1001234567890")
    monkeypatch.setenv("ADMIN_IDS", "111,222")
    monkeypatch.setenv("SELLER_USERNAME", "seller_test")
    monkeypatch.setenv("LEADS_CHAT_ID", "")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.test")
    monkeypatch.setenv("WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("BACKUP_CHAT_ID", "111")
    # Keep tests hermetic: don't serve a real web/dist build unless a test opts in.
    monkeypatch.setenv("WEB_DIST_DIR", str(tmp_path / "no-web-dist"))
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
async def _drain_broadcasts():
    """publisher.publish schedules a background subscriber broadcast; finish it
    inside the test's event loop so no task outlives the test."""
    yield
    from app.services import notify

    await notify.drain(timeout=5)
