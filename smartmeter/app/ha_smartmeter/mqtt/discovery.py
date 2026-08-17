"""MQTT Discovery payloads.

Discovery is published after the first telegram has been decoded, not at
start-up. Two things depend on that: the device carries the real meter serial as
its identifier, and only the values the meter actually sends get entities. A
single-phase meter therefore never grows three voltage sensors, two of which
would sit at "unknown" for ever.

All entities share one state topic carrying a JSON object, so a telegram is one
publish rather than fifteen, and every value in Home Assistant comes from the
same instant.
"""

from __future__ import annotations

import re
from typing import Any

from .. import __version__
from ..obis import BY_KEY, ObisEntry
from ..suppliers import SupplierProfile

PROJECT_URL = "https://github.com/Alexander423/ha-smartmeter-at"

_UNSAFE = re.compile(r"[^a-zA-Z0-9_-]+")


def node_id(meter_number: str | None, system_title: bytes) -> str:
    """A stable identifier for this meter, safe to put in a topic.

    The meter serial is the natural choice. When a meter does not report one,
    the system title does the job: it is unique per meter and never changes.
    """
    if meter_number:
        cleaned = _UNSAFE.sub("_", meter_number).strip("_")
        if cleaned:
            return cleaned
    return "meter_" + system_title.hex()


def state_topic(base_topic: str, node: str) -> str:
    return f"{base_topic}/{node}/state"


def availability_topic(base_topic: str) -> str:
    """One topic for the whole add-on, not one per meter.

    It has to be decided before the MQTT connection is made, because it is the
    last will, and at that point no telegram has been read and the meter serial
    is not known yet.
    """
    return f"{base_topic}/status"


def discovery_topic(prefix: str, node: str, key: str) -> str:
    return f"{prefix}/sensor/{node}/{key}/config"


def device_payload(
    profile: SupplierProfile,
    node: str,
    meter_number: str | None,
    name_override: str = "",
) -> dict[str, Any]:
    return {
        "identifiers": [node],
        "name": name_override or _default_device_name(meter_number),
        "manufacturer": profile.manufacturer,
        "model": profile.model,
        "serial_number": meter_number or None,
        "sw_version": __version__,
    }


def origin_payload() -> dict[str, Any]:
    return {"name": "ha-smartmeter-at", "sw_version": __version__, "support_url": PROJECT_URL}


def sensor_payload(
    entry: ObisEntry,
    node: str,
    base_topic: str,
    device: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": entry.name,
        "unique_id": f"{node}_{entry.key}",
        "object_id": f"{node}_{entry.key}",
        "state_topic": state_topic(base_topic, node),
        "value_template": "{{ value_json." + entry.key + " | default(None) }}",
        "availability_topic": availability_topic(base_topic),
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": device,
        "origin": origin_payload(),
    }
    if entry.unit:
        payload["unit_of_measurement"] = entry.unit
    if entry.device_class:
        payload["device_class"] = entry.device_class
    if entry.state_class:
        payload["state_class"] = entry.state_class
    if entry.icon:
        payload["icon"] = entry.icon
    if entry.precision is not None:
        payload["suggested_display_precision"] = entry.precision
    if entry.diagnostic:
        payload["entity_category"] = "diagnostic"
    if not entry.enabled_by_default:
        payload["enabled_by_default"] = False
    return payload


def payloads_for(
    keys: list[str],
    node: str,
    base_topic: str,
    device: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """(topic suffix key, payload) for every value the meter sent."""
    out = []
    for key in keys:
        entry = BY_KEY.get(key)
        if entry is None:
            continue
        out.append((key, sensor_payload(entry, node, base_topic, device)))
    return out


def _default_device_name(meter_number: str | None) -> str:
    return f"Smart meter {meter_number}" if meter_number else "Smart meter"
