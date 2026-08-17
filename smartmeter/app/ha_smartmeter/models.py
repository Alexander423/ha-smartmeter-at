"""Objects passed between layers.

The layers are: transport (bytes) -> M-Bus framing (MBusFrame) -> reassembly
(bytes) -> DLMS ciphering (CipheredApdu) -> decryption (bytes) -> parsing
(Telegram). Nothing here imports a transport or a protocol module, so these
types can be constructed freely in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class MBusFrame:
    """One M-Bus long frame, already checksum-verified."""

    c_field: int
    a_field: int
    ci_field: int
    payload: bytes
    #: The complete frame including start and stop bytes, kept for capture mode.
    raw: bytes = b""

    @property
    def segment_number(self) -> int:
        return self.ci_field & 0x0F

    @property
    def is_final(self) -> bool:
        """FIN bit. Set on the last segment and on single-segment messages."""
        return bool(self.ci_field & 0x10)

    @property
    def has_mbus_data_header(self) -> bool:
        """Bits 7-6-5. This spec never sets them; anything else is a foreign frame."""
        return bool(self.ci_field & 0xE0)


@dataclass(frozen=True, slots=True)
class CipheredApdu:
    """A general-glo-ciphering APDU, split into its parts but not yet decrypted."""

    system_title: bytes
    security_control: int
    frame_counter: int
    ciphertext: bytes
    #: Last 12 bytes of the GCM output when the security control byte says the
    #: message is authenticated. None when it is encryption-only.
    tag: bytes | None

    @property
    def iv(self) -> bytes:
        """96-bit GCM nonce: system title followed by the frame counter."""
        return self.system_title + self.frame_counter.to_bytes(4, "big")

    @property
    def is_authenticated(self) -> bool:
        return bool(self.security_control & 0x10)

    @property
    def is_encrypted(self) -> bool:
        return bool(self.security_control & 0x20)


@dataclass(frozen=True, slots=True)
class Reading:
    """One decoded value."""

    key: str
    obis: str
    value: float | int | str | datetime
    unit: str | None = None


@dataclass(slots=True)
class Telegram:
    """A complete decoded push message."""

    readings: dict[str, Reading] = field(default_factory=dict)
    system_title: bytes = b""
    frame_counter: int = 0
    timestamp: datetime | None = None
    #: True when the GCM tag was checked and matched. False means the payload
    #: was accepted on structure alone because no authentication key is known.
    authenticated: bool = False

    @property
    def meter_number(self) -> str | None:
        reading = self.readings.get("meter_number")
        return str(reading.value) if reading is not None else None

    def values(self) -> dict[str, float | int | str]:
        """Flat mapping for the MQTT state payload. Datetimes become ISO strings."""
        out: dict[str, float | int | str] = {}
        for key, reading in self.readings.items():
            value = reading.value
            out[key] = value.isoformat() if isinstance(value, datetime) else value
        return out
