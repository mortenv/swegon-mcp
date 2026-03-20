"""Modbus TCP client wrapper for Swegon SuperWISE."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from .config import (
    AppConfig,
    TemperatureRegister,
    FanModeRegister,
    AirBoostRegister,
    StatusRegister,
)


class SwegonModbusClient:
    def __init__(self, config: AppConfig):
        self.config = config
        self._client: AsyncModbusTcpClient | None = None
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def _connected(self):
        async with self._lock:
            client = AsyncModbusTcpClient(
                host=self.config.modbus.host,
                port=self.config.modbus.port,
                timeout=self.config.modbus.timeout,
            )
            try:
                await client.connect()
                if not client.connected:
                    raise ConnectionError(
                        f"Could not connect to SuperWISE at "
                        f"{self.config.modbus.host}:{self.config.modbus.port}"
                    )
                yield client
            finally:
                client.close()

    async def get_temperature(self, register: TemperatureRegister) -> float:
        async with self._connected() as client:
            result = await client.read_holding_registers(
                address=register.address,
                count=1,
                device_id=self.config.modbus.unit_id,
            )
            if result.isError():
                raise ModbusException(f"Failed to read register {register.address}")
            raw = result.registers[0]
            return raw * register.scale

    async def set_temperature(
        self, register: TemperatureRegister, value: float
    ) -> None:
        if not (register.min <= value <= register.max):
            raise ValueError(
                f"Temperature {value} out of allowed range "
                f"[{register.min}–{register.max}] for {register.label}"
            )
        raw = int(round(value / register.scale))
        async with self._connected() as client:
            result = await client.write_register(
                address=register.address,
                value=raw,
                device_id=self.config.modbus.unit_id,
            )
            if result.isError():
                raise ModbusException(f"Failed to write register {register.address}")

    async def set_fan_mode(self, register: FanModeRegister, mode: str) -> None:
        if mode not in register.values:
            allowed = list(register.values.keys())
            raise ValueError(f"Unknown fan mode '{mode}'. Allowed: {allowed}")
        value = register.values[mode]
        async with self._connected() as client:
            if register.type == "coil":
                result = await client.write_coil(
                    address=register.address,
                    value=bool(value),
                    device_id=self.config.modbus.unit_id,
                )
            else:
                result = await client.write_register(
                    address=register.address,
                    value=value,
                    device_id=self.config.modbus.unit_id,
                )
            if result.isError():
                raise ModbusException(
                    f"Failed to write fan mode register {register.address}"
                )

    async def trigger_air_boost(self, register: AirBoostRegister) -> None:
        """Trigger SuperWISE 'Air boost' (Manuell forsering).
        SuperWISE manages boost duration and auto-revert — no timer needed here."""
        async with self._connected() as client:
            if register.type == "coil":
                result = await client.write_coil(
                    address=register.address,
                    value=True,
                    device_id=self.config.modbus.unit_id,
                )
            else:
                result = await client.write_register(
                    address=register.address,
                    value=1,
                    device_id=self.config.modbus.unit_id,
                )
            if result.isError():
                raise ModbusException(
                    f"Failed to trigger air boost register {register.address}"
                )

    async def get_status(self, register: StatusRegister) -> float:
        async with self._connected() as client:
            if register.type == "input":
                result = await client.read_input_registers(
                    address=register.address,
                    count=1,
                    device_id=self.config.modbus.unit_id,
                )
            else:
                result = await client.read_holding_registers(
                    address=register.address,
                    count=1,
                    device_id=self.config.modbus.unit_id,
                )
            if result.isError():
                raise ModbusException(
                    f"Failed to read status register {register.address}"
                )
            raw = result.registers[0]
            return raw * register.scale
