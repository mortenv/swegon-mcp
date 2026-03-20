"""HTTP/SSE transport for swegon-mcp.

Wraps the MCP server in a Starlette app with:
- SSE endpoint for MCP clients (e.g. Claude Desktop, OpenClaw)
- API key authentication via X-API-Key header or ?api_key= query param
- /health endpoint (no auth required)
"""

from __future__ import annotations

import logging
import os
import secrets

from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from .server import app as mcp_app

logger = logging.getLogger("swegon-mcp.http")


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Reject requests that don't carry the correct API key.

    Skips authentication for /health so uptime monitors work without credentials.
    """

    def __init__(self, app, api_key: str) -> None:
        super().__init__(app)
        self.api_key = api_key

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)

        provided = request.headers.get("X-API-Key") or request.query_params.get(
            "api_key"
        )
        if not provided or not secrets.compare_digest(provided, self.api_key):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        return await call_next(request)


def create_app(api_key: str) -> Starlette:
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

    starlette_app = Starlette(
        routes=[
            Route("/health", handle_health),
            Route("/sse", handle_sse),
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
