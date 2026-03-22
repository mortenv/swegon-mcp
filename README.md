# swegon-mcp

MCP server for controlling Swegon WISE ventilation systems (GOLD series) through SuperWISE.

Exposes ventilation control as MCP tools so AI assistants (Claude, OpenClaw, etc.) can read status, set temperatures, and toggle damper functions on command.

## Features

- 🌡️ Read and set temperature setpoints per room (Modbus TCP)
- 📊 Read status values (supply/extract temperatures etc.)
- 🔧 Damper control — toggle constant airflow function per room (Socket.IO API)
- 🔒 Whitelist-based register access — only configured registers are accessible
- ✅ Value range validation per register

## Requirements

- Python 3.10+
- SuperWISE II (or GOLD RX with Modbus TCP enabled) on your local network
- Modbus TCP enabled on the unit (port 502)
- For damper control: a SuperWISE web UI user account

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
- Find register addresses in the Modbus export from your SuperWISE unit
- For damper control: add `superwise` (host, user, password) and `damper_rooms` sections — see `config.example.yaml` for details

### 3. Enable Modbus TCP on SuperWISE

On the SuperWISE web interface or control panel, enable Modbus TCP access.
Refer to your SuperWISE II documentation for the exact menu path.

> **Note:** While the GOLD RX AHU also supports Modbus TCP directly, commands
> sent to it may be overridden by SuperWISE. Always target the SuperWISE unit
> so it remains the authoritative controller.

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
| `get_damper_status` | Read current damper function status (on/off) for one or all rooms |
| `set_damper` | Set damper function value (0 = off, 1 = on) for a specific room |

## Damper Control

The damper tools control the constant airflow function on Wise Damper devices via the SuperWISE web UI's Socket.IO API. This is separate from the Modbus interface and requires a web UI login.

Credentials can be set in `config.yaml` or via environment variables:
- `SWEGON_SUPERWISE_USER` — web UI login email
- `SWEGON_SUPERWISE_PASSWORD` — web UI login password

Each room needs its location indices from the SuperWISE tree (ahu, grouping, node_container, node). See `config.example.yaml` for the format.

## Security

- Only registers listed in `config.yaml` are accessible — no raw Modbus access
- Temperature values are validated against per-register min/max limits
- Modbus TCP access can be restricted by IP on the GOLD RX unit itself
- SuperWISE web UI credentials support env var override for Docker/secrets management

## Finding Your Register Addresses

Export the Modbus register list from SuperWISE and map the relevant registers in your config. Key registers typically include:
- Temperature setpoints per room/zone
- AHU operating mode
- Fan speed level

## License

MIT
