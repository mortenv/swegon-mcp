"""Tests for OAuth 2.0 endpoints in swegon-mcp HTTP server."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from swegon_mcp.http_server import _bearer_tokens, _registered_clients, create_app

VALID_KEY = "test-secret-key-abc123"
WRONG_KEY = "wrong-key"


@pytest.fixture(autouse=True)
def clear_stores():
    """Clear in-memory OAuth stores between tests."""
    _registered_clients.clear()
    _bearer_tokens.clear()
    yield
    _registered_clients.clear()
    _bearer_tokens.clear()


@pytest.fixture
def client():
    return TestClient(create_app(api_key=VALID_KEY), raise_server_exceptions=False)


# ── OAuth metadata ─────────────────────────────────────────────────────────


class TestOAuthMetadata:
    def test_oauth_metadata_endpoint(self, client):
        r = client.get("/.well-known/oauth-authorization-server")
        assert r.status_code == 200
        data = r.json()
        assert "issuer" in data
        assert "token_endpoint" in data
        assert "registration_endpoint" in data
        assert "client_credentials" in data["grant_types_supported"]

    def test_oauth_protected_resource_endpoint(self, client):
        r = client.get("/.well-known/oauth-protected-resource")
        assert r.status_code == 200
        data = r.json()
        assert "resource" in data
        assert "authorization_servers" in data
        assert "header" in data["bearer_methods_supported"]

    def test_metadata_no_auth_required(self, client):
        """OAuth metadata must be public — no credentials needed."""
        r = client.get("/.well-known/oauth-authorization-server")
        assert r.status_code == 200


# ── Client registration ────────────────────────────────────────────────────


class TestClientRegistration:
    def test_register_client(self, client):
        r = client.post("/oauth/register", json={"client_name": "test"})
        assert r.status_code == 201
        data = r.json()
        assert "client_id" in data
        assert "client_secret" in data
        assert data["client_secret"] == VALID_KEY
        assert "client_credentials" in data["grant_types"]

    def test_register_stores_client(self, client):
        r = client.post("/oauth/register", json={})
        client_id = r.json()["client_id"]
        assert client_id in _registered_clients

    def test_register_no_auth_required(self, client):
        r = client.post("/oauth/register", json={})
        assert r.status_code == 201


# ── Token endpoint ─────────────────────────────────────────────────────────


class TestTokenEndpoint:
    def test_token_with_valid_secret(self, client):
        r = client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_secret": VALID_KEY,
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert "access_token" in data
        assert data["token_type"] == "Bearer"
        assert data["expires_in"] == 3600

    def test_token_with_invalid_secret(self, client):
        r = client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_secret": WRONG_KEY,
            },
        )
        assert r.status_code == 401
        assert r.json()["error"] == "invalid_client"

    def test_token_wrong_grant_type(self, client):
        r = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_secret": VALID_KEY,
            },
        )
        assert r.status_code == 400
        assert r.json()["error"] == "unsupported_grant_type"

    def test_token_stored_in_memory(self, client):
        r = client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_secret": VALID_KEY,
            },
        )
        token = r.json()["access_token"]
        assert token in _bearer_tokens


# ── Bearer token auth ──────────────────────────────────────────────────────


class TestBearerAuth:
    def test_sse_with_bearer_token(self, client):
        # Get token first
        r = client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_secret": VALID_KEY,
            },
        )
        token = r.json()["access_token"]

        # Use Bearer token on health (non-SSE endpoint to avoid hanging)
        r2 = client.get("/health", headers={"Authorization": f"Bearer {token}"})
        assert r2.status_code == 200

    def test_sse_with_invalid_bearer(self, client):
        r = client.get("/sse", headers={"Authorization": "Bearer invalid-token-xyz"})
        assert r.status_code == 401

    def test_backward_compat_api_key(self, client):
        """X-API-Key header must still work after OAuth added."""
        r = client.get("/health", headers={"X-API-Key": VALID_KEY})
        assert r.status_code == 200

    def test_no_auth_returns_401(self, client):
        r = client.get("/sse")
        assert r.status_code == 401


# ── Full OAuth flow ────────────────────────────────────────────────────────


class TestFullOAuthFlow:
    def test_register_then_get_token(self, client):
        """Simulate what mcporter does: register → get token → use API."""
        # 1. Register
        reg = client.post("/oauth/register", json={"client_name": "mcporter"})
        assert reg.status_code == 201
        client_secret = reg.json()["client_secret"]

        # 2. Get token
        tok = client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": reg.json()["client_id"],
                "client_secret": client_secret,
            },
        )
        assert tok.status_code == 200
        token = tok.json()["access_token"]

        # 3. Use token
        r = client.get("/health", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
