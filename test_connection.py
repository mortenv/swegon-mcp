#!/usr/bin/env python3
"""
Connection test for swegon-mcp.

Reads one value from each configured register type to verify:
- Network connectivity to SuperWISE
- Correct Modbus unit ID
- Register addresses are readable

Usage:
    uv run python test_connection.py [config.yaml]
    python test_connection.py [config.yaml]
"""

import asyncio
import sys
from pathlib import Path

# Allow running from repo root without install
sys.path.insert(0, str(Path(__file__).parent))

from swegon_mcp.config import load_config
from swegon_mcp.modbus_client import SwegonModbusClient


async def test_connection(config_path: str = "config.yaml") -> bool:
    print(f"Loading config: {config_path}")
    try:
        cfg = load_config(config_path)
    except FileNotFoundError as e:
        print(f"❌ {e}")
        return False

    print(f"\nConnecting to SuperWISE at {cfg.modbus.host}:{cfg.modbus.port} (unit {cfg.modbus.unit_id})")
    client = SwegonModbusClient(cfg)
    all_ok = True

    # --- Status reads ---
    if cfg.registers.status_reads:
        print("\n📊 Status reads:")
        for reg in cfg.registers.status_reads:
            try:
                value = await client.get_status(reg)
                print(f"  ✅ {reg.label}: {value:.2f} {reg.unit}  (addr={reg.address})")
            except Exception as e:
                print(f"  ❌ {reg.label}: {e}  (addr={reg.address})")
                all_ok = False
    else:
        print("\n⚠️  No status_reads configured — skipping.")

    # --- Temperature setpoints ---
    if cfg.registers.temperature_setpoints:
        print("\n🌡️  Temperature setpoints:")
        for reg in cfg.registers.temperature_setpoints:
            try:
                value = await client.get_temperature(reg)
                print(f"  ✅ {reg.label}: {value:.2f} {reg.unit}  (addr={reg.address})")
            except Exception as e:
                print(f"  ❌ {reg.label}: {e}  (addr={reg.address})")
                all_ok = False
    else:
        print("\n⚠️  No temperature_setpoints configured — skipping.")

    # --- Air boost registers (read-only check, no write) ---
    if cfg.registers.air_boosts:
        print("\n💨 Air boost registers (read-only check):")
        for reg in cfg.registers.air_boosts:
            try:
                async with client._connected() as modbus:
                    if reg.type == "coil":
                        result = await modbus.read_coils(
                            address=reg.address, count=1, slave=cfg.modbus.unit_id
                        )
                    else:
                        result = await modbus.read_holding_registers(
                            address=reg.address, count=1, slave=cfg.modbus.unit_id
                        )
                    if result.isError():
                        raise Exception(f"Modbus error reading register {reg.address}")
                print(f"  ✅ {reg.label}: readable  (addr={reg.address}, type={reg.type})")
            except Exception as e:
                print(f"  ❌ {reg.label}: {e}  (addr={reg.address})")
                all_ok = False
    else:
        print("\n⚠️  No air_boosts configured — skipping.")

    print()
    if all_ok:
        print("✅ All registers OK — ready to use!")
    else:
        print("⚠️  Some registers failed. Check addresses in config.yaml against your Modbus export.")
        print("   Tip: Addresses are 0-based (pymodbus). SuperWISE '4x14954' → address 14953.")

    return all_ok


if __name__ == "__main__":
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    ok = asyncio.run(test_connection(config_path))
    sys.exit(0 if ok else 1)
