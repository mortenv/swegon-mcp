"""
Security tests: verify that the MCP server enforces the register whitelist
and temperature range limits. No real Modbus connection needed.
"""
import pytest
from unittest.mock import AsyncMock, patch

from swegon_mcp.config import (
    AppConfig, ModbusConfig, RegistersConfig, BoostConfig,
    TemperatureRegister, AirBoostRegister,
)
from swegon_mcp.modbus_client import SwegonModbusClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_config(rooms: list[dict] | None = None) -> AppConfig:
    """Build a minimal AppConfig with optional room overrides."""
    if rooms is None:
        rooms = [
            {"name": "living_room", "label": "Living Room", "address": 1001, "min": 18, "max": 25},
            {"name": "bedroom", "label": "Bedroom", "address": 1002, "min": 16, "max": 23},
        ]
    return AppConfig(
        modbus=ModbusConfig(host="127.0.0.1", port=502),
        registers=RegistersConfig(
            temperature_setpoints=[TemperatureRegister(**r) for r in rooms],
            air_boosts=[AirBoostRegister(name="main", label="Main AHU", address=4001)],
        ),
        boost=BoostConfig(),
    )


@pytest.fixture
def config():
    return make_config()


@pytest.fixture
def client(config):
    return SwegonModbusClient(config)


# ---------------------------------------------------------------------------
# Temperature range validation
# ---------------------------------------------------------------------------

class TestTemperatureRangeValidation:
    """set_temperature must reject values outside per-register min/max."""

    @pytest.mark.asyncio
    async def test_rejects_temperature_above_max(self, client, config):
        reg = config.registers.temperature_setpoints[0]  # living_room, max=25
        with pytest.raises(ValueError, match="out of allowed range"):
            await client.set_temperature(reg, 30.0)

    @pytest.mark.asyncio
    async def test_rejects_temperature_below_min(self, client, config):
        reg = config.registers.temperature_setpoints[0]  # living_room, min=18
        with pytest.raises(ValueError, match="out of allowed range"):
            await client.set_temperature(reg, 10.0)

    @pytest.mark.asyncio
    async def test_accepts_temperature_at_min_boundary(self, client, config):
        reg = config.registers.temperature_setpoints[0]  # living_room, min=18
        client.set_temperature = AsyncMock(return_value=None)
        await client.set_temperature(reg, 18.0)  # should not raise
        client.set_temperature.assert_called_once_with(reg, 18.0)

    @pytest.mark.asyncio
    async def test_accepts_temperature_at_max_boundary(self, client, config):
        reg = config.registers.temperature_setpoints[0]  # living_room, max=25
        client.set_temperature = AsyncMock(return_value=None)
        await client.set_temperature(reg, 25.0)  # should not raise
        client.set_temperature.assert_called_once_with(reg, 25.0)

    @pytest.mark.asyncio
    async def test_each_room_uses_its_own_range(self, client, config):
        """bedroom has max=23, living_room has max=25 — cross-check."""
        bedroom = config.registers.temperature_setpoints[1]  # max=23
        with pytest.raises(ValueError, match="out of allowed range"):
            await client.set_temperature(bedroom, 24.0)


# ---------------------------------------------------------------------------
# Room whitelist enforcement (via server tool dispatch)
# ---------------------------------------------------------------------------

class TestRoomWhitelist:
    """The MCP server must only allow rooms listed in config."""

    @pytest.mark.asyncio
    async def test_unknown_room_returns_error(self):
        """Requesting a room not in config should return an error, not crash."""
        import swegon_mcp.server as srv

        config = make_config()
        srv._config = config
        srv._client = SwegonModbusClient(config)

        result = await srv.call_tool("set_temperature", {"room": "garage", "temperature": 20.0})
        assert len(result) == 1
        assert "Unknown room" in result[0].text

    @pytest.mark.asyncio
    async def test_known_room_does_not_return_unknown_error(self):
        """A valid room should not produce an 'Unknown room' error (even if Modbus fails)."""
        import swegon_mcp.server as srv

        config = make_config()
        srv._config = config
        client = SwegonModbusClient(config)
        # Make Modbus fail with a connection error so we don't need real hardware
        client.set_temperature = AsyncMock(side_effect=ConnectionError("no hw"))
        srv._client = client

        result = await srv.call_tool("set_temperature", {"room": "living_room", "temperature": 21.0})
        assert "Unknown room" not in result[0].text
        assert "Connection error" in result[0].text

    @pytest.mark.asyncio
    async def test_unknown_boost_unit_returns_error(self):
        """Requesting boost for an unconfigured unit should return an error."""
        import swegon_mcp.server as srv

        config = make_config()
        srv._config = config
        srv._client = SwegonModbusClient(config)

        result = await srv.call_tool("boost_fan", {"unit": "attic"})
        assert "Unknown boost unit" in result[0].text


# ---------------------------------------------------------------------------
# Boost delegates to SuperWISE (no revert timer in MCP server)
# ---------------------------------------------------------------------------

class TestBoostDelegation:
    """Boost should call trigger_air_boost and NOT schedule any revert timer."""

    @pytest.mark.asyncio
    async def test_boost_calls_trigger_air_boost(self):
        import swegon_mcp.server as srv

        config = make_config()
        srv._config = config
        client = SwegonModbusClient(config)
        client.trigger_air_boost = AsyncMock(return_value=None)
        srv._client = client

        result = await srv.call_tool("boost_fan", {"unit": "main"})
        client.trigger_air_boost.assert_called_once()
        assert "SuperWISE" in result[0].text

    @pytest.mark.asyncio
    async def test_boost_does_not_import_asyncio_create_task(self):
        """server.py should not use asyncio.create_task for revert logic."""
        import inspect
        import swegon_mcp.server as srv
        source = inspect.getsource(srv)
        assert "create_task" not in source, (
            "server.py must not schedule revert timers — SuperWISE handles this"
        )
