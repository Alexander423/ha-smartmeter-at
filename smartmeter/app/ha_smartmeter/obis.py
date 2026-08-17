"""OBIS code registry.

An OBIS code is six bytes, written A-B:C.D.E.F. The meter sends the six raw
bytes; everything above this module uses the string form and the short `key`,
which is what ends up in the MQTT payload and the entity object id.

Sensor metadata lives here rather than in the MQTT layer so that adding a value
means editing one table.

Reactive energy deliberately carries no device_class. Home Assistant only gained
a reactive energy device class recently and an unknown device_class breaks the
entity outright, whereas a missing one only means it does not appear in the
energy dashboard. Reactive energy does not belong there anyway.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ObisEntry:
    """One value the meter can report."""

    obis: str
    key: str
    name: str
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    icon: str | None = None
    #: Multiply the raw integer by this unless the telegram carries its own scaler.
    scale: float = 1.0
    #: Decimal places for display. None means let Home Assistant decide.
    precision: int | None = None
    #: Diagnostic entities are grouped separately in the device page.
    diagnostic: bool = False
    #: Entities created but switched off until the user asks for them.
    enabled_by_default: bool = True
    #: Three-phase meters only. Absence is normal, not an error.
    optional: bool = True
    #: Plausible range for the converted value. Used only to warn about a wrong
    #: scaler, never to reject a reading.
    sane_range: tuple[float, float] | None = None


REGISTRY: tuple[ObisEntry, ...] = (
    ObisEntry(
        obis="0-0:1.0.0.255",
        key="clock",
        name="Meter clock",
        device_class="timestamp",
        icon="mdi:clock-outline",
        diagnostic=True,
    ),
    ObisEntry(
        obis="0-0:96.1.0.255",
        key="meter_number",
        name="Meter number",
        icon="mdi:identifier",
        diagnostic=True,
    ),
    ObisEntry(
        obis="0-0:42.0.0.255",
        key="logical_device_name",
        name="Logical device name",
        icon="mdi:tag-outline",
        diagnostic=True,
        enabled_by_default=False,
    ),
    ObisEntry(
        obis="1-0:32.7.0.255",
        key="voltage_l1",
        name="Voltage L1",
        unit="V",
        device_class="voltage",
        state_class="measurement",
        precision=1,
        optional=False,
        sane_range=(150.0, 300.0),
    ),
    ObisEntry(
        obis="1-0:52.7.0.255",
        key="voltage_l2",
        name="Voltage L2",
        unit="V",
        device_class="voltage",
        state_class="measurement",
        precision=1,
        sane_range=(150.0, 300.0),
    ),
    ObisEntry(
        obis="1-0:72.7.0.255",
        key="voltage_l3",
        name="Voltage L3",
        unit="V",
        device_class="voltage",
        state_class="measurement",
        precision=1,
        sane_range=(150.0, 300.0),
    ),
    ObisEntry(
        obis="1-0:31.7.0.255",
        key="current_l1",
        name="Current L1",
        unit="A",
        device_class="current",
        state_class="measurement",
        precision=2,
        optional=False,
        sane_range=(0.0, 200.0),
    ),
    ObisEntry(
        obis="1-0:51.7.0.255",
        key="current_l2",
        name="Current L2",
        unit="A",
        device_class="current",
        state_class="measurement",
        precision=2,
        sane_range=(0.0, 200.0),
    ),
    ObisEntry(
        obis="1-0:71.7.0.255",
        key="current_l3",
        name="Current L3",
        unit="A",
        device_class="current",
        state_class="measurement",
        precision=2,
        sane_range=(0.0, 200.0),
    ),
    ObisEntry(
        obis="1-0:1.7.0.255",
        key="active_power_plus",
        name="Active power import",
        unit="W",
        device_class="power",
        state_class="measurement",
        precision=0,
        optional=False,
        sane_range=(0.0, 100_000.0),
    ),
    ObisEntry(
        obis="1-0:2.7.0.255",
        key="active_power_minus",
        name="Active power export",
        unit="W",
        device_class="power",
        state_class="measurement",
        precision=0,
        optional=False,
        sane_range=(0.0, 100_000.0),
    ),
    ObisEntry(
        obis="1-0:1.8.0.255",
        key="active_energy_plus",
        name="Active energy import",
        unit="Wh",
        device_class="energy",
        state_class="total_increasing",
        precision=0,
        optional=False,
    ),
    ObisEntry(
        obis="1-0:2.8.0.255",
        key="active_energy_minus",
        name="Active energy export",
        unit="Wh",
        device_class="energy",
        state_class="total_increasing",
        precision=0,
        optional=False,
    ),
    ObisEntry(
        obis="1-0:3.8.0.255",
        key="reactive_energy_plus",
        name="Reactive energy import",
        unit="varh",
        state_class="total_increasing",
        icon="mdi:flash-outline",
        precision=0,
        diagnostic=True,
    ),
    ObisEntry(
        obis="1-0:4.8.0.255",
        key="reactive_energy_minus",
        name="Reactive energy export",
        unit="varh",
        state_class="total_increasing",
        icon="mdi:flash-outline",
        precision=0,
        diagnostic=True,
    ),
)

BY_OBIS: dict[str, ObisEntry] = {e.obis: e for e in REGISTRY}
BY_KEY: dict[str, ObisEntry] = {e.key: e for e in REGISTRY}

#: Values every meter sends, single- or three-phase. Used to decide whether a
#: telegram was understood at all.
REQUIRED_KEYS: frozenset[str] = frozenset(e.key for e in REGISTRY if not e.optional)


def format_obis(raw: bytes) -> str:
    """Six raw bytes to the A-B:C.D.E.F string form."""
    if len(raw) != 6:
        raise ValueError(f"OBIS codes are 6 bytes, got {len(raw)}")
    a, b, c, d, e, f = raw
    return f"{a}-{b}:{c}.{d}.{e}.{f}"


def parse_obis(text: str) -> bytes:
    """The A-B:C.D.E.F string form back to six raw bytes."""
    try:
        group_a, rest = text.split("-", 1)
        group_b, rest = rest.split(":", 1)
        c, d, e, f = rest.split(".")
        values = [int(v) for v in (group_a, group_b, c, d, e, f)]
    except ValueError as exc:
        raise ValueError(f"not an OBIS code: {text!r}") from exc
    if any(v < 0 or v > 255 for v in values):
        raise ValueError(f"OBIS group out of range: {text!r}")
    return bytes(values)


def looks_like_obis(raw: bytes) -> bool:
    """Cheap test used when scanning a telegram for embedded OBIS codes.

    Group A is the medium (1 = electricity, 0 = abstract) and group F is almost
    always 255 in a push telegram. Requiring both keeps random six-byte strings,
    such as a meter serial, from being mistaken for a code.
    """
    return len(raw) == 6 and raw[0] in (0, 1) and raw[5] == 255
