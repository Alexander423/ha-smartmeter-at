"""A-XDR encoding of DLMS/COSEM data, as used inside a push telegram.

The decoder produces a tree of `Node` objects and keeps the type tag, because
the OBIS extraction above it needs to tell an octet string apart from a number.

Only the types that appear in a customer-interface push telegram are supported.
Unknown tags raise rather than being skipped: silently dropping a value would
shift every following field in a positional profile.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from ..errors import ParseError


class Tag:
    NULL = 0x00
    ARRAY = 0x01
    STRUCTURE = 0x02
    BOOLEAN = 0x03
    BIT_STRING = 0x04
    INT32 = 0x05
    UINT32 = 0x06
    OCTET_STRING = 0x09
    VISIBLE_STRING = 0x0A
    UTF8_STRING = 0x0C
    BCD = 0x0D
    INT8 = 0x0F
    INT16 = 0x10
    UINT8 = 0x11
    UINT16 = 0x12
    INT64 = 0x14
    UINT64 = 0x15
    ENUM = 0x16
    FLOAT32 = 0x17
    FLOAT64 = 0x18
    DATE_TIME = 0x19
    DATE = 0x1A
    TIME = 0x1B


_INTEGER_TAGS: dict[int, tuple[int, bool]] = {
    # tag: (byte width, signed)
    Tag.INT8: (1, True),
    Tag.UINT8: (1, False),
    Tag.ENUM: (1, False),
    Tag.INT16: (2, True),
    Tag.UINT16: (2, False),
    Tag.INT32: (4, True),
    Tag.UINT32: (4, False),
    Tag.INT64: (8, True),
    Tag.UINT64: (8, False),
}

_STRING_TAGS = frozenset({Tag.OCTET_STRING, Tag.VISIBLE_STRING, Tag.UTF8_STRING, Tag.BCD})

#: Marker the meter uses for "no value available" in a deviation field.
_DEVIATION_UNSPECIFIED = 0x8000


@dataclass(frozen=True, slots=True)
class Node:
    """One A-XDR item. `value` is a list of Nodes for arrays and structures."""

    tag: int
    value: Any

    @property
    def is_container(self) -> bool:
        return self.tag in (Tag.ARRAY, Tag.STRUCTURE)

    @property
    def is_number(self) -> bool:
        return self.tag in _INTEGER_TAGS or self.tag in (Tag.FLOAT32, Tag.FLOAT64)

    @property
    def children(self) -> list[Node]:
        return self.value if self.is_container else []


# ---------------------------------------------------------------- length codes


def read_length(data: bytes, offset: int) -> tuple[int, int]:
    """A-XDR variable length. Returns (length, new offset).

    A first byte below 0x80 is the length itself. 0x81..0x84 means the length is
    in the next 1..4 bytes.
    """
    if offset >= len(data):
        raise ParseError(f"truncated length field at offset {offset}")
    first = data[offset]
    offset += 1
    if first < 0x80:
        return first, offset
    count = first & 0x7F
    if count == 0 or count > 4:
        raise ParseError(f"unsupported length encoding 0x{first:02X} at offset {offset - 1}")
    if offset + count > len(data):
        raise ParseError(f"truncated {count}-byte length field at offset {offset}")
    return int.from_bytes(data[offset : offset + count], "big"), offset + count


def encode_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    for count in (1, 2, 3, 4):
        if length < 1 << (8 * count):
            return bytes([0x80 | count]) + length.to_bytes(count, "big")
    raise ValueError(f"length {length} does not fit in an A-XDR length field")


# -------------------------------------------------------------------- decoding


def decode(data: bytes, offset: int = 0, _depth: int = 0) -> tuple[Node, int]:
    """Decode one item. Returns (node, offset after the item)."""
    if _depth > 32:
        raise ParseError("A-XDR nesting deeper than 32 levels, refusing to recurse further")
    if offset >= len(data):
        raise ParseError(f"truncated item at offset {offset}")
    tag = data[offset]
    offset += 1

    if tag == Tag.NULL:
        return Node(tag, None), offset

    if tag in (Tag.ARRAY, Tag.STRUCTURE):
        count, offset = read_length(data, offset)
        # A container cannot have more elements than there are bytes left.
        if count > len(data) - offset:
            raise ParseError(
                f"container at offset {offset} claims {count} elements "
                f"but only {len(data) - offset} bytes remain"
            )
        items: list[Node] = []
        for _ in range(count):
            item, offset = decode(data, offset, _depth + 1)
            items.append(item)
        return Node(tag, items), offset

    if tag in _INTEGER_TAGS:
        width, signed = _INTEGER_TAGS[tag]
        if offset + width > len(data):
            raise ParseError(f"truncated integer at offset {offset}")
        value = int.from_bytes(data[offset : offset + width], "big", signed=signed)
        return Node(tag, value), offset + width

    if tag == Tag.BOOLEAN:
        if offset >= len(data):
            raise ParseError(f"truncated boolean at offset {offset}")
        return Node(tag, data[offset] != 0), offset + 1

    if tag in (Tag.FLOAT32, Tag.FLOAT64):
        width = 4 if tag == Tag.FLOAT32 else 8
        if offset + width > len(data):
            raise ParseError(f"truncated float at offset {offset}")
        fmt = ">f" if tag == Tag.FLOAT32 else ">d"
        return Node(tag, struct.unpack(fmt, data[offset : offset + width])[0]), offset + width

    if tag in _STRING_TAGS:
        length, offset = read_length(data, offset)
        if offset + length > len(data):
            raise ParseError(f"truncated string of {length} bytes at offset {offset}")
        return Node(tag, data[offset : offset + length]), offset + length

    if tag == Tag.BIT_STRING:
        bits, offset = read_length(data, offset)
        width = (bits + 7) // 8
        if offset + width > len(data):
            raise ParseError(f"truncated bit-string at offset {offset}")
        return Node(tag, data[offset : offset + width]), offset + width

    if tag in (Tag.DATE_TIME, Tag.DATE, Tag.TIME):
        width = {Tag.DATE_TIME: 12, Tag.DATE: 5, Tag.TIME: 4}[tag]
        if offset + width > len(data):
            raise ParseError(f"truncated date/time at offset {offset}")
        return Node(tag, data[offset : offset + width]), offset + width

    raise ParseError(f"unknown A-XDR tag 0x{tag:02X} at offset {offset - 1}")


def decode_one(data: bytes) -> Node:
    """Decode a single item and require that nothing follows it."""
    node, offset = decode(data)
    if offset != len(data):
        raise ParseError(f"{len(data) - offset} trailing bytes after A-XDR item")
    return node


# -------------------------------------------------------------------- encoding


def encode(node: Node) -> bytes:
    """Inverse of `decode`, used by the frame simulator and by round-trip tests."""
    tag = node.tag
    if tag == Tag.NULL:
        return bytes([tag])
    if tag in (Tag.ARRAY, Tag.STRUCTURE):
        body = b"".join(encode(child) for child in node.value)
        return bytes([tag]) + encode_length(len(node.value)) + body
    if tag in _INTEGER_TAGS:
        width, signed = _INTEGER_TAGS[tag]
        return bytes([tag]) + int(node.value).to_bytes(width, "big", signed=signed)
    if tag == Tag.BOOLEAN:
        return bytes([tag, 0xFF if node.value else 0x00])
    if tag in (Tag.FLOAT32, Tag.FLOAT64):
        fmt = ">f" if tag == Tag.FLOAT32 else ">d"
        return bytes([tag]) + struct.pack(fmt, node.value)
    if tag in _STRING_TAGS:
        return bytes([tag]) + encode_length(len(node.value)) + bytes(node.value)
    if tag == Tag.BIT_STRING:
        return bytes([tag]) + encode_length(len(node.value) * 8) + bytes(node.value)
    if tag in (Tag.DATE_TIME, Tag.DATE, Tag.TIME):
        return bytes([tag]) + bytes(node.value)
    raise ValueError(f"cannot encode A-XDR tag 0x{tag:02X}")


# ------------------------------------------------------------------- date-time


def parse_datetime(raw: bytes) -> datetime | None:
    """DLMS date-time (12 bytes) to an aware datetime, or None if unusable.

    Returns None rather than raising when the meter marks fields as unspecified,
    which it does on a cold start before the clock has been set. A missing clock
    is not a reason to throw away an otherwise good telegram.
    """
    if len(raw) != 12:
        return None
    year = int.from_bytes(raw[0:2], "big")
    month, day = raw[2], raw[3]
    hour, minute, second = raw[5], raw[6], raw[7]
    deviation = int.from_bytes(raw[9:11], "big")
    status = raw[11]

    if year == 0xFFFF or month in (0xFF, 0xFE, 0xFD) or day in (0xFF, 0xFE, 0xFD):
        return None
    if 0xFF in (hour, minute, second):
        return None
    if status != 0xFF and status & 0x01:
        # Bit 0 is "invalid clock". Trust the meter when it says so.
        return None

    try:
        if deviation == _DEVIATION_UNSPECIFIED:
            # No offset given. Read it as container local time, which Supervisor
            # sets from the Home Assistant timezone.
            return datetime(year, month, day, hour, minute, second).astimezone()
        # DLMS states the deviation as UTC minus local time, so a location one
        # hour east of Greenwich sends -60.
        offset = timedelta(minutes=-int.from_bytes(raw[9:11], "big", signed=True))
        return datetime(year, month, day, hour, minute, second, tzinfo=timezone(offset))
    except ValueError:
        return None


def encode_datetime(value: datetime, deviation_minutes: int | None = None) -> bytes:
    """Inverse of `parse_datetime`, for the simulator."""
    if deviation_minutes is None:
        offset = value.utcoffset()
        deviation_minutes = -int(offset.total_seconds() // 60) if offset else 0
    return (
        value.year.to_bytes(2, "big")
        + bytes(
            [value.month, value.day, value.isoweekday(), value.hour, value.minute, value.second]
        )
        + b"\xff"  # hundredths not used
        + deviation_minutes.to_bytes(2, "big", signed=True)
        + b"\x00"  # clock status: valid
    )


def utcnow() -> datetime:
    return datetime.now(UTC)
