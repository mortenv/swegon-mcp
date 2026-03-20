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


async def action_read_all_status(client, cfg):
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


async def action_read_setpoints(client, cfg):
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


async def action_set_temperature(client, cfg):
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


async def action_boost(client, cfg):
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


async def action_quick_check(client, cfg):
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

    if ok:
        print("\n  ✅ SuperWISE connection looks good!")
    else:
        print("\n  ⚠️  Some registers failed. Check addresses in config.yaml.")


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

    print(f"\nSuperWISE: {cfg.modbus.host}:{cfg.modbus.port}")
    print(f"Rooms:     {[r.name for r in cfg.registers.temperature_setpoints]}")
    print(f"Boosts:    {[b.name for b in cfg.registers.air_boosts]}")

    client = SwegonModbusClient(cfg)

    actions = {
        "Quick connectivity check": action_quick_check,
        "Read all status values": action_read_all_status,
        "Read all temperature setpoints": action_read_setpoints,
        "Set temperature for a room": action_set_temperature,
        "Trigger air boost for a unit": action_boost,
    }

    while True:
        choices = list(actions.keys()) + ["Exit"]
        chosen = pick("What do you want to do?", choices)

        if chosen == "Exit":
            print("\nBye!")
            break

        await actions[chosen](client, cfg)

        again = input("\n  Do another? (y/n): ").strip().lower()
        if again != "y":
            print("\nBye!")
            break


if __name__ == "__main__":
    asyncio.run(main())
