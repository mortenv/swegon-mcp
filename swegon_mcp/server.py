"""MCP server exposing Swegon WISE ventilation control tools."""
from __future__ import annotations

import logging
from typing import Any

import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

from .config import load_config
from .modbus_client import SwegonModbusClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("swegon-mcp")

app = Server("swegon-mcp")
_client: SwegonModbusClient | None = None
_config = None


def get_client() -> SwegonModbusClient:
    if _client is None:
        raise RuntimeError("Client not initialized")
    return _client


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    cfg = _config
    rooms = [r.name for r in cfg.registers.temperature_setpoints]
    fan_units = [f.name for f in cfg.registers.fan_modes]
    fan_modes_example = []
    if cfg.registers.fan_modes:
        fan_modes_example = list(cfg.registers.fan_modes[0].values.keys())

    boost_units = [b.name for b in cfg.registers.air_boosts]

    tools = [
        types.Tool(
            name="get_status",
            description="Get current status readings from the ventilation system (temperatures, etc.)",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="get_temperature_setpoints",
            description="Get current temperature setpoints for all configured rooms.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="set_temperature",
            description=f"Set the temperature setpoint for a room. Available rooms: {rooms}",
            inputSchema={
                "type": "object",
                "properties": {
                    "room": {
                        "type": "string",
                        "description": f"Room name. One of: {rooms}",
                        "enum": rooms,
                    },
                    "temperature": {
                        "type": "number",
                        "description": "Target temperature in °C",
                    },
                },
                "required": ["room", "temperature"],
            },
        ),
        types.Tool(
            name="set_fan_mode",
            description=(
                f"Set the fan/ventilation mode. "
                f"Available units: {fan_units}. "
                f"Available modes: {fan_modes_example}"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "unit": {
                        "type": "string",
                        "description": f"Fan unit name. One of: {fan_units}",
                        "enum": fan_units if fan_units else ["main"],
                    },
                    "mode": {
                        "type": "string",
                        "description": f"Fan mode. One of: {fan_modes_example}",
                    },
                },
                "required": ["unit", "mode"],
            },
        ),
        types.Tool(
            name="boost_fan",
            description=(
                "Trigger SuperWISE 'Air boost' (Manuell forsering) for a ventilation unit. "
                "SuperWISE manages boost duration and auto-revert — no timer needed. "
                f"Available units: {boost_units}"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "unit": {
                        "type": "string",
                        "description": f"Boost unit name. One of: {boost_units}",
                        "enum": boost_units if boost_units else ["main"],
                    },
                },
                "required": ["unit"],
            },
        ),
    ]
    return tools


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    client = get_client()
    cfg = _config

    try:
        if name == "get_status":
            if not cfg.registers.status_reads:
                return [types.TextContent(type="text", text="No status registers configured.")]
            lines = []
            for reg in cfg.registers.status_reads:
                value = await client.get_status(reg)
                lines.append(f"{reg.label}: {value:.1f} {reg.unit}")
            return [types.TextContent(type="text", text="\n".join(lines))]

        elif name == "get_temperature_setpoints":
            if not cfg.registers.temperature_setpoints:
                return [types.TextContent(type="text", text="No temperature registers configured.")]
            lines = []
            for reg in cfg.registers.temperature_setpoints:
                value = await client.get_temperature(reg)
                lines.append(f"{reg.label}: {value:.1f} {reg.unit}")
            return [types.TextContent(type="text", text="\n".join(lines))]

        elif name == "set_temperature":
            room_name = arguments["room"]
            temperature = float(arguments["temperature"])
            reg = next(
                (r for r in cfg.registers.temperature_setpoints if r.name == room_name), None
            )
            if reg is None:
                return [types.TextContent(type="text", text=f"Unknown room: {room_name}")]
            await client.set_temperature(reg, temperature)
            return [types.TextContent(
                type="text",
                text=f"✅ {reg.label} temperature setpoint set to {temperature:.1f} {reg.unit}"
            )]

        elif name == "set_fan_mode":
            unit_name = arguments["unit"]
            mode = arguments["mode"]
            reg = next(
                (r for r in cfg.registers.fan_modes if r.name == unit_name), None
            )
            if reg is None:
                return [types.TextContent(type="text", text=f"Unknown fan unit: {unit_name}")]
            await client.set_fan_mode(reg, mode)
            return [types.TextContent(
                type="text",
                text=f"✅ {reg.label} fan mode set to '{mode}'"
            )]

        elif name == "boost_fan":
            unit_name = arguments["unit"]
            reg = next(
                (r for r in cfg.registers.air_boosts if r.name == unit_name), None
            )
            if reg is None:
                return [types.TextContent(type="text", text=f"Unknown boost unit: {unit_name}")]

            await client.trigger_air_boost(reg)

            return [types.TextContent(
                type="text",
                text=(
                    f"✅ Air boost triggered for {reg.label}. "
                    f"SuperWISE will manage duration and revert automatically."
                )
            )]

        else:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

    except ValueError as e:
        return [types.TextContent(type="text", text=f"❌ Invalid input: {e}")]
    except ConnectionError as e:
        return [types.TextContent(type="text", text=f"❌ Connection error: {e}")]
    except Exception as e:
        logger.error(f"Tool error: {e}", exc_info=True)
        return [types.TextContent(type="text", text=f"❌ Error: {e}")]


def main():
    import sys

    config_path = "config.yaml"
    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    global _client, _config
    _config = load_config(config_path)
    _client = SwegonModbusClient(_config)

    logger.info(
        f"Starting swegon-mcp | SuperWISE: {_config.modbus.host}:{_config.modbus.port} | "
        f"Rooms: {[r.name for r in _config.registers.temperature_setpoints]} | "
        f"Fan units: {[f.name for f in _config.registers.fan_modes]}"
    )

    asyncio.run(mcp.server.stdio.stdio_server(app))


if __name__ == "__main__":
    main()
