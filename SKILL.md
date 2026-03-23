---
name: swegon
description: "Control Swegon SuperWISE ventilation system via swegon-mcp. Use when: user asks about room temperature, sets temperature, boosts ventilation, checks humidity, or reads air quality."
homepage: https://github.com/mortenv/swegon-mcp
metadata: { "openclaw": { "emoji": "🌬️", "requires": { "bins": ["mcporter"] } } }
---

# Swegon SuperWISE Skill

Control ventilation and read sensor data via [swegon-mcp](https://github.com/mortenv/swegon-mcp).

## Prerequisites

- swegon-mcp running (see repo README for setup)
- mcporter configured: `mcporter config add swegon --url http://<host>:<port>/sse`
- Bearer token or API key set in mcporter config

## When to Use

✅ **USE this skill when:**

- "What's the temperature in [room]?"
- "Set [room] to [N] degrees"
- "Boost [room]"
- "What's the humidity?"
- "What's the outdoor temperature?"
- "Show all room temperatures"
- "Turn off boost in [room]"

## Available Tools

List all tools and their parameters:

```bash
mcporter list swegon --schema
```

## Commands

### Status (temperatures + outdoor)

```bash
mcporter call swegon.get_status
```

### Temperature setpoints

```bash
mcporter call swegon.get_temperature_setpoints
```

### Set temperature

```bash
mcporter call swegon.set_temperature room=<room_name> temperature=<value>
```

### Boost fan (regular rooms)

```bash
mcporter call swegon.boost_fan unit=<unit_name>
mcporter call swegon.boost_fan unit=ahu   # whole house
```

### Boost bathroom/wet room (ZoneDamper)

```bash
mcporter call swegon.set_damper room=<room_name> value=1   # ON
mcporter call swegon.set_damper room=<room_name> value=0   # OFF
```

### Damper status

```bash
mcporter call swegon.get_damper_status
```

### Fan mode

```bash
mcporter call swegon.set_fan_mode unit=<unit_name> mode=<mode>
```

## Finding Room Names

Room names are defined in `config.yaml` on the server. List available names:

```bash
mcporter list swegon --schema | grep -A5 "set_temperature"
```

## Important Notes

- **Bathrooms** → always use `set_damper`, NOT `boost_fan` (coil not exposed for wet rooms)
- **Regular rooms** → use `boost_fan`
- Temperature changes take a few minutes to take effect
- `get_damper_status` to check if anything is currently boosted

## Token Refresh

mcporter Bearer tokens are in-memory on the server (cleared on restart). If you get 401:

```bash
# Re-fetch token and update mcporter config
curl -X POST http://<host>:<port>/oauth/token \
  -d "grant_type=client_credentials&client_secret=<api_key>"
```

Then update `~/.mcporter/mcporter.json` with the new Bearer token.
