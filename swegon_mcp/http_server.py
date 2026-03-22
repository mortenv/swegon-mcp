"""HTTP/SSE transport for swegon-mcp.

Wraps the MCP server in a Starlette app with:
- SSE endpoint for MCP clients (e.g. Claude Desktop, OpenClaw, mcporter)
- API key authentication via X-API-Key header or ?api_key= query param
- OAuth 2.0 client_credentials flow (for mcporter + standard MCP clients)
- /health endpoint (no auth required)
"""

from __future__ import annotations

import logging
import os
import secrets
import time
import uuid

from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
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


def _validate_auth(request: Request, api_key: str) -> bool:
    """Return True if request carries a valid credential."""
    # 1. X-API-Key header
    provided = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    if provided and secrets.compare_digest(provided, api_key):
        return True

    # 2. Bearer token (OAuth)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        stored_key = _bearer_tokens.get(token)
        if stored_key and secrets.compare_digest(stored_key, api_key):
            return True

    return False


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Auth middleware — allows X-API-Key and Bearer tokens."""

    def __init__(self, app, api_key: str) -> None:
        super().__init__(app)
        self.api_key = api_key

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        if not _validate_auth(request, self.api_key):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        return await call_next(request)


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
        middleware=[Middleware(ApiKeyMiddleware, api_key=api_key)],
    )
    return starlette_app


def get_api_key() -> str:
    key = os.environ.get("SWEGON_API_KEY", "")
    if not key:
        raise RuntimeError(
            "SWEGON_API_KEY environment variable is not set. "
            'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
        )
    return key
