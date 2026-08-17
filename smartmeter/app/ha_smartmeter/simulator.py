"""Generates telegrams that are byte-for-byte valid, so the rest of the add-on
can be developed and tested without an M-Bus adapter.

It builds the real thing: a COSEM data-notification, encrypted with AES-128-GCM
under a known key, wrapped in a general-glo-ciphering APDU and split across as
many M-Bus frames as it needs. Feeding its output into `FrameReader` exercises
every layer except the serial port itself.

Values are emitted in two shapes on purpose, because both occur in the wild:
voltage and current as a register structure with its own scaler and unit,
power and energy as plain integers.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .dlms import axdr
from .dlms.apdu import build_ciphered_apdu
from .dlms.axdr import Node, Tag
from .dlms.crypto import encrypt
from .framing.hdlc_framer import FLAG, FORMAT_TYPE_3, LLC_FROM_SERVER, SEGMENTATION_BIT, crc16_x25
from .mbus.frame import MAX_DLMS_PER_FRAME, build_frame
from .obis import BY_KEY, parse_obis

C_FIELD_SND_UD = 0x53
A_FIELD_BROADCAST = 0xFF

#: HDLC addresses as a meter pushing to a client uses them: client 16 and
#: server 1, each shifted left with bit 0 marking the last byte.
HDLC_DEST = 0x21
HDLC_SRC = 0x03
#: Unnumbered information frame with the poll/final bit set.
HDLC_CONTROL = 0x13

#: DLMS unit enum values for the register form.
_UNIT_ENUM = {"V": 35, "A": 33, "W": 27, "Wh": 30, "varh": 32}

DEFAULT_VALUES: dict[str, float] = {
    "voltage_l1": 231.4,
    "voltage_l2": 229.8,
    "voltage_l3": 230.6,
    "current_l1": 1.23,
    "current_l2": 0.87,
    "current_l3": 2.05,
    "active_power_plus": 412,
    "active_power_minus": 0,
    "active_energy_plus": 1234567,
    "active_energy_minus": 89012,
    "reactive_energy_plus": 3456,
    "reactive_energy_minus": 78,
}

#: Values a single-phase meter leaves out.
THREE_PHASE_ONLY = ("voltage_l2", "voltage_l3", "current_l2", "current_l3")


@dataclass(slots=True)
class MeterSimulator:
    key: bytes
    system_title: bytes = b"\x53\x41\x47\x01\x02\x03\x04\x05"  # "SAG" plus a serial
    meter_number: str = "1SAG1234567890"
    logical_device_name: str = "SAG0000000000"
    three_phase: bool = True
    #: "mbus", "p1" or "hdlc". Decides the link layer wrapped around the APDU.
    interface: str = "mbus"
    security_control: int = 0x21
    auth_key: bytes | None = None
    tsap: tuple[int, int] | None = (0x01, 0x67)
    frame_counter: int = 1
    #: Lowered in tests to force segmentation with a short telegram.
    max_dlms_per_frame: int = MAX_DLMS_PER_FRAME
    #: Information field size for the HDLC interface. Real meters negotiate
    #: something around this, so telegrams segment there too.
    max_hdlc_info: int = 128
    values: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_VALUES))

    # ------------------------------------------------------------------ layers

    def build_plaintext(
        self,
        timestamp: datetime | None = None,
        overrides: Mapping[str, float] | None = None,
    ) -> bytes:
        """A COSEM data-notification carrying the current values."""
        moment = timestamp or datetime.now(UTC)
        values = dict(self.values)
        if overrides:
            values.update(overrides)
        if not self.three_phase:
            for key in THREE_PHASE_ONLY:
                values.pop(key, None)

        items: list[Node] = []
        items += self._tagged("clock", Node(Tag.OCTET_STRING, axdr.encode_datetime(moment)))
        items += self._tagged("meter_number", _ascii(self.meter_number))
        items += self._tagged("logical_device_name", _ascii(self.logical_device_name))

        for key, value in values.items():
            entry = BY_KEY[key]
            if entry.unit in ("V", "A"):
                items += self._tagged(key, _register(value, entry.unit))
            else:
                items += self._tagged(key, Node(Tag.UINT32, round(value)))

        body = axdr.encode(Node(Tag.STRUCTURE, items))
        clock = axdr.encode_datetime(moment)
        return (
            bytes([0x0F])
            + b"\x00\x00\x00\x01"  # long-invoke-id-and-priority
            + bytes([len(clock)])
            + clock
            + body
        )

    def build_apdu(self, plaintext: bytes | None = None) -> bytes:
        """Encrypt a plaintext and wrap it in a general-glo-ciphering APDU."""
        data = plaintext if plaintext is not None else self.build_plaintext()
        ciphertext, tag = encrypt(
            self.system_title,
            self.frame_counter,
            self.security_control,
            data,
            self.key,
            self.auth_key,
        )
        return build_ciphered_apdu(
            self.system_title, self.security_control, self.frame_counter, ciphertext, tag
        )

    def build_frames(self, plaintext: bytes | None = None) -> list[bytes]:
        """Complete link-layer frames, ready to be written to a serial line.

        Which wrapper goes around the APDU depends on `interface`, exactly as it
        does for a real meter: M-Bus long frames for the western operators, no
        wrapper at all for P1, HDLC for the optical interface.
        """
        apdu = self.build_apdu(plaintext)
        if self.interface == "p1":
            # P1 has no link layer. The APDU delimits itself and that is all.
            return [apdu]
        if self.interface == "hdlc":
            return self._build_hdlc_frames(apdu)
        return self._build_mbus_frames(apdu)

    def _build_mbus_frames(self, apdu: bytes) -> list[bytes]:
        chunks = _chunk(apdu, self.max_dlms_per_frame)
        prefix = bytes(self.tsap) if self.tsap else b""
        frames = []
        for index, chunk in enumerate(chunks):
            final = index == len(chunks) - 1
            ci = (0x10 if final else 0x00) | (index & 0x0F)
            frames.append(build_frame(C_FIELD_SND_UD, A_FIELD_BROADCAST, ci, prefix + chunk))
        return frames

    def _build_hdlc_frames(self, apdu: bytes) -> list[bytes]:
        """The LLC header goes on the first segment only, as on the wire."""
        chunks = _chunk(LLC_FROM_SERVER + apdu, self.max_hdlc_info)
        return [
            build_hdlc_frame(chunk, segmented=index < len(chunks) - 1)
            for index, chunk in enumerate(chunks)
        ]

    def next_telegram(self, plaintext: bytes | None = None) -> bytes:
        """One push interval worth of bytes, then advance the frame counter."""
        data = b"".join(self.build_frames(plaintext))
        self.frame_counter += 1
        return data

    # ----------------------------------------------------------------- helpers

    def _tagged(self, key: str, value: Node) -> list[Node]:
        return [Node(Tag.OCTET_STRING, parse_obis(BY_KEY[key].obis)), value]


def _ascii(text: str) -> Node:
    return Node(Tag.OCTET_STRING, text.encode("ascii"))


def _register(value: float, unit: str) -> Node:
    """A value with its own scaler and unit, as a real register object sends it."""
    scaler = -2 if unit == "A" else -1
    raw = round(value / (10**scaler))
    return Node(
        Tag.STRUCTURE,
        [
            Node(Tag.UINT32, raw),
            Node(Tag.STRUCTURE, [Node(Tag.INT8, scaler), Node(Tag.ENUM, _UNIT_ENUM[unit])]),
        ],
    )


def build_hdlc_frame(info: bytes, segmented: bool = False) -> bytes:
    """One HDLC type 3 frame around an information field.

    The length field counts the format field through the FCS, so it is nine
    bytes of header and check sequences plus the information itself.
    """
    length = 9 + len(info)
    if length > 0x7FF:
        raise ValueError(f"HDLC frame of {length} bytes exceeds the 11 bit length field")
    header = bytes(
        [
            FORMAT_TYPE_3 | (SEGMENTATION_BIT if segmented else 0) | (length >> 8),
            length & 0xFF,
            HDLC_DEST,
            HDLC_SRC,
            HDLC_CONTROL,
        ]
    )
    header += crc16_x25(header).to_bytes(2, "little")
    body = header + info
    return bytes([FLAG]) + body + crc16_x25(body).to_bytes(2, "little") + bytes([FLAG])


def _chunk(data: bytes, size: int) -> list[bytes]:
    if size < 1:
        raise ValueError("chunk size must be at least 1")
    return [data[i : i + size] for i in range(0, len(data), size)] or [b""]


def hex_lines(frames: Iterable[bytes]) -> str:
    """Frames as the hex-per-line format the replay source and captures use."""
    return "\n".join(frame.hex() for frame in frames) + "\n"
