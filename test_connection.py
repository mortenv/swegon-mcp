#!/usr/bin/env python3
"""
Interactive connection test for swegon-mcp.

Connects to SuperWISE and lets you pick a room and action to test.
No AI needed — run directly from the terminal.

Usage:
    uv run python test_connection.py [config.yaml]
    python test_connection.py [config.yaml]
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from swegon_mcp.config import load_config
from swegon_mcp.modbus_client import SwegonModbusClient
from swegon_mcp.superwise_client import SuperWiseClient


def pick(prompt: str, options: list[str]) -> str:
    """Show a numbered menu and return the selected option."""
    print(f"\n{prompt}")
    for i, opt in enumerate(options, 1):
        print(f"  {i}) {opt}")
    while True:
        raw = input("  → ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        print(f"  Enter a number between 1 and {len(options)}")


def ask_float(prompt: str, min_val: float, max_val: float) -> float:
    """Prompt for a number within a range."""
    while True:
        raw = input(f"  {prompt} ({min_val}–{max_val}): ").strip()
        try:
            val = float(raw)
            if min_val <= val <= max_val:
                return val
            print(f"  Must be between {min_val} and {max_val}")
        except ValueError:
            print("  Enter a valid number")


async def action_read_all_status(client, cfg, sw_client=None):
    print("\n📊 Reading status...")
    if not cfg.registers.status_reads:
        print("  No status registers configured.")
        return
    for reg in cfg.registers.status_reads:
        try:
            val = await client.get_status(reg)
            print(f"  {reg.label}: {val:.1f} {reg.unit}")
        except Exception as e:
            print(f"  ❌ {reg.label}: {e}")


async def action_read_setpoints(client, cfg, sw_client=None):
    print("\n🌡️  Reading temperature setpoints...")
    if not cfg.registers.temperature_setpoints:
        print("  No temperature registers configured.")
        return
    for reg in cfg.registers.temperature_setpoints:
        try:
            val = await client.get_temperature(reg)
            print(f"  {reg.label}: {val:.1f} {reg.unit}")
        except Exception as e:
            print(f"  ❌ {reg.label}: {e}")


async def action_set_temperature(client, cfg, sw_client=None):
    if not cfg.registers.temperature_setpoints:
        print("  No temperature registers configured.")
        return

    rooms = cfg.registers.temperature_setpoints
    labels = [f"{r.label}  ({r.min}–{r.max} °C)" for r in rooms]
    chosen_label = pick("Which room?", labels)
    reg = rooms[labels.index(chosen_label)]

    temp = ask_float(f"New setpoint for {reg.label}", reg.min, reg.max)

    print(f"\n  Setting {reg.label} → {temp:.1f} °C ...")
    try:
        await client.set_temperature(reg, temp)
        print(f"  ✅ Done! {reg.label} setpoint set to {temp:.1f} °C")

        # Read back to confirm
        val = await client.get_temperature(reg)
        print(f"  ✅ Read back: {val:.1f} °C")
        if abs(val - temp) > 0.5:
            print("  ⚠️  Value differs — SuperWISE may have clamped it.")
    except Exception as e:
        print(f"  ❌ Failed: {e}")


async def action_boost(client, cfg, sw_client=None):
    if not cfg.registers.air_boosts:
        print("  No air boost registers configured.")
        return

    boosts = cfg.registers.air_boosts
    labels = [b.label for b in boosts]
    chosen_label = pick("Boost which unit?", labels)
    reg = boosts[labels.index(chosen_label)]

    print(f"\n  Triggering air boost for {reg.label} ...")
    try:
        await client.trigger_air_boost(reg)
        print("  ✅ Air boost triggered! SuperWISE manages duration and auto-revert.")
    except Exception as e:
        print(f"  ❌ Failed: {e}")


async def action_damper_status(client, cfg, sw_client=None):
    """Read damper status for all configured rooms."""
    if not cfg.damper_rooms or not sw_client:
        print("  No damper rooms configured (or superwise not configured).")
        return

    print("\n🔧 Reading damper status...")
    for room in cfg.damper_rooms:
        try:
            val = await sw_client.get_damper_value(room)
            status = "ON" if val == 1 else "OFF" if val == 0 else str(val)
            print(f"  {room.name:<20} {room.label:<40} {status} ({val})")
        except Exception as e:
            print(f"  {room.name:<20} {room.label:<40} ❌ {e}")


async def action_set_damper(client, cfg, sw_client=None):
    """Toggle a damper value for a room."""
    if not cfg.damper_rooms or not sw_client:
        print("  No damper rooms configured (or superwise not configured).")
        return

    labels = [f"{r.name} — {r.label}" for r in cfg.damper_rooms]
    chosen_label = pick("Which room?", labels)
    room = cfg.damper_rooms[labels.index(chosen_label)]

    value_label = pick(f"Set {room.name} to:", ["OFF (0)", "ON (1)"])
    value = 1 if "ON" in value_label else 0

    print(f"\n  Setting {room.label} → {'ON' if value else 'OFF'} ...")
    try:
        result = await sw_client.set_damper_value(room, value)
        success = result.get("success", [])
        if success:
            print(f'  ✅ Done! "{success[0]["name"]}" = {success[0]["value"]}')
        else:
            print("  ✅ Done!")

        # Read back
        readback = await sw_client.get_damper_value(room)
        status = "ON" if readback == 1 else "OFF"
        print(f"  ✅ Read back: {status} ({readback})")
    except Exception as e:
        print(f"  ❌ Failed: {e}")


async def action_set_all_dampers(client, cfg, sw_client=None):
    """Set all dampers to the same value."""
    if not cfg.damper_rooms or not sw_client:
        print("  No damper rooms configured (or superwise not configured).")
        return

    value_label = pick("Set ALL dampers to:", ["OFF (0)", "ON (1)"])
    value = 1 if "ON" in value_label else 0

    print(f"\n  Setting all dampers → {'ON' if value else 'OFF'} ...")
    for room in cfg.damper_rooms:
        try:
            await sw_client.set_damper_value(room, value)
            print(f"  ✅ {room.name:<20} {room.label}")
        except Exception as e:
            print(f"  ❌ {room.name:<20} {e}")

    print("\n  Reading back...")
    for room in cfg.damper_rooms:
        try:
            val = await sw_client.get_damper_value(room)
            status = "ON" if val == 1 else "OFF"
            print(f"  {room.name:<20} {room.label:<40} {status} ({val})")
        except Exception as e:
            print(f"  {room.name:<20} {room.label:<40} ❌ {e}")


async def action_quick_check(client, cfg, sw_client=None):
    """Read one register of each type to verify connectivity."""
    print("\n🔌 Quick connectivity check...")
    ok = True

    if cfg.registers.status_reads:
        reg = cfg.registers.status_reads[0]
        try:
            val = await client.get_status(reg)
            print(f"  ✅ Status read OK  ({reg.label}: {val:.1f} {reg.unit})")
        except Exception as e:
            print(f"  ❌ Status read FAILED: {e}")
            ok = False

    if cfg.registers.temperature_setpoints:
        reg = cfg.registers.temperature_setpoints[0]
        try:
            val = await client.get_temperature(reg)
            print(f"  ✅ Temperature read OK  ({reg.label}: {val:.1f} {reg.unit})")
        except Exception as e:
            print(f"  ❌ Temperature read FAILED: {e}")
            ok = False

    if cfg.registers.air_boosts:
        reg = cfg.registers.air_boosts[0]
        try:
            async with client._connected() as modbus:
                if reg.type == "coil":
                    result = await modbus.read_coils(
                        address=reg.address, count=1, device_id=cfg.modbus.unit_id
                    )
                else:
                    result = await modbus.read_holding_registers(
                        address=reg.address, count=1, device_id=cfg.modbus.unit_id
                    )
            if result.isError():
                raise Exception(f"Modbus error at address {reg.address}")
            print(f"  ✅ Boost register readable  ({reg.label}, addr={reg.address})")
        except Exception as e:
            print(f"  ❌ Boost register FAILED: {e}")
            ok = False

    if cfg.damper_rooms and sw_client:
        room = cfg.damper_rooms[0]
        try:
            val = await sw_client.get_damper_value(room)
            status = "ON" if val == 1 else "OFF"
            print(f"  ✅ Damper read OK  ({room.label}: {status})")
        except Exception as e:
            print(f"  ❌ Damper read FAILED: {e}")
            ok = False

    if ok:
        print("\n  ✅ SuperWISE connection looks good!")
    else:
        print("\n  ⚠️  Some checks failed. Check config.yaml.")


async def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"

    print("=" * 50)
    print("  swegon-mcp — interactive connection test")
    print("=" * 50)

    try:
        cfg = load_config(config_path)
    except FileNotFoundError as e:
        print(f"\n❌ {e}")
        sys.exit(1)

    print(f"\nModbus:    {cfg.modbus.host}:{cfg.modbus.port}")
    print(f"Rooms:     {[r.name for r in cfg.registers.temperature_setpoints]}")
    print(f"Boosts:    {[b.name for b in cfg.registers.air_boosts]}")

    client = SwegonModbusClient(cfg)
    sw_client = None

    if cfg.superwise and cfg.damper_rooms:
        sw_client = SuperWiseClient(cfg)
        print(f"Damper:    {cfg.superwise.host} ({len(cfg.damper_rooms)} rooms)")
    else:
        print("Damper:    not configured")

    actions = {
        "Quick connectivity check": action_quick_check,
        "Read all status values": action_read_all_status,
        "Read all temperature setpoints": action_read_setpoints,
        "Set temperature for a room": action_set_temperature,
        "Trigger air boost for a unit": action_boost,
    }

    if sw_client:
        actions["Read damper status (all rooms)"] = action_damper_status
        actions["Set damper for a room"] = action_set_damper
        actions["Set ALL dampers on/off"] = action_set_all_dampers

    while True:
        choices = list(actions.keys()) + ["Exit"]
        chosen = pick("What do you want to do?", choices)

        if chosen == "Exit":
            print("\nBye!")
            break

        await actions[chosen](client, cfg, sw_client)

        again = input("\n  Do another? (y/n): ").strip().lower()
        if again != "y":
            print("\nBye!")
            break


if __name__ == "__main__":
    asyncio.run(main())
