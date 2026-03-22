#!/usr/bin/env python3
"""
Local MCP server test — no SuperWISE needed.

This script simulates what an AI assistant (Claude, OpenClaw) does when it
calls the MCP tools. All Modbus calls are mocked, so you can run this anywhere
to verify that:

  ✅ The server starts correctly
  ✅ Tools are listed with correct names and descriptions
  ✅ Room whitelist is enforced (unknown rooms are rejected)
  ✅ Temperature limits are enforced per room
  ✅ Air boost delegates correctly
  ✅ Status reads work
  ✅ All tool responses are human-readable

Usage:
    uv run python test_local.py
    python test_local.py

No config.yaml needed — uses built-in test fixtures.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).parent))

from swegon_mcp.config import (
    AppConfig,
    ModbusConfig,
    RegistersConfig,
    BoostConfig,
    TemperatureRegister,
    AirBoostRegister,
    StatusRegister,
)
from swegon_mcp.modbus_client import SwegonModbusClient
import swegon_mcp.server as srv

# ─────────────────────────────────────────────
# Test config — mirrors a typical home setup
# ─────────────────────────────────────────────
TEST_CONFIG = AppConfig(
    modbus=ModbusConfig(host="10.0.0.100", port=502),
    registers=RegistersConfig(
        temperature_setpoints=[
            TemperatureRegister(
                name="stue",
                label="Stue",
                address=1001,
                min=18,
                max=26,
                scale=0.01,
                unit="°C",
            ),
            TemperatureRegister(
                name="soverom",
                label="Soverom",
                address=1002,
                min=15,
                max=22,
                scale=0.01,
                unit="°C",
            ),
            TemperatureRegister(
                name="kontor",
                label="Kontor",
                address=1003,
                min=16,
                max=25,
                scale=0.01,
                unit="°C",
            ),
        ],
        air_boosts=[
            AirBoostRegister(
                name="stue", label="Stue boost", address=2001, type="coil"
            ),
            AirBoostRegister(
                name="soverom", label="Soverom boost", address=2002, type="coil"
            ),
        ],
        status_reads=[
            StatusRegister(
                name="ute",
                label="Utetemperatur",
                address=3001,
                type="input",
                scale=0.01,
                unit="°C",
            ),
            StatusRegister(
                name="tilluft",
                label="Tilluftstemperatur",
                address=3002,
                type="input",
                scale=0.01,
                unit="°C",
            ),
        ],
    ),
    boost=BoostConfig(),
)

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

PASS = "✅"
FAIL = "❌"
results = []


def check(name: str, ok: bool, detail: str = ""):
    icon = PASS if ok else FAIL
    msg = f"  {icon} {name}"
    if detail:
        msg += f"\n     → {detail}"
    print(msg)
    results.append((name, ok))


class _FakeResult:
    """Fake Modbus result object (synchronous .isError(), no await needed)."""

    def __init__(self, ok=True):
        self._ok = ok
        self.registers = [0]
        self.bits = [False]

    def isError(self):
        return not self._ok


def make_mock_modbus_ctx():
    """Return a fake async context manager simulating a Modbus connection."""
    result = _FakeResult(ok=True)
    mock_modbus = AsyncMock()
    mock_modbus.write_register = AsyncMock(return_value=result)
    mock_modbus.write_coil = AsyncMock(return_value=result)
    mock_modbus.read_holding_registers = AsyncMock(return_value=result)
    mock_modbus.read_input_registers = AsyncMock(return_value=result)
    mock_modbus.read_coils = AsyncMock(return_value=result)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_connected():
        yield mock_modbus

    return fake_connected


def make_mock_client(temp_value=2100, status_value=-500):
    """
    Mock client that:
    - Keeps real set_temperature validation (range checks run for real)
    - Mocks the Modbus transport (no network needed)
    - Returns fake values for reads
    """
    client = SwegonModbusClient(TEST_CONFIG)
    client.get_temperature = AsyncMock(return_value=temp_value * 0.01)
    client.get_status = AsyncMock(return_value=status_value * 0.01)
    client._connected = make_mock_modbus_ctx()
    client.trigger_air_boost = AsyncMock(return_value=None)
    return client


# ─────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────


async def test_tool_listing():
    """Tools are listed with names and descriptions."""
    print("\n📋 Tool listing")
    srv._config = TEST_CONFIG
    srv._client = make_mock_client()

    tools = await srv.list_tools()
    names = [t.name for t in tools]

    check("get_status listed", "get_status" in names)
    check("get_temperature_setpoints listed", "get_temperature_setpoints" in names)
    check("set_temperature listed", "set_temperature" in names)
    check("set_fan_mode listed", "set_fan_mode" in names)
    check("boost_fan listed", "boost_fan" in names)

    set_temp_tool = next(t for t in tools if t.name == "set_temperature")
    rooms_in_schema = set_temp_tool.inputSchema["properties"]["room"].get("enum", [])
    check(
        "Rooms in set_temperature schema match config",
        set(rooms_in_schema) == {"stue", "soverom", "kontor"},
        f"Got: {rooms_in_schema}",
    )


async def test_get_status():
    """Status reads return formatted values."""
    print("\n📊 get_status")
    srv._config = TEST_CONFIG
    srv._client = make_mock_client(status_value=-500)  # -5.00°C

    result = await srv.call_tool("get_status", {})
    text = result[0].text

    check("Returns text", bool(text))
    check("Contains Utetemperatur", "Utetemperatur" in text, text)
    check("Contains Tilluftstemperatur", "Tilluftstemperatur" in text)


async def test_get_temperature_setpoints():
    """Reading setpoints returns values for all rooms."""
    print("\n🌡️  get_temperature_setpoints")
    srv._config = TEST_CONFIG
    srv._client = make_mock_client(temp_value=2100)  # 21.00°C

    result = await srv.call_tool("get_temperature_setpoints", {})
    text = result[0].text

    check("Returns text", bool(text))
    check("Contains Stue", "Stue" in text, text)
    check("Contains Soverom", "Soverom" in text)
    check("Contains Kontor", "Kontor" in text)


async def test_set_temperature_valid():
    """Setting a valid temperature succeeds."""
    print("\n🌡️  set_temperature — valid cases")
    srv._config = TEST_CONFIG
    srv._client = make_mock_client()

    result = await srv.call_tool(
        "set_temperature", {"room": "stue", "temperature": 22.0}
    )
    text = result[0].text

    check("Returns success message", "✅" in text, text)
    check("Mentions room name", "Stue" in text)
    check("Mentions temperature", "22" in text)
    check("No error in response", "❌" not in text)


async def test_set_temperature_out_of_range():
    """Temperatures outside room limits are rejected."""
    print("\n🌡️  set_temperature — range enforcement")
    srv._config = TEST_CONFIG
    srv._client = make_mock_client()

    # Stue: min=18, max=26
    too_hot = await srv.call_tool(
        "set_temperature", {"room": "stue", "temperature": 30.0}
    )
    check("Rejects 30°C for Stue (max=26)", "❌" in too_hot[0].text, too_hot[0].text)

    too_cold = await srv.call_tool(
        "set_temperature", {"room": "stue", "temperature": 10.0}
    )
    check("Rejects 10°C for Stue (min=18)", "❌" in too_cold[0].text, too_cold[0].text)

    # Soverom has stricter max=22
    borderline = await srv.call_tool(
        "set_temperature", {"room": "soverom", "temperature": 23.0}
    )
    check(
        "Rejects 23°C for Soverom (max=22)",
        "❌" in borderline[0].text,
        borderline[0].text,
    )

    boundary_ok = await srv.call_tool(
        "set_temperature", {"room": "soverom", "temperature": 22.0}
    )
    check(
        "Accepts 22°C for Soverom (at max)",
        "✅" in boundary_ok[0].text,
        boundary_ok[0].text,
    )


async def test_room_whitelist():
    """Unknown rooms are rejected, not silently ignored."""
    print("\n🔒 Room whitelist")
    srv._config = TEST_CONFIG
    srv._client = make_mock_client()

    result = await srv.call_tool(
        "set_temperature", {"room": "garasje", "temperature": 20.0}
    )
    check(
        "Rejects unknown room 'garasje'",
        "Unknown room" in result[0].text,
        result[0].text,
    )

    result2 = await srv.call_tool("set_temperature", {"room": "", "temperature": 20.0})
    check(
        "Rejects empty room name",
        "Unknown room" in result2[0].text or "❌" in result2[0].text,
    )


async def test_boost_fan():
    """Boost triggers air boost and mentions SuperWISE."""
    print("\n💨 boost_fan")
    client = make_mock_client()
    srv._config = TEST_CONFIG
    srv._client = client

    result = await srv.call_tool("boost_fan", {"unit": "stue"})
    text = result[0].text

    check("Returns success message", "✅" in text, text)
    check("Mentions SuperWISE manages revert", "SuperWISE" in text)
    check("trigger_air_boost mock was called", client.trigger_air_boost.call_count >= 1)

    # Reset for next call
    client.trigger_air_boost.reset_mock()

    unknown = await srv.call_tool("boost_fan", {"unit": "bod"})
    check(
        "Rejects unknown boost unit 'bod'",
        "Unknown boost unit" in unknown[0].text,
        unknown[0].text,
    )
    check(
        "trigger_air_boost not called for unknown unit",
        client.trigger_air_boost.call_count == 0,
    )


async def test_no_revert_timer():
    """Server must not schedule its own revert timer (SuperWISE handles it)."""
    print("\n⏱️  No revert timer in server")
    import inspect

    source = inspect.getsource(srv)
    check(
        "No asyncio.create_task in server.py",
        "create_task" not in source,
        "Found create_task — remove it, SuperWISE handles revert",
    )


# ─────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────


async def main():
    print("=" * 55)
    print("  swegon-mcp local test (no hardware needed)")
    print("=" * 55)

    await test_tool_listing()
    await test_get_status()
    await test_get_temperature_setpoints()
    await test_set_temperature_valid()
    await test_set_temperature_out_of_range()
    await test_room_whitelist()
    await test_boost_fan()
    await test_no_revert_timer()

    passed = sum(1 for _, ok in results if ok)
    failed = sum(1 for _, ok in results if not ok)

    print()
    print("=" * 55)
    print(f"  {PASS} {passed} passed   {FAIL} {failed} failed   ({len(results)} total)")
    print("=" * 55)

    if failed:
        print("\nFailed tests:")
        for name, ok in results:
            if not ok:
                print(f"  {FAIL} {name}")
        sys.exit(1)
    else:
        print("\nAll tests passed — MCP server behaves correctly.")
        print("Next step: connect to SuperWISE and run test_connection.py")


if __name__ == "__main__":
    asyncio.run(main())
