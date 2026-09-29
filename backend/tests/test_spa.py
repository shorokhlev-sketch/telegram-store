"""FastAPI serves the built storefront (web/dist) with an SPA fallback,
without shadowing /api and /tg."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

INDEX = "<!doctype html><html><body><div id=root></div>SPA-INDEX</body></html>"


@pytest.fixture
def fake_dist(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX, encoding="utf-8")
    (dist / "assets" / "app-abc123.js").write_text("console.log('app')", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    monkeypatch.setenv("WEB_DIST_DIR", str(dist))
    from app.config import get_settings

    get_settings.cache_clear()
    return dist


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_fallback_serves_index_for_client_routes(fake_dist):
    async with await _client() as client:
        for path in ("/", "/item/X", "/item/ABCDEFGH", "/admin", "/some/deep/path"):
            resp = await client.get(path)
            assert resp.status_code == 200, path
            assert "SPA-INDEX" in resp.text
            assert resp.headers["content-type"].startswith("text/html")
            assert resp.headers["cache-control"] == "no-cache"


@pytest.mark.asyncio
async def test_static_assets_served_with_long_cache(fake_dist):
    async with await _client() as client:
        resp = await client.get("/assets/app-abc123.js")
        assert resp.status_code == 200
        assert resp.text == "console.log('app')"
        assert "immutable" in resp.headers["cache-control"]

        resp = await client.get("/favicon.svg")
        assert resp.status_code == 200 and resp.text == "<svg/>"

        # A missing hashed asset is a plain 404, not the HTML shell.
        resp = await client.get("/assets/missing.js")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_no_path_traversal_out_of_dist(fake_dist):
    async with await _client() as client:
        resp = await client.get("/..%2Fsecret.txt")
        assert "nope" not in resp.text


@pytest.mark.asyncio
async def test_unknown_api_and_tg_paths_stay_json_404(fake_dist):
    async with await _client() as client:
        for path in ("/api/nope", "/api/items/x/y/z", "/tg/nope", "/api"):
            resp = await client.get(path)
            assert resp.status_code == 404, path
            assert resp.json() == {"detail": "Not Found"}


@pytest.mark.asyncio
async def test_api_health_still_works(fake_dist):
    async with await _client() as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_non_get_unknown_path_is_not_the_spa(fake_dist):
    async with await _client() as client:
        resp = await client.post("/item/X")
    assert resp.status_code in (404, 405)
    assert "SPA-INDEX" not in resp.text


@pytest.mark.asyncio
async def test_without_dist_everything_404s_as_before(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_DIST_DIR", str(tmp_path / "absent"))
    from app.config import get_settings

    get_settings.cache_clear()
    async with await _client() as client:
        resp = await client.get("/item/X")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}
