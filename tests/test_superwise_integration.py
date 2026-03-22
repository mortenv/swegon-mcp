"""
Integration test for SuperWISE damper control.

Spins up a fake SuperWISE server (HTTP login + raw Socket.IO protocol over HTTP)
on localhost, then runs the SuperWiseClient and MCP tools against it.

No real SuperWISE hardware needed.

Usage:
    uv run pytest tests/test_superwise_integration.py -v
"""

from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio

from aiohttp import web

from swegon_mcp.config import (
    AppConfig,
    DamperLocation,
    DamperRoom,
    ModbusConfig,
    SuperWiseConfig,
)
from swegon_mcp.superwise_client import SuperWiseClient
import swegon_mcp.server as srv

FAKE_PORT = 18923
FAKE_USER = "test@test.com"
FAKE_PASSWORD = "testpass123"
FAKE_CSRF = "fake-csrf-token-abc123"
FAKE_SESSION = "fake-session-id-xyz789"

TEST_ROOM = DamperRoom(
    name="test_room",
    label="Test Floor - Test Room - Wise Damper",
    location=DamperLocation(grouping=1, node_container=2, node=3),
    type_id=1011,
    io_name="",
)

TEST_ROOM_2 = DamperRoom(
    name="test_room_2",
    label="Test Floor - Test Room 2 - Wise Damper",
    location=DamperLocation(grouping=2, node_container=5, node=10),
    type_id=1011,
    io_name="",
)


# ─── Fake SuperWISE Server ────────────────────────────────────────────────────
#
# Implements the bare minimum of the SuperWISE web interface:
# - HTTP login (GET for CSRF, POST with credentials → 302 + session cookie)
# - Engine.IO/Socket.IO v2 protocol over HTTP long-polling
#   (handshake, namespace connect, request/response events)


