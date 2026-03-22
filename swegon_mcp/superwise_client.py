"""Socket.IO client for SuperWISE damper control.

Communicates with the SuperWISE web UI via its Socket.IO v2 API
to read and write damper IO data (e.g. "Funksjon konstant luftmengde").
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx
import socketio

from .config import AppConfig, DamperRoom

logger = logging.getLogger("swegon-mcp.superwise")


class SuperWiseClient:
    def __init__(self, config: AppConfig):
        self.config = config
        self._cookies: str | None = None
        self._lock = asyncio.Lock()

    @property
    def _base_url(self) -> str:
        return f"http://{self.config.superwise.host}"

    async def _login(self) -> str:
        """Login to SuperWISE web UI and return session cookie string."""
        async with self._lock:
            if self._cookies:
                return self._cookies

            sw = self.config.superwise
            async with httpx.AsyncClient(follow_redirects=False) as client:
                # Get login page for CSRF token
                resp = await client.get(
                    f"{self._base_url}/login/",
                    params={"next": "/index"},
                )
                match = re.search(r'csrf_token.*?value="([^"]+)"', resp.text)
                if not match:
                    raise ConnectionError(
                        "Could not extract CSRF token from login page"
                    )
                csrf = match.group(1)

                # POST login
                login_resp = await client.post(
                    f"{self._base_url}/login/",
                    params={"next": "/index"},
                    data={
                        "csrf_token": csrf,
                        "email": sw.user,
                        "password": sw.password,
                    },
                    cookies=resp.cookies,
                )

                if login_resp.status_code != 302:
                    raise ConnectionError(
                        f"Login failed (HTTP {login_resp.status_code}). Check credentials."
                    )

                # Extract session cookie
                session_cookie = login_resp.cookies.get("session")
                if not session_cookie:
                    raise ConnectionError("No session cookie in login response")

                self._cookies = f"session={session_cookie}"
                logger.info("Logged in to SuperWISE at %s", sw.host)
                return self._cookies

    def _invalidate_session(self) -> None:
        """Clear cached session so next call re-authenticates."""
        self._cookies = None

    async def _socketio_request(
        self, cookies: str, event_data: dict, response_command: str
    ) -> dict:
        """Connect to Socket.IO, emit a request, wait for matching response."""
        timeout = self.config.superwise.timeout
        result: dict = {}
        error: Exception | None = None
        done = asyncio.Event()

        sio = socketio.AsyncClient(
            reconnection=False,
            logger=False,
        )

        @sio.on("response", namespace="/all")
        async def on_response(data: dict) -> None:
            nonlocal result, error
            req = data.get("request", {})
            if req.get("command") != response_command:
                return

            if data.get("status") != 0:
                error = RuntimeError(
                    f"SuperWISE error: {data.get('response', {}).get('error', data)}"
                )
            else:
                result = data
            done.set()

        @sio.on("connect_error", namespace="/all")
        async def on_connect_error(data: Any) -> None:
            nonlocal error
            error = ConnectionError(f"Socket.IO connection error: {data}")
            done.set()

        try:
            await sio.connect(
                self._base_url,
                namespaces=["/all"],
                transports=["polling", "websocket"],
                headers={"Cookie": cookies},
            )

            await sio.emit("request", event_data, namespace="/all")

            try:
                await asyncio.wait_for(done.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                raise TimeoutError(f"No response from SuperWISE within {timeout}s")

            if error:
                raise error

            return result

        finally:
            if sio.connected:
                await sio.disconnect()

    async def get_damper_value(self, room: DamperRoom) -> int:
        """Read the current damper value for a room. Returns 0 or 1."""
        cookies = await self._login()

        try:
            result = await self._socketio_request(
                cookies,
                {
                    "method": "get",
                    "command": "system__req__device_io_data",
                    "callback_function": "device_data_response",
                    "location": room.location.model_dump(),
                    "parameters": {"current_data": True, "config_data": True},
                },
                "system__req__device_io_data",
            )
        except ConnectionError:
            self._invalidate_session()
            raise

        data = result.get("response", {}).get("data", {})
        node_id = str(room.location.node)
        type_id = str(room.type_id)

        # Search in current/config sections
        for section in ("current", "config"):
            section_data = data.get(section, {})
            if isinstance(section_data, dict):
                node_data = section_data.get(node_id, {})
                if isinstance(node_data, dict) and type_id in node_data:
                    entry = node_data[type_id]
                    raw = entry["value"] if isinstance(entry, dict) else entry
                    return int(raw)

        # Fallback: search all nested dicts
        for val in data.values():
            if not isinstance(val, dict):
                continue
            if type_id in val:
                entry = val[type_id]
                return entry["value"] if isinstance(entry, dict) else entry
            for sub_val in val.values():
                if isinstance(sub_val, dict) and type_id in sub_val:
                    entry = sub_val[type_id]
                    raw = entry["value"] if isinstance(entry, dict) else entry
                    return int(raw)

        raise ValueError(
            f"type_id {room.type_id} not found for {room.label}. "
            f"Response keys: {list(data.keys())}"
        )

    async def set_damper_value(self, room: DamperRoom, value: int) -> dict:
        """Set the damper value (0 or 1) for a room."""
        if value not in (0, 1):
            raise ValueError(f"Value must be 0 or 1, got {value}")

        cookies = await self._login()

        try:
            result = await self._socketio_request(
                cookies,
                {
                    "method": "post",
                    "command": "system__post__device_io_data",
                    "callback_function": "post_device_data_response",
                    "location": room.location.model_dump(),
                    "parameters": {
                        "change_list": [
                            {
                                "type_id": room.type_id,
                                "value": value,
                                "name": room.io_name,
                            }
                        ],
                        "sign": None,
                    },
                },
                "system__post__device_io_data",
            )
        except ConnectionError:
            self._invalidate_session()
            raise

        resp_data = result.get("response", {}).get("data", {})
        failed = resp_data.get("failed", [])
        if failed:
            raise RuntimeError(f"Failed to set damper: {failed}")

        return resp_data
