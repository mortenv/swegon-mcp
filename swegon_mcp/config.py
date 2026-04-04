"""Configuration loader for swegon-mcp."""

from __future__ import annotations

import os
import yaml
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field, model_validator


class ModbusConfig(BaseModel):
    host: str
    port: int = 502
    unit_id: int = 1
    timeout: int = 5


class TemperatureRegister(BaseModel):
    name: str
    label: str
    address: int
    min: float = 16.0
    max: float = 28.0
    scale: float = 1.0
    unit: str = "°C"


class FanModeRegister(BaseModel):
    name: str
    label: str
    address: int
    type: Literal["coil", "holding"] = "holding"
    values: dict[str, int] = {"normal": 0, "high": 2, "away": 1}


class AirBoostRegister(BaseModel):
    """SuperWISE 'Air boost' / Manuell forsering register.
    Writing 1 triggers a timed boost; SuperWISE manages duration and auto-revert."""

    name: str
    label: str
    address: int
    type: Literal["coil", "holding"] = "coil"


class StatusRegister(BaseModel):
    name: str
    label: str
    address: int
    type: Literal["input", "holding"] = "input"
    scale: float = 1.0
    unit: str = ""


class RegistersConfig(BaseModel):
    temperature_setpoints: list[TemperatureRegister] = Field(default_factory=list)
    fan_modes: list[FanModeRegister] = Field(default_factory=list)
    air_boosts: list[AirBoostRegister] = Field(default_factory=list)
    status_reads: list[StatusRegister] = Field(default_factory=list)


class BoostConfig(BaseModel):
    default_duration_minutes: int = 30
    max_duration_minutes: int = 120


class SuperWiseConfig(BaseModel):
    host: str
    user: str = ""
    password: str = ""
    timeout: int = 15

    @model_validator(mode="after")
    def _env_overrides(self):
        if not self.user:
            self.user = os.environ.get("SWEGON_SUPERWISE_USER", "")
        if not self.password:
            self.password = os.environ.get("SWEGON_SUPERWISE_PASSWORD", "")
        return self


class DamperLocation(BaseModel):
    system: int = 0
    superwise: int = 0
    director: int = 0
    ahu: int = 1
    grouping: int
    function_group: int = 0
    node_container: int
    node: int


class DamperRoom(BaseModel):
    name: str
    label: str
    location: DamperLocation
    type_id: int = 1011
    io_name: str = "Feil Funksjon konstant luftmengde"


class AppConfig(BaseModel):
    modbus: ModbusConfig
    registers: RegistersConfig = Field(default_factory=RegistersConfig)
    boost: BoostConfig = Field(default_factory=BoostConfig)
    superwise: SuperWiseConfig | None = None
    damper_rooms: list[DamperRoom] = Field(default_factory=list)


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}\n"
            "Copy config.example.yaml to config.yaml and fill in your settings."
        )
    with open(config_path) as f:
        data = yaml.safe_load(f)
    return AppConfig(**data)
