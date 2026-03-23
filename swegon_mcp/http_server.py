"""HTTP/SSE transport for swegon-mcp.

Wraps the MCP server in a Starlette app with:
- SSE endpoint for MCP clients (e.g. Claude Desktop, OpenClaw, mcporter)
- API key authentication via X-API-Key header or ?api_key= query param
- OAuth 2.0 client_credentials flow (for mcporter + standard MCP clients)
- /health endpoint (no auth required)
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
import uuid
from urllib.parse import parse_qs

from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from .server import app as mcp_app

logger = logging.getLogger("swegon-mcp.http")

# In-memory stores (cleared on restart)
_registered_clients: dict[str, str] = {}  # client_id → client_secret
_bearer_tokens: dict[str, str] = {}  # token → api_key

# Paths that don't require auth
_PUBLIC_PATHS = {
    "/health",
    "/.well-known/oauth-authorization-server",
    "/.well-known/oauth-protected-resource",
    "/oauth/register",
    "/oauth/token",
}


class ApiKeyMiddleware:
    """Pure ASGI middleware for API key + Bearer token auth.

    Uses raw ASGI instead of BaseHTTPMiddleware to avoid breaking
    SSE/streaming responses (Starlette BaseHTTPMiddleware wraps the
    response body iterator which is incompatible with SSE).
    """

    def __init__(self, app, api_key: str) -> None:
        self.app = app
        self.api_key = api_key

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in _PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        # Check auth: X-API-Key header, ?api_key= query param, or Bearer token
        headers = dict(scope.get("headers", []))

        # 1. X-API-Key header
        provided = (headers.get(b"x-api-key") or b"").decode()

        # 2. ?api_key= query param
        if not provided:
            qs = parse_qs(scope.get("query_string", b"").decode())
            provided = qs.get("api_key", [""])[0]

        # 3. Bearer token (OAuth)
        if not provided:
            auth_header = (headers.get(b"authorization") or b"").decode()
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
                stored_key = _bearer_tokens.get(token)
                if stored_key and secrets.compare_digest(stored_key, self.api_key):
                    await self.app(scope, receive, send)
                    return

        if provided and secrets.compare_digest(provided, self.api_key):
            await self.app(scope, receive, send)
            return

        # Reject
        body = json.dumps({"error": "Unauthorized"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"content-length", str(len(body)).encode()],
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def create_app(api_key: str) -> Starlette:  # noqa: C901
    sse = SseServerTransport("/messages/")

    async def handle_sse(request: Request) -> Response:
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await mcp_app.run(
                streams[0],
                streams[1],
                mcp_app.create_initialization_options(),
            )
        return Response()

    async def handle_health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "swegon-mcp"})

    # ── OAuth endpoints ────────────────────────────────────────────────────

    async def handle_oauth_metadata(request: Request) -> JSONResponse:
        base = str(request.base_url).rstrip("/")
        return JSONResponse(
            {
                "issuer": base,
                "token_endpoint": f"{base}/oauth/token",
                "registration_endpoint": f"{base}/oauth/register",
                "response_types_supported": ["token"],
                "grant_types_supported": ["client_credentials"],
                "token_endpoint_auth_methods_supported": ["client_secret_post", "none"],
            }
        )

    async def handle_protected_resource(request: Request) -> JSONResponse:
        base = str(request.base_url).rstrip("/")
        return JSONResponse(
            {
                "resource": base,
                "authorization_servers": [base],
                "bearer_methods_supported": ["header"],
            }
        )

    async def handle_register(request: Request) -> JSONResponse:
        """RFC 7591 Dynamic Client Registration — accept any client."""
        client_id = str(uuid.uuid4())
        _registered_clients[client_id] = api_key
        return JSONResponse(
            {
                "client_id": client_id,
                "client_secret": api_key,
                "client_id_issued_at": int(time.time()),
                "grant_types": ["client_credentials"],
                "token_endpoint_auth_method": "client_secret_post",
            },
            status_code=201,
        )

    async def handle_token(request: Request) -> JSONResponse:
        """Issue a Bearer token for valid client_credentials."""
        form = await request.form()
        grant_type = form.get("grant_type", "")
        client_secret = form.get("client_secret", "")

        if grant_type != "client_credentials":
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

        if not client_secret or not secrets.compare_digest(client_secret, api_key):
            return JSONResponse({"error": "invalid_client"}, status_code=401)

        token = secrets.token_hex(32)
        _bearer_tokens[token] = api_key
        return JSONResponse(
            {
                "access_token": token,
                "token_type": "Bearer",
                "expires_in": 3600,
            }
        )

    starlette_app = Starlette(
        routes=[
            Route("/health", handle_health),
            Route("/sse", handle_sse),
            Route("/.well-known/oauth-authorization-server", handle_oauth_metadata),
            Route("/.well-known/oauth-protected-resource", handle_protected_resource),
            Route("/oauth/register", handle_register, methods=["POST"]),
            Route("/oauth/token", handle_token, methods=["POST"]),
            Mount("/messages/", app=sse.handle_post_message),
        ],
    )
    return ApiKeyMiddleware(starlette_app, api_key=api_key)


def get_api_key() -> str:
    key = os.environ.get("SWEGON_API_KEY", "")
    if not key:
        raise RuntimeError(
            "SWEGON_API_KEY environment variable is not set. "
            'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
        )
    return key
