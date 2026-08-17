"""M-Bus long frame: parsing, building and checksums.

    68 L L 68 C A CI <user data> CS 16

L counts C, A, CI and the user data, so it is 3 + len(payload). The checksum is
the arithmetic sum of those same L bytes with the carries thrown away.

`payload` here still contains the two transport service access point bytes
(STSAP, DTSAP) when the operator's profile says the meter sends them. Stripping
them belongs to the segmentation layer, which is the first place that knows
which profile is in use.
"""

from __future__ import annotations

from ..errors import FrameError
from ..models import MBusFrame

START = 0x68
STOP = 0x16

#: L is a single byte, and 3 of it is the C/A/CI header.
MAX_PAYLOAD = 0xFF - 3

#: Largest DLMS payload one frame may carry, from the technical description.
#: Longer messages are segmented. Enforced by the simulator, not by the parser,
#: because a real meter that exceeds it should still be read.
MAX_DLMS_PER_FRAME = 250


def checksum(data: bytes) -> int:
    """Arithmetic sum, carries discarded."""
    return sum(data) & 0xFF


def build_frame(c_field: int, a_field: int, ci_field: int, payload: bytes) -> bytes:
    """Assemble a complete frame including start and stop bytes."""
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload of {len(payload)} bytes exceeds the {MAX_PAYLOAD} byte maximum")
    body = bytes([c_field, a_field, ci_field]) + payload
    length = len(body)
    return bytes([START, length, length, START]) + body + bytes([checksum(body), STOP])


def parse_frame(raw: bytes) -> MBusFrame:
    """Parse one complete frame. Raises FrameError on anything malformed.

    Used by tests and by the capture replay path. The live byte stream goes
    through `FrameReader`, which has to resynchronise and cannot simply raise.
    """
    if len(raw) < 6:
        raise FrameError(f"frame of {len(raw)} bytes is too short to be an M-Bus long frame")
    if raw[0] != START or raw[3] != START:
        raise FrameError(f"missing start byte 0x68 (got 0x{raw[0]:02X} and 0x{raw[3]:02X})")
    if raw[1] != raw[2]:
        raise FrameError(f"L field bytes differ: 0x{raw[1]:02X} and 0x{raw[2]:02X}")

    length = raw[1]
    if length < 3:
        raise FrameError(f"L field of {length} is smaller than the C/A/CI header")
    expected = 6 + length
    if len(raw) != expected:
        raise FrameError(f"frame says {expected} bytes but {len(raw)} were given")
    if raw[-1] != STOP:
        raise FrameError(f"missing stop byte 0x16 (got 0x{raw[-1]:02X})")

    body = raw[4 : 4 + length]
    given = raw[4 + length]
    computed = checksum(body)
    if given != computed:
        raise FrameError(f"checksum 0x{given:02X} does not match computed 0x{computed:02X}")

    return MBusFrame(
        c_field=body[0],
        a_field=body[1],
        ci_field=body[2],
        payload=body[3:],
        raw=bytes(raw),
    )
