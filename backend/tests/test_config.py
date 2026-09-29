from app.config import get_settings


def test_settings_parse_admin_ids_and_urls():
    s = get_settings()
    assert s.admin_ids == [111, 222]
    assert s.is_admin(111) and not s.is_admin(333)
    assert s.channel_id == -1001234567890
    assert s.webhook_url == "https://example.test/tg/webhook"
    assert s.database_url.startswith("sqlite+aiosqlite:///")


def test_empty_int_env_values_are_zero(monkeypatch):
    monkeypatch.setenv("BACKUP_CHAT_ID", "")
    monkeypatch.setenv("CHANNEL_ID", "")
    get_settings.cache_clear()
    s = get_settings()
    assert s.backup_chat_id == 0 and s.channel_id == 0


def test_seller_chat_url_encodes_text_and_strips_at(monkeypatch):
    s = get_settings()
    assert s.seller_username == "seller_test"
    assert (
        s.seller_chat_url("Здравствуйте, интересует iPhone 15 Pro 256 ГБ, лот AB&CD")
        == "https://t.me/seller_test?text=%D0%97%D0%B4%D1%80%D0%B0%D0%B2%D1%81%D1%82%D0%B2%D1%83%D0%B9%D1%82%D0%B5"
        "%2C%20%D0%B8%D0%BD%D1%82%D0%B5%D1%80%D0%B5%D1%81%D1%83%D0%B5%D1%82%20iPhone%2015%20Pro%20256%20"
        "%D0%93%D0%91%2C%20%D0%BB%D0%BE%D1%82%20AB%26CD"
    )

    monkeypatch.setenv("SELLER_USERNAME", " @shop_owner ")
    get_settings.cache_clear()
    assert get_settings().seller_username == "shop_owner"
    assert get_settings().seller_chat_url("hi") == "https://t.me/shop_owner?text=hi"

    monkeypatch.setenv("SELLER_USERNAME", "")
    get_settings.cache_clear()
    assert get_settings().seller_username == ""
    assert get_settings().seller_chat_url("hi") is None


def test_leads_chat_id_falls_back_to_first_admin(monkeypatch):
    s = get_settings()
    assert s.leads_chat_id == 0
    assert s.leads_chat_id_effective == 111

    monkeypatch.setenv("LEADS_CHAT_ID", "-1009998887776")
    get_settings.cache_clear()
    assert get_settings().leads_chat_id_effective == -1009998887776

    monkeypatch.setenv("LEADS_CHAT_ID", "")
    monkeypatch.setenv("ADMIN_IDS", "")
    get_settings.cache_clear()
    assert get_settings().leads_chat_id_effective == 0