class FakeSuperWISE:
    """Minimal fake SuperWISE with raw EIO=3 polling protocol."""

    def __init__(self):
        self._state: dict[tuple[int, int], int] = {
            (3, 1011): 0,  # test_room: OFF
            (10, 1011): 1,  # test_room_2: ON
        }
        self._sessions: dict[str, dict] = {}  # sid -> {buffer: [...]}
        self._sid_counter = 0
        self._app = web.Application()
        self._setup_routes()

    def _new_sid(self) -> str:
        self._sid_counter += 1
        return f"fake_sid_{self._sid_counter:04d}"

    def _setup_routes(self):
        self._app.router.add_get("/login/", self._login_get)
        self._app.router.add_post("/login/", self._login_post)
        self._app.router.add_get("/socket.io/", self._eio_poll)
        self._app.router.add_post("/socket.io/", self._eio_post)

    # ─── HTTP Login ───────────────────────────────────────────────────────

    async def _login_get(self, request: web.Request) -> web.Response:
        html = (
            "<html><body>"
            '<form id="login_form" action="" method="post">'
            f'<input id="csrf_token" name="csrf_token" type="hidden" value="{FAKE_CSRF}">'
            "</form></body></html>"
        )
        resp = web.Response(text=html, content_type="text/html")
        return resp

    async def _login_post(self, request: web.Request) -> web.Response:
        data = await request.post()
        if data.get("csrf_token") != FAKE_CSRF:
            return web.Response(status=403, text="Bad CSRF")
        if data.get("email") != FAKE_USER or data.get("password") != FAKE_PASSWORD:
            return web.Response(status=401, text="Bad credentials")

        resp = web.Response(status=302)
        resp.set_cookie("session", FAKE_SESSION)
        resp.headers["Location"] = "/index"
        return resp

    # ─── Engine.IO v2 (EIO=3) polling ─────────────────────────────────────
    #
    # EIO=3 binary framing:
    #   Payload = N * (len_prefix + \xff + packet_data)
    #   len_prefix = \x00 + digits_as_bytes + \xff
    #   Digits encode the byte length of packet_data.

    def _eio_encode(self, *packets: str) -> bytes:
        """Encode multiple EIO=3 packets into a single binary payload."""
        result = b""
        for pkt in packets:
            pkt_bytes = pkt.encode("utf-8")
            length = len(pkt_bytes)
            digits = [int(d) for d in str(length)]
            result += b"\x00" + bytes(digits) + b"\xff" + pkt_bytes
        return result

    def _eio_decode_binary(self, raw: bytes) -> list[str]:
        """Decode binary EIO=3 payload: \\x00 + digit_bytes + \\xff + data."""
        packets = []
        i = 0
        while i < len(raw):
            is_text = raw[i] == 0x00
            i += 1
            digits = []
            while i < len(raw) and raw[i] != 0xFF:
                digits.append(raw[i])
                i += 1
            i += 1  # skip 0xff
            length = int("".join(str(d) for d in digits))
            if is_text:
                packets.append(raw[i : i + length].decode("utf-8"))
            i += length
        return packets

    async def _eio_poll(self, request: web.Request) -> web.Response:
        sid = request.query.get("sid")

        if not sid:
            # Handshake: return new session
            sid = self._new_sid()
            self._sessions[sid] = {"buffer": [], "event": asyncio.Event()}
            open_pkt = json.dumps(
                {
                    "sid": sid,
                    "upgrades": [],
                    "pingInterval": 25000,
                    "pingTimeout": 60000,
                }
            )
            # EIO open packet (type 0) + SIO connect to /all (type 40)
            payload = self._eio_encode(f"0{open_pkt}", "40/all,")
            return web.Response(body=payload, content_type="application/octet-stream")

        session = self._sessions.get(sid)
        if not session:
            return web.Response(status=400, text="Unknown session")

        # Long-poll: wait for data or return ping
        if session["buffer"]:
            packets = session["buffer"]
            session["buffer"] = []
            payload = self._eio_encode(*packets)
            return web.Response(body=payload, content_type="application/octet-stream")

        # Wait briefly for responses to queue up
        session["event"].clear()
        try:
            await asyncio.wait_for(session["event"].wait(), timeout=0.5)
        except asyncio.TimeoutError:
            pass

        if session["buffer"]:
            packets = session["buffer"]
            session["buffer"] = []
            payload = self._eio_encode(*packets)
            return web.Response(body=payload, content_type="application/octet-stream")

        # Send a noop/ping to keep connection alive
        return web.Response(
            body=self._eio_encode("6"),  # EIO ping
            content_type="application/octet-stream",
        )

    async def _eio_post(self, request: web.Request) -> web.Response:
        sid = request.query.get("sid")
        session = self._sessions.get(sid)
        if not session:
            return web.Response(status=400, text="Unknown session")

        body = await request.read()

        # Parse incoming binary EIO=3 packets
        for pkt in self._eio_decode_binary(body):
            await self._handle_packet(sid, pkt, session)

        return web.Response(text="ok")

    async def _handle_packet(self, sid: str, pkt: str, session: dict):
        if not pkt:
            return

        eio_type = pkt[0]

        if eio_type == "2":
            # EIO ping → queue pong
            session["buffer"].append("3")
            session["event"].set()
            return

        if eio_type == "4":
            # SIO message
            sio_data = pkt[1:]
            await self._handle_sio_message(sid, sio_data, session)

    async def _handle_sio_message(self, sid: str, data: str, session: dict):
        # Parse "2/all,["request", {...}]"
        if not data.startswith("2/all,"):
            return
        json_str = data[len("2/all,") :]
        try:
            arr = json.loads(json_str)
        except json.JSONDecodeError:
            return

        if not isinstance(arr, list) or len(arr) < 2 or arr[0] != "request":
            return

        request_data = arr[1]
        command = request_data.get("command", "")
        location = request_data.get("location", {})
        node_id = location.get("node", 0)

        if command == "system__req__device_io_data":
            response = self._make_read_response(request_data, node_id)
        elif command == "system__post__device_io_data":
            response = self._make_write_response(request_data, node_id)
        else:
            return

        resp_json = json.dumps(["response", response])
        session["buffer"].append(f"42/all,{resp_json}")
        session["event"].set()

    def _make_read_response(self, request_data: dict, node_id: int) -> dict:
        type_id = 1011
        value = self._state.get((node_id, type_id), 0)
        return {
            "status": 0,
            "response": {
                "data": {
                    "current": {
                        str(node_id): {
                            str(type_id): {"value": value},
                        },
                    },
                    "config": {},
                },
            },
            "request": request_data,
        }

    def _make_write_response(self, request_data: dict, node_id: int) -> dict:
        params = request_data.get("parameters", {})
        change_list = params.get("change_list", [])

        success = []
        for change in change_list:
            tid = change.get("type_id", 0)
            value = change.get("value", 0)
            name = change.get("name", "")
            self._state[(node_id, tid)] = value
            success.append({"value": value, "type_id": tid, "name": name})

        return {
            "status": 0,
            "response": {
                "data": {"success": success, "failed": []},
                "length": len(success),
            },
            "request": request_data,
        }


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="module")
async def fake_superwise():
    fake = FakeSuperWISE()
    runner = web.AppRunner(fake._app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", FAKE_PORT)
    await site.start()
    yield fake
    await runner.cleanup()


@pytest_asyncio.fixture(scope="module")
async def config(fake_superwise):
    return AppConfig(
        modbus=ModbusConfig(host="127.0.0.1"),
        superwise=SuperWiseConfig(
            host=f"127.0.0.1:{FAKE_PORT}",
            user=FAKE_USER,
            password=FAKE_PASSWORD,
            timeout=10,
        ),
        damper_rooms=[TEST_ROOM, TEST_ROOM_2],
    )


@pytest_asyncio.fixture(scope="module")
async def superwise_client(config):
    return SuperWiseClient(config)


@pytest_asyncio.fixture(scope="module")
async def mcp_server(config):
    old_config = srv._config
    old_client = srv._client
    old_sw = srv._superwise_client

    srv._config = config
    srv._client = None
    srv._superwise_client = SuperWiseClient(config)
    yield
    srv._config = old_config
    srv._client = old_client
    srv._superwise_client = old_sw


# ─── SuperWiseClient direct tests ─────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="module")
class TestSuperWiseClientDirect:
    async def test_login_succeeds(self, superwise_client):
        cookies = await superwise_client._login()
        assert cookies, "Expected session cookies"
        assert "session=" in cookies

    async def test_login_caches_session(self, superwise_client):
        cookies1 = await superwise_client._login()
        cookies2 = await superwise_client._login()
        assert cookies1 == cookies2

    async def test_read_damper_value(self, superwise_client):
        value = await superwise_client.get_damper_value(TEST_ROOM)
        assert value in (0, 1)

    async def test_read_returns_int(self, superwise_client):
        value = await superwise_client.get_damper_value(TEST_ROOM)
        assert isinstance(value, int)

    async def test_read_initial_values(self, superwise_client, fake_superwise):
        fake_superwise._state[(3, 1011)] = 0
        fake_superwise._state[(10, 1011)] = 1

        val1 = await superwise_client.get_damper_value(TEST_ROOM)
        val2 = await superwise_client.get_damper_value(TEST_ROOM_2)
        assert val1 == 0
        assert val2 == 1

    async def test_set_damper_on(self, superwise_client, fake_superwise):
        fake_superwise._state[(3, 1011)] = 0

        result = await superwise_client.set_damper_value(TEST_ROOM, 1)
        assert "success" in result
        assert result["success"][0]["value"] == 1
        assert fake_superwise._state[(3, 1011)] == 1

    async def test_set_damper_off(self, superwise_client, fake_superwise):
        fake_superwise._state[(3, 1011)] = 1

        result = await superwise_client.set_damper_value(TEST_ROOM, 0)
        assert result["success"][0]["value"] == 0
        assert fake_superwise._state[(3, 1011)] == 0

    async def test_set_and_read_back(self, superwise_client, fake_superwise):
        fake_superwise._state[(3, 1011)] = 0

        await superwise_client.set_damper_value(TEST_ROOM, 1)
        value = await superwise_client.get_damper_value(TEST_ROOM)
        assert value == 1

        await superwise_client.set_damper_value(TEST_ROOM, 0)
        value = await superwise_client.get_damper_value(TEST_ROOM)
        assert value == 0

    async def test_set_invalid_value_rejected(self, superwise_client):
        with pytest.raises(ValueError, match="0 or 1"):
            await superwise_client.set_damper_value(TEST_ROOM, 2)

    async def test_rooms_are_independent(self, superwise_client, fake_superwise):
        fake_superwise._state[(3, 1011)] = 0
        fake_superwise._state[(10, 1011)] = 0

        await superwise_client.set_damper_value(TEST_ROOM, 1)
        val2 = await superwise_client.get_damper_value(TEST_ROOM_2)
        assert val2 == 0


# ─── MCP tool tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="module")
class TestDamperMCPTools:
    async def test_damper_tools_listed(self, mcp_server):
        tools = await srv.list_tools()
        names = [t.name for t in tools]
        assert "get_damper_status" in names
        assert "set_damper" in names

    async def test_damper_tools_have_room_enum(self, mcp_server):
        tools = await srv.list_tools()
        status_tool = next(t for t in tools if t.name == "get_damper_status")
        room_enum = status_tool.inputSchema["properties"]["room"]["enum"]
        assert TEST_ROOM.name in room_enum
        assert TEST_ROOM_2.name in room_enum

    async def test_set_damper_schema(self, mcp_server):
        tools = await srv.list_tools()
        set_tool = next(t for t in tools if t.name == "set_damper")
        props = set_tool.inputSchema["properties"]
        assert "room" in props
        assert "value" in props
        assert props["value"]["enum"] == [0, 1]

    async def test_get_status_all_rooms(self, mcp_server, fake_superwise):
        fake_superwise._state[(3, 1011)] = 0
        fake_superwise._state[(10, 1011)] = 1

        result = await srv.call_tool("get_damper_status", {})
        text = result[0].text
        assert TEST_ROOM.label in text
        assert TEST_ROOM_2.label in text
        assert "OFF" in text
        assert "ON" in text

    async def test_get_status_single_room(self, mcp_server, fake_superwise):
        fake_superwise._state[(3, 1011)] = 1

        result = await srv.call_tool("get_damper_status", {"room": TEST_ROOM.name})
        text = result[0].text
        assert TEST_ROOM.label in text
        assert "ON" in text

    async def test_get_status_unknown_room(self, mcp_server):
        result = await srv.call_tool("get_damper_status", {"room": "nonexistent"})
        assert "Unknown room" in result[0].text

    async def test_set_damper_success(self, mcp_server, fake_superwise):
        fake_superwise._state[(3, 1011)] = 0

        result = await srv.call_tool("set_damper", {"room": TEST_ROOM.name, "value": 1})
        assert "✅" in result[0].text
        assert fake_superwise._state[(3, 1011)] == 1

    async def test_set_damper_and_verify_status(self, mcp_server, fake_superwise):
        fake_superwise._state[(3, 1011)] = 0

        await srv.call_tool("set_damper", {"room": TEST_ROOM.name, "value": 1})
        status = await srv.call_tool("get_damper_status", {"room": TEST_ROOM.name})
        assert "ON" in status[0].text

        await srv.call_tool("set_damper", {"room": TEST_ROOM.name, "value": 0})
        status = await srv.call_tool("get_damper_status", {"room": TEST_ROOM.name})
        assert "OFF" in status[0].text

    async def test_set_damper_unknown_room(self, mcp_server):
        result = await srv.call_tool("set_damper", {"room": "nonexistent", "value": 0})
        assert "Unknown room" in result[0].text

    async def test_damper_tools_hidden_without_config(self, mcp_server):
        saved_config = srv._config
        saved_sw = srv._superwise_client

        srv._config = AppConfig(modbus=ModbusConfig(host="127.0.0.1"))
        srv._superwise_client = None

        tools = await srv.list_tools()
        names = [t.name for t in tools]
        assert "get_damper_status" not in names
        assert "set_damper" not in names

        srv._config = saved_config
        srv._superwise_client = saved_sw

    async def test_set_damper_without_superwise_client(self, mcp_server):
        saved = srv._superwise_client
        srv._superwise_client = None

        result = await srv.call_tool("set_damper", {"room": TEST_ROOM.name, "value": 0})
        assert "not configured" in result[0].text

        result = await srv.call_tool("get_damper_status", {})
        assert "not configured" in result[0].text

        srv._superwise_client = saved


# ─── Login edge cases ────────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="module")
class TestLoginEdgeCases:
    async def test_bad_password_raises(self, fake_superwise):
        config = AppConfig(
            modbus=ModbusConfig(host="127.0.0.1"),
            superwise=SuperWiseConfig(
                host=f"127.0.0.1:{FAKE_PORT}",
                user=FAKE_USER,
                password="wrong-password",
                timeout=5,
            ),
            damper_rooms=[TEST_ROOM],
        )
        client = SuperWiseClient(config)
        with pytest.raises(ConnectionError):
            await client._login()

    async def test_bad_user_raises(self, fake_superwise):
        config = AppConfig(
            modbus=ModbusConfig(host="127.0.0.1"),
            superwise=SuperWiseConfig(
                host=f"127.0.0.1:{FAKE_PORT}",
                user="wrong@user.com",
                password=FAKE_PASSWORD,
                timeout=5,
            ),
            damper_rooms=[TEST_ROOM],
        )
        client = SuperWiseClient(config)
        with pytest.raises(ConnectionError):
            await client._login()
