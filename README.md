# swegon-mcp

MCP server for controlling Swegon WISE ventilation systems (GOLD series) via Modbus TCP through SuperWISE.

Exposes ventilation control as MCP tools so AI assistants (Claude, OpenClaw, etc.) can set temperatures and control fan modes on command.

## Features

- 🌡️ Read and set temperature setpoints per room
- 💨 Set fan mode (normal / high / away)
- ⏱️ Temporary fan boost with auto-revert
- 📊 Read status values (supply/extract temperatures etc.)
- 🔒 Whitelist-based register access — only configured registers are accessible
- ✅ Value range validation per register

## Requirements

- Python 3.10+
- SuperWISE II (or GOLD RX with Modbus TCP enabled) on your local network
- Modbus TCP enabled on the unit (port 502)

## Setup

### 1. Install

```bash
pip install swegon-mcp
```

Or from source:
```bash
git clone https://github.com/yourname/swegon-mcp
cd swegon-mcp
pip install -e .
```

### 2. Configure

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml`:
- Set `modbus.host` to your SuperWISE IP address
- Add your rooms under `registers.temperature_setpoints`
- Add fan units under `registers.fan_modes`
- Find register addresses in the Modbus export from your SuperWISE unit

### 3. Enable Modbus TCP on GOLD RX

On the unit control panel:
```
SETTINGS → INSTALLATION (code: 1111) → COMMUNICATION → ETHERNET → MODBUS TCP
```

### 4. Run

```bash
swegon-mcp
# or with custom config path:
swegon-mcp /path/to/config.yaml
```

### 5. Connect to OpenClaw / Claude Desktop

Add to your MCP config:
```json
{
  "mcpServers": {
    "swegon": {
      "command": "swegon-mcp",
      "args": ["/path/to/config.yaml"]
    }
  }
}
```

## Available Tools

| Tool | Description |
|------|-------------|
| `get_status` | Read current system status (temperatures etc.) |
| `get_temperature_setpoints` | Read current setpoints for all rooms |
| `set_temperature` | Set temperature setpoint for a specific room |
| `set_fan_mode` | Set fan mode (normal/high/away) |
| `boost_fan` | Temporarily boost fan to high, auto-reverts |

## Security

- Only registers listed in `config.yaml` are accessible — no raw Modbus access
- Temperature values are validated against per-register min/max limits
- Fan modes are validated against the configured allowed values
- Modbus TCP access can be restricted by IP on the GOLD RX unit itself

## Finding Your Register Addresses

Export the Modbus register list from SuperWISE and map the relevant registers in your config. Key registers typically include:
- Temperature setpoints per room/zone
- AHU operating mode
- Fan speed level

## License

MIT
