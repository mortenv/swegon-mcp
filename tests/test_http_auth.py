"""
Tests for HTTP transport and API key authentication.
Verifies that the server correctly rejects/accepts requests based on auth.

SSE endpoints hold connections open indefinitely, so auth tests use a minimal
Starlette app that mounts only the middleware — no real SSE stream involved.
"""

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from swegon_mcp.http_server import ApiKeyMiddleware, create_app

VALID_KEY = "test-secret-key-abc123"
WRONG_KEY = "wrong-key"


def make_auth_test_app(api_key: str) -> Starlette:
    """Minimal app with only auth middleware and a dummy /probe endpoint."""

    async def probe(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "swegon-mcp"})

    return Starlette(
        routes=[Route("/health", health), Route("/probe", probe)],
        middleware=[Middleware(ApiKeyMiddleware, api_key=api_key)],
    )


@pytest.fixture
def client():
    return TestClient(make_auth_test_app(VALID_KEY), raise_server_exceptions=False)


@pytest.fixture
def health_client():
    return TestClient(create_app(api_key=VALID_KEY), raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# /health — no auth required
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_health_returns_200_without_auth(self, health_client):
        response = health_client.get("/health")
        assert response.status_code == 200

    def test_health_returns_ok_status(self, health_client):
        response = health_client.get("/health")
        assert response.json()["status"] == "ok"

    def test_health_returns_service_name(self, health_client):
        response = health_client.get("/health")
        assert response.json()["service"] == "swegon-mcp"


# ---------------------------------------------------------------------------
# API key via X-API-Key header
# ---------------------------------------------------------------------------


class TestApiKeyHeader:
    def test_rejects_request_without_api_key(self, client):
        response = client.get("/probe")
        assert response.status_code == 401

    def test_rejects_wrong_api_key(self, client):
        response = client.get("/probe", headers={"X-API-Key": WRONG_KEY})
        assert response.status_code == 401

    def test_accepts_correct_api_key_header(self, client):
        response = client.get("/probe", headers={"X-API-Key": VALID_KEY})
        assert response.status_code == 200

    def test_401_response_is_json(self, client):
        response = client.get("/probe")
        assert response.headers["content-type"].startswith("application/json")
        assert "error" in response.json()


# ---------------------------------------------------------------------------
# API key via query parameter
# ---------------------------------------------------------------------------


class TestApiKeyQueryParam:
    def test_accepts_correct_api_key_query_param(self, client):
        response = client.get(f"/probe?api_key={VALID_KEY}")
        assert response.status_code == 200

    def test_rejects_wrong_api_key_query_param(self, client):
        response = client.get(f"/probe?api_key={WRONG_KEY}")
        assert response.status_code == 401

    def test_empty_api_key_query_param_is_rejected(self, client):
        response = client.get("/probe?api_key=")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Timing-safe comparison (no timing oracle)
# ---------------------------------------------------------------------------


class TestTimingSafeComparison:
    def test_uses_secrets_compare_digest(self):
        """Middleware must use secrets.compare_digest to prevent timing attacks."""
        import inspect
        from swegon_mcp import http_server

        source = inspect.getsource(http_server)
        assert "secrets.compare_digest" in source, (
            "API key comparison must use secrets.compare_digest to prevent timing attacks"
        )


# ---------------------------------------------------------------------------
# Missing API key env var
# ---------------------------------------------------------------------------


class TestGetApiKey:
    def test_raises_if_env_var_not_set(self, monkeypatch):
        monkeypatch.delenv("SWEGON_API_KEY", raising=False)
        from swegon_mcp.http_server import get_api_key

        with pytest.raises(RuntimeError, match="SWEGON_API_KEY"):
            get_api_key()

    def test_returns_key_from_env_var(self, monkeypatch):
        monkeypatch.setenv("SWEGON_API_KEY", "my-secret")
        from swegon_mcp.http_server import get_api_key

        assert get_api_key() == "my-secret"
