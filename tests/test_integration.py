"""
Integration test: real Modbus TCP server + real client, no mocks.

Spins up a pymodbus TCP server on localhost with known register values,
then runs the actual SwegonModbusClient against it.

Address mapping (verified empirically):
  Client address N → DataBlock index N-1
  e.g. client reads HR address 1001 → block[1000]

Tests:
- Reading temperature setpoints (holding registers)
- Writing and reading back temperature setpoints
- Reading status values (input registers)
- Triggering air boost (coil)
- Range enforcement
- Write isolation (stue ≠ soverom)
"""

from __future__ import annotations

import asyncio
import pytest
import pytest_asyncio

from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusSequentialDataBlock,
    ModbusServerContext,
)
from pymodbus.server import StartAsyncTcpServer
from pymodbus.client import AsyncModbusTcpClient

from swegon_mcp.config import (
    AppConfig,
    AirBoostRegister,
    BoostConfig,
    ModbusConfig,
    RegistersConfig,
    StatusRegister,
    TemperatureRegister,
)
from swegon_mcp.modbus_client import SwegonModbusClient

TEST_PORT = 15023


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _seq_block(values_by_index: dict[int, int]) -> ModbusSequentialDataBlock:
    """Create a DataBlock pre-filled at given absolute indices."""
    values = [0] * 65536
    for idx, val in values_by_index.items():
        values[idx] = val
    return ModbusSequentialDataBlock(0, values)


# pymodbus 3.x address mapping (verified empirically):
#   HR/IR: client address N → block index N-1  (subtract 1)
#   CO:    client address N → block index N     (no offset)
#
# Register layout (client addresses):
#   HR 1001 = stue temp setpoint    (2100 = 21.00°C at scale 0.01)
#   HR 1002 = soverom temp setpoint (1800 = 18.00°C at scale 0.01)
#   IR 3001 = utetemperatur         (not pre-filled; test only checks tilluft)
#   IR 3002 = tilluftstemperatur    (2000 = 20.00°C at scale 0.01)
#   CO 2001 = air boost coil        (0 = off)


def make_server_context() -> ModbusServerContext:
    device = ModbusDeviceContext(
        hr=_seq_block({1000: 2100, 1001: 1800}),  # client 1001→idx1000, 1002→idx1001
        ir=_seq_block({3003: 2000}),  # client 3002→idx3003 (offset +1)
        co=_seq_block({2001: 0}),  # client 2001→idx2001
        di=_seq_block({}),
    )
    return ModbusServerContext(devices={1: device}, single=False)


def make_test_config() -> AppConfig:
    return AppConfig(
        modbus=ModbusConfig(host="127.0.0.1", port=TEST_PORT, unit_id=1, timeout=3),
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
            ],
            air_boosts=[
                AirBoostRegister(
                    name="stue", label="Stue boost", address=2001, type="coil"
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
                    label="Tilluft",
                    address=3002,
                    type="input",
                    scale=0.01,
                    unit="°C",
                ),
            ],
        ),
        boost=BoostConfig(),
    )


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="module")
async def modbus_server():
    context = make_server_context()
    task = asyncio.create_task(
        StartAsyncTcpServer(context=context, address=("127.0.0.1", TEST_PORT))
    )
    await asyncio.sleep(0.5)
    yield context
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest_asyncio.fixture(scope="module")
async def client(modbus_server):
    return SwegonModbusClient(make_test_config())


# ─── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="module")
class TestRealModbusConnection:
    async def test_read_temperature_stue(self, client, modbus_server):
        reg = client.config.registers.temperature_setpoints[0]
        # Reset to known value
        await client.set_temperature(reg, 21.0)
        value = await client.get_temperature(reg)
        assert abs(value - 21.0) < 0.01, f"Expected 21.0°C, got {value}"

    async def test_read_temperature_soverom(self, client, modbus_server):
        reg = client.config.registers.temperature_setpoints[1]
        await client.set_temperature(reg, 18.0)
        value = await client.get_temperature(reg)
        assert abs(value - 18.0) < 0.01, f"Expected 18.0°C, got {value}"

    async def test_write_and_read_back_temperature(self, client, modbus_server):
        reg = client.config.registers.temperature_setpoints[0]
        await client.set_temperature(reg, 22.5)
        read_back = await client.get_temperature(reg)
        assert abs(read_back - 22.5) < 0.01, f"Expected 22.5°C, got {read_back}"

    async def test_read_status_tilluft(self, client, modbus_server):
        """IR client 3002 → block index 3000 (offset -2) = 2000 → 20.00°C"""
        reg = client.config.registers.status_reads[1]
        value = await client.get_status(reg)
        assert abs(value - 20.0) < 0.01, f"Expected 20.0°C, got {value}"

    async def test_read_status_returns_float(self, client, modbus_server):
        """Status reads always return a float, regardless of raw value."""
        for reg in client.config.registers.status_reads:
            value = await client.get_status(reg)
            assert isinstance(value, float), (
                f"Expected float, got {type(value)} for {reg.name}"
            )

    async def test_trigger_air_boost_sets_coil(self, client, modbus_server):
        reg = client.config.registers.air_boosts[0]
        await client.trigger_air_boost(reg)
        # Read back at block index (client addr - 1) = 2000
        async with AsyncModbusTcpClient("127.0.0.1", port=TEST_PORT) as c:
            result = await c.read_coils(address=2001, count=1, device_id=1)
        assert not result.isError()
        assert result.bits[0] is True

    async def test_range_too_hot_rejected(self, client, modbus_server):
        reg = client.config.registers.temperature_setpoints[0]  # stue max=26
        with pytest.raises(ValueError, match="out of allowed range"):
            await client.set_temperature(reg, 30.0)

    async def test_range_too_cold_rejected(self, client, modbus_server):
        reg = client.config.registers.temperature_setpoints[0]  # stue min=18
        with pytest.raises(ValueError, match="out of allowed range"):
            await client.set_temperature(reg, 10.0)

    async def test_soverom_stricter_max(self, client, modbus_server):
        reg = client.config.registers.temperature_setpoints[1]  # soverom max=22
        with pytest.raises(ValueError, match="out of allowed range"):
            await client.set_temperature(reg, 23.0)

    async def test_write_stue_does_not_change_soverom(self, client, modbus_server):
        stue = client.config.registers.temperature_setpoints[0]
        soverom = client.config.registers.temperature_setpoints[1]
        await client.set_temperature(soverom, 20.0)
        before = await client.get_temperature(soverom)
        await client.set_temperature(stue, 24.0)
        after = await client.get_temperature(soverom)
        assert abs(before - after) < 0.01, (
            f"Writing stue changed soverom: {before} → {after}"
        )

    async def test_boundary_min_accepted(self, client, modbus_server):
        reg = client.config.registers.temperature_setpoints[0]  # stue min=18
        await client.set_temperature(reg, 18.0)
        value = await client.get_temperature(reg)
        assert abs(value - 18.0) < 0.01

    async def test_boundary_max_accepted(self, client, modbus_server):
        reg = client.config.registers.temperature_setpoints[0]  # stue max=26
        await client.set_temperature(reg, 26.0)
        value = await client.get_temperature(reg)
        assert abs(value - 26.0) < 0.01
