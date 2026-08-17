"""Add-on options and MQTT credentials.

Supervisor writes the user's options to /data/options.json. The MQTT broker
credentials are not options at all: run.sh reads them from the Supervisor
services API with bashio and passes them in through the environment, so the user
never has to type a broker address.

Every value is validated here, before anything opens a serial port, so a
configuration mistake shows up as one clear line at the top of the add-on log
instead of a traceback fifteen seconds later.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .dlms.crypto import parse_key
from .errors import ConfigError
from .suppliers import SupplierProfile
from .suppliers import get as get_profile

_LOGGER = logging.getLogger(__name__)

OPTIONS_PATH = Path("/data/options.json")

#: Where captures and debug output go. `addon_config:rw` maps the add-on's own
#: configuration directory here.
CONFIG_DIR = Path("/config")
#: Used when /config is not mapped, which is the case when running outside
#: Supervisor.
FALLBACK_DIR = Path("/data")

LOG_LEVELS = ("trace", "debug", "info", "notice", "warning", "error", "fatal")


@dataclass(frozen=True, slots=True)
class MqttSettings:
    host: str
    port: int = 1883
    username: str = ""
    password: str = ""
    discovery_prefix: str = "homeassistant"
    #: Root of this add-on's own topics, below the discovery prefix.
    base_topic: str = "smartmeter"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> MqttSettings:
        env = env if env is not None else dict(os.environ)
        host = env.get("MQTT_HOST", "").strip()
        if not host:
            raise ConfigError(
                "no MQTT broker was provided by Supervisor",
                hint=(
                    "This add-on needs an MQTT broker. Install the Mosquitto broker add-on, "
                    "start it, then restart this add-on."
                ),
            )
        return cls(
            host=host,
            port=_int(env.get("MQTT_PORT"), 1883, "MQTT_PORT"),
            username=env.get("MQTT_USERNAME", ""),
            password=env.get("MQTT_PASSWORD", ""),
            discovery_prefix=env.get("MQTT_DISCOVERY_PREFIX", "homeassistant").strip("/")
            or "homeassistant",
            base_topic=env.get("MQTT_BASE_TOPIC", "smartmeter").strip("/") or "smartmeter",
        )


@dataclass(frozen=True, slots=True)
class Options:
    supplier: str = "tinetz"
    port: str = ""
    key: str = ""
    auth_key: str = ""
    #: 0 publishes every telegram, which is one state write every 5 s.
    min_publish_interval: int = 0
    capture_raw: bool = False
    log_level: str = "info"
    #: Development aid: read frames from a hex file instead of the serial port.
    replay_file: str = ""
    #: Entities go unavailable after this long without a telegram.
    stale_after: int = 30
    #: Overrides the device name in Home Assistant. Empty means derive it.
    device_name: str = ""

    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    # ---------------------------------------------------------------- loading

    @classmethod
    def load(cls, path: Path | None = None) -> Options:
        source = path or Path(os.environ.get("SMARTMETER_OPTIONS", OPTIONS_PATH))
        if not source.is_file():
            raise ConfigError(
                f"add-on options file {source} is missing",
                hint="Restart the add-on. If this persists, reinstall it.",
            )
        try:
            # utf-8-sig, because a hand-written options file for local testing
            # often picks up a byte order mark and the error it causes reads as
            # though the JSON itself were wrong.
            data = json.loads(source.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"{source} is not valid JSON: {exc}",
                hint="Reset the add-on configuration to its defaults and try again.",
            ) from exc
        return cls.from_mapping(data)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> Options:
        options = cls(
            supplier=str(data.get("supplier", "tinetz")).strip(),
            port=str(data.get("port", "")).strip(),
            key=str(data.get("key", "")).strip(),
            auth_key=str(data.get("auth_key", "")).strip(),
            min_publish_interval=_int(data.get("min_publish_interval"), 0, "min_publish_interval"),
            capture_raw=bool(data.get("capture_raw", False)),
            log_level=str(data.get("log_level", "info")).strip().lower(),
            replay_file=str(data.get("replay_file", "")).strip(),
            stale_after=_int(data.get("stale_after"), 30, "stale_after"),
            device_name=str(data.get("device_name", "")).strip(),
            raw=data,
        )
        options.validate()
        return options

    # -------------------------------------------------------------- validation

    def validate(self) -> None:
        # Reading these three is the validation. Each raises a ConfigError that
        # already says what the user should do about it.
        _profile = self.profile
        _key = self.encryption_key
        _auth_key = self.authentication_key

        if self.log_level not in LOG_LEVELS:
            raise ConfigError(
                f"unknown log level {self.log_level!r}",
                hint="Choose one of: " + ", ".join(LOG_LEVELS) + ".",
            )
        if self.min_publish_interval < 0:
            raise ConfigError(
                "min_publish_interval cannot be negative",
                hint="Use 0 to publish every telegram, or a number of seconds to publish less.",
            )
        if self.stale_after <= 0:
            raise ConfigError(
                "stale_after must be at least one second",
                hint="30 is a good value: it is six missed telegrams.",
            )
        if not self.replay_file and not self.port:
            raise ConfigError(
                "no serial device selected",
                hint=(
                    "Open the add-on configuration and pick your M-Bus adapter from the "
                    "Device list. If the list is empty, the adapter is not plugged in."
                ),
            )

    # ---------------------------------------------------------------- derived

    @property
    def profile(self) -> SupplierProfile:
        return get_profile(self.supplier)

    @property
    def encryption_key(self) -> bytes:
        return parse_key(self.key)

    @property
    def authentication_key(self) -> bytes | None:
        if not self.auth_key:
            return None
        return parse_key(self.auth_key, what="authentication key")

    @property
    def replay_mode(self) -> bool:
        return bool(self.replay_file)

    @property
    def secrets(self) -> tuple[str, ...]:
        """Strings that must never reach a log line or a capture file."""
        return tuple(value for value in (self.key, self.auth_key) if value)

    def output_dir(self) -> Path:
        """Where captures go. Falls back to /data when /config is not mapped."""
        for candidate in (CONFIG_DIR, FALLBACK_DIR):
            if candidate.is_dir() and os.access(candidate, os.W_OK):
                return candidate
        return Path.cwd()

    def describe(self) -> str:
        profile = self.profile
        source = f"replay {self.replay_file}" if self.replay_mode else self.port
        return f"{profile.label} on {source}, {profile.serial.describe()}"


def _int(value: Any, default: int, name: str) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"{name} must be a whole number, got {value!r}",
            hint=f"Set {name} to a number in the add-on configuration.",
        ) from exc
