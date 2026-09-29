import sqlite3

from alembic import command
from alembic.config import Config

from tests.conftest import BACKEND_DIR

EXPECTED_TABLES = {"items", "photos", "channel_messages", "tags", "item_tags", "events", "leads", "subscribers"}


def _alembic_config(db_path) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    return cfg


def test_upgrade_head_on_empty_db(tmp_path):
    db_path = tmp_path / "fresh.db"
    command.upgrade(_alembic_config(db_path), "head")

    conn = sqlite3.connect(db_path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()

    assert EXPECTED_TABLES <= tables
    assert "ix_items_status_published_at" in indexes
    assert "ix_item_tags_tag_id" in indexes


def test_downgrade_base_removes_tables(tmp_path):
    db_path = tmp_path / "fresh.db"
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    conn = sqlite3.connect(db_path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert not (EXPECTED_TABLES & tables)


def _columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_upgrade_from_0001_backfills_existing_iphone_lots(tmp_path):
    db_path = tmp_path / "v1.db"
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "0001")

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO items (id, public_id, status, model, generation, storage_gb, color, created_by) "
        "VALUES (1, 'OLDLOT01', 'active', 'iPhone 15 Pro', '15', 256, 'Black Titanium', 111)"
    )
    conn.execute("INSERT INTO items (id, public_id, status, created_by) VALUES (2, 'EMPTYDR1', 'draft', 111)")
    conn.execute(
        "INSERT INTO photos (id, item_id, tg_file_id, file_unique_id, position) VALUES (1, 1, 'f0', 'u0', 0)"
    )
    conn.execute("INSERT INTO channel_messages (item_id, chat_id, message_id, kind) VALUES (1, -100, 5, 'media')")
    conn.commit()
    conn.close()

    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    assert {"category", "device_type", "brand", "year", "variant", "skipped_json"} <= _columns(conn, "items")
    assert {"kind", "duration", "file_size"} <= _columns(conn, "photos")
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "leads" in tables and "ix_leads_item_id" in indexes

    rows = conn.execute(
        "SELECT public_id, category, device_type, brand, model, year, variant, skipped_json FROM items ORDER BY id"
    ).fetchall()
    assert rows == [
        ("OLDLOT01", "iphone", "phone", "Apple", "iPhone 15 Pro", None, None, None),
        ("EMPTYDR1", None, None, None, None, None, None, None),
    ]
    assert conn.execute("SELECT kind, duration, file_size FROM photos").fetchall() == [("photo", None, None)]
    # new rows get kind='photo' from the server default
    conn.execute("INSERT INTO photos (item_id, tg_file_id, file_unique_id, position) VALUES (1, 'f1', 'u1', 1)")
    assert conn.execute("SELECT kind FROM photos WHERE tg_file_id='f1'").fetchone() == ("photo",)
    conn.execute(
        "INSERT INTO photos (item_id, kind, tg_file_id, file_unique_id, position, duration) "
        "VALUES (1, 'video', 'v1', 'uv1', 2, 12)"
    )
    conn.execute(
        "INSERT INTO leads (item_id, source, buyer_name, buyer_contact) VALUES (1, 'site', 'Иван', '+79990000000')"
    )
    conn.commit()
    conn.close()

    command.downgrade(cfg, "0001")

    conn = sqlite3.connect(db_path)
    assert "category" not in _columns(conn, "items")
    assert "kind" not in _columns(conn, "photos")
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "leads" not in tables
    assert {"ix_items_status_published_at", "ix_photos_item_id"} <= indexes
    # old data survives the table rebuild; videos (not representable in v1) are dropped
    assert conn.execute("SELECT public_id, model FROM items ORDER BY id").fetchall() == [
        ("OLDLOT01", "iPhone 15 Pro"),
        ("EMPTYDR1", None),
    ]
    assert conn.execute("SELECT tg_file_id FROM photos ORDER BY position").fetchall() == [("f0",), ("f1",)]
    assert conn.execute("SELECT count(*) FROM channel_messages").fetchone() == (1,)
    conn.close()

    command.upgrade(cfg, "head")  # round trip works


def test_subscribers_upgrade_and_downgrade(tmp_path):
    db_path = tmp_path / "subs.db"
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "0002")
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO items (id, public_id, status, created_by) VALUES (1, 'KEEPME01', 'active', 111)")
    conn.commit()
    conn.close()

    command.upgrade(cfg, "head")
    conn = sqlite3.connect(db_path)
    assert _columns(conn, "subscribers") == {
        "tg_id", "username", "first_name", "subscribed", "created_at", "updated_at", "blocked_at"
    }
    conn.execute("INSERT INTO subscribers (tg_id, username) VALUES (5000000000, 'big_id')")  # > 32-bit ids
    row = conn.execute("SELECT tg_id, subscribed, blocked_at, created_at IS NOT NULL FROM subscribers").fetchone()
    assert row == (5000000000, 1, None, 1)
    conn.commit()
    conn.close()

    command.downgrade(cfg, "0002")
    conn = sqlite3.connect(db_path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "subscribers" not in tables and "leads" in tables
    assert conn.execute("SELECT public_id FROM items").fetchall() == [("KEEPME01",)]
    conn.close()

    command.upgrade(cfg, "head")  # round trip
