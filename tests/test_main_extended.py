"""Extended startup, persistence, and ASGI middleware coverage."""

import runpy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from app import main


async def _receive():
    return {"type": "http.request", "body": b"", "more_body": False}


def _http_scope(path: str, *, scheme: str = "https", host: str = "test") -> dict:
    return {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": scheme,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"host", host.encode())],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 443),
    }


def test_persistence_check_covers_skip_existing_write_failure_warning_and_first_deploy(tmp_path):
    with patch("app.main.config.SKIP_PERSISTENCE_CHECK", True):
        assert main._check_persistent_storage() is True

    marker = MagicMock()
    marker.exists.return_value = True
    with (
        patch("app.main.config.SKIP_PERSISTENCE_CHECK", False),
        patch("app.main._PERSISTENCE_MARKER", marker),
    ):
        assert main._check_persistent_storage() is True

    marker.exists.return_value = False
    marker.write_text.side_effect = OSError("read only")
    with (
        patch("app.main.config.SKIP_PERSISTENCE_CHECK", False),
        patch("app.main._PERSISTENCE_MARKER", marker),
    ):
        assert main._check_persistent_storage() is False

    database = tmp_path / "f1.db"
    database.touch()
    persistence_marker = tmp_path / ".marker"
    with (
        patch("app.main.config.SKIP_PERSISTENCE_CHECK", False),
        patch("app.main.config.DATABASE_PATH", str(database)),
        patch("app.main._PERSISTENCE_MARKER", persistence_marker),
    ):
        assert main._check_persistent_storage() is False

    persistence_marker.unlink()
    database.unlink()
    with (
        patch("app.main.config.SKIP_PERSISTENCE_CHECK", False),
        patch("app.main.config.DATABASE_PATH", str(database)),
        patch("app.main._PERSISTENCE_MARKER", persistence_marker),
    ):
        assert main._check_persistent_storage() is True


@pytest.mark.asyncio
async def test_lifespan_isolates_startup_and_shutdown_failures():
    def completed_task(coroutine, *, name):
        coroutine.close()
        return SimpleNamespace(done=lambda: True)

    with (
        patch("app.main._check_persistent_storage"),
        patch("app.main.ensure_runtime_circuits_data", side_effect=RuntimeError("seed")),
        patch("app.main.RENDER_WORKER_COUNT", 1),
        patch("app.main.run_render", new=AsyncMock(side_effect=RuntimeError("warm"))),
        patch("app.main.start_scheduler"),
        patch("app.main.create_supervised_task", side_effect=completed_task),
        patch("app.main.stop_scheduler"),
        patch("app.main.asyncio.sleep", new=AsyncMock()),
        patch(
            "app.main.flush_api_calls_to_db",
            new=AsyncMock(side_effect=RuntimeError("flush")),
        ),
        patch("app.main.drain_background_tasks", new=AsyncMock()),
        patch("app.main.shutdown_render_executor", side_effect=RuntimeError("executor")),
        patch(
            "app.main.close_shared_http_clients",
            new=AsyncMock(side_effect=RuntimeError("http")),
        ),
        patch.object(
            main.Database,
            "close_all",
            new=AsyncMock(side_effect=RuntimeError("database")),
        ),
        patch("app.main.sentry_sdk.capture_exception") as capture,
    ):
        async with main.lifespan(FastAPI()):
            pass

    assert capture.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "status", "expected_cache"),
    [
        ("/static/fonts/font.ttf", 200, "public, max-age=31536000, immutable"),
        ("/static/image.png", 200, "public, max-age=86400"),
        ("/static/app.css", 200, "public, max-age=3600"),
        ("/static/data.txt", 200, None),
        ("/static/app.css", 404, None),
        ("/page", 200, None),
    ],
)
async def test_static_cache_middleware_policies(path, status, expected_cache):
    messages = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def send(message):
        messages.append(message)

    await main.StaticCacheMiddleware(app)(_http_scope(path), _receive, send)
    headers = dict(messages[0]["headers"])
    assert headers.get(b"cache-control") == (
        expected_cache.encode() if expected_cache is not None else None
    )


@pytest.mark.asyncio
async def test_static_and_security_middleware_forward_non_http_scopes():
    calls = []

    async def app(scope, receive, send):
        calls.append(scope["type"])

    scope = {"type": "websocket"}
    await main.StaticCacheMiddleware(app)(scope, _receive, AsyncMock())
    await main.SecurityHeadersMiddleware(app)(scope, _receive, AsyncMock())
    assert calls == ["websocket", "websocket"]


@pytest.mark.asyncio
async def test_security_middleware_redirects_www_with_query_and_sets_https_headers():
    messages = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def send(message):
        messages.append(message)

    scope = _http_scope("/path", host="www.example.test")
    scope["query_string"] = b"a=1"
    with patch("app.main.config.SITE_URL", "https://example.test"):
        await main.SecurityHeadersMiddleware(app)(scope, _receive, send)

    headers = dict(messages[0]["headers"])
    assert headers[b"location"] == b"https://example.test/path?a=1"
    assert headers[b"strict-transport-security"] == b"max-age=31536000"

    messages.clear()
    scope = _http_scope("/path", scheme="http", host="example.test")
    with patch("app.main.config.SITE_URL", "https://example.test"):
        await main.SecurityHeadersMiddleware(app)(scope, _receive, send)
    headers = dict(messages[0]["headers"])
    assert b"strict-transport-security" not in headers
    assert headers[b"x-frame-options"] == b"DENY"


def test_html_404_predicate_rejects_non_get_requests():
    scope = _http_scope("/unknown")
    scope["method"] = "POST"
    scope["headers"].append((b"accept", b"text/html"))

    assert main._should_render_html_404(Request(scope)) is False


@pytest.mark.filterwarnings("ignore:.*app.main.*found in sys.modules.*:RuntimeWarning")
def test_main_entrypoint_initializes_sentry_and_invokes_uvicorn_without_starting_server():
    with (
        patch("app.config.config.SENTRY_DSN", "https://sentry.example/1"),
        patch("sentry_sdk.init") as sentry_init,
        patch("uvicorn.run") as uvicorn_run,
    ):
        runpy.run_module("app.main", run_name="__main__")

    sentry_init.assert_called_once()
    uvicorn_run.assert_called_once()
