from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ha_smartmeter.dlms import axdr
from ha_smartmeter.dlms.axdr import Node, Tag
from ha_smartmeter.errors import ParseError


@pytest.mark.parametrize("length", [0, 1, 127, 128, 255, 256, 65535, 65536])
def test_length_round_trip(length):
    encoded = axdr.encode_length(length)
    assert axdr.read_length(encoded, 0) == (length, len(encoded))


def test_short_lengths_use_one_byte():
    assert axdr.encode_length(127) == b"\x7f"
    assert axdr.encode_length(128) == b"\x81\x80"
    assert axdr.encode_length(300) == b"\x82\x01\x2c"


@pytest.mark.parametrize(
    "node",
    [
        Node(Tag.NULL, None),
        Node(Tag.UINT8, 200),
        Node(Tag.INT8, -5),
        Node(Tag.UINT16, 65535),
        Node(Tag.INT16, -32768),
        Node(Tag.UINT32, 4_000_000_000),
        Node(Tag.INT32, -2_000_000_000),
        Node(Tag.UINT64, 2**63),
        Node(Tag.ENUM, 35),
        Node(Tag.BOOLEAN, True),
        Node(Tag.OCTET_STRING, b"\x01\x02\x03"),
        Node(Tag.VISIBLE_STRING, b"hello"),
        Node(Tag.FLOAT64, 1.5),
        Node(Tag.STRUCTURE, [Node(Tag.UINT8, 1), Node(Tag.OCTET_STRING, b"ab")]),
        Node(Tag.ARRAY, [Node(Tag.STRUCTURE, [Node(Tag.UINT16, 7)])]),
    ],
)
def test_encode_decode_round_trip(node):
    assert axdr.decode_one(axdr.encode(node)) == node


def test_long_octet_string_round_trip():
    node = Node(Tag.OCTET_STRING, bytes(range(256)) * 2)
    assert axdr.decode_one(axdr.encode(node)) == node


def test_unknown_tag_is_rejected():
    with pytest.raises(ParseError, match="unknown A-XDR tag"):
        axdr.decode_one(b"\xee\x00")


def test_truncated_item_is_rejected():
    with pytest.raises(ParseError, match="truncated"):
        axdr.decode_one(b"\x12\x00")  # uint16 with only one byte


def test_container_longer_than_the_buffer_is_rejected():
    # A wrong key produces bytes like this: a structure claiming 127 elements
    # with two bytes behind it. It must be rejected before the decode loop runs.
    with pytest.raises(ParseError, match="claims 127 elements"):
        axdr.decode_one(b"\x02\x7f\x11\x01")


def test_trailing_bytes_rejected_by_decode_one_but_reported_by_decode():
    data = axdr.encode(Node(Tag.UINT8, 1)) + b"\xff\xff"
    with pytest.raises(ParseError, match="trailing bytes"):
        axdr.decode_one(data)
    node, consumed = axdr.decode(data)
    assert node == Node(Tag.UINT8, 1)
    assert consumed == 2


def test_nesting_is_bounded():
    deep = b"\x02\x01" * 40 + b"\x00"
    with pytest.raises(ParseError, match="nesting"):
        axdr.decode_one(deep)


class TestDateTime:
    def test_round_trip_with_explicit_offset(self):
        # Central European Summer Time is UTC+2, which DLMS writes as -120.
        moment = datetime(2026, 8, 17, 14, 30, 15, tzinfo=timezone(timedelta(hours=2)))
        raw = axdr.encode_datetime(moment)
        assert raw[9:11] == (-120).to_bytes(2, "big", signed=True)
        assert axdr.parse_datetime(raw) == moment

    def test_unspecified_deviation_falls_back_to_local_time(self):
        raw = axdr.encode_datetime(datetime(2026, 1, 2, 3, 4, 5), deviation_minutes=-0x8000)
        parsed = axdr.parse_datetime(raw)
        assert parsed is not None
        assert parsed.utcoffset() is not None
        assert (parsed.year, parsed.month, parsed.day) == (2026, 1, 2)

    @pytest.mark.parametrize(
        "raw",
        [
            b"\xff\xff\x08\x11\x01\x0e\x1e\x0f\xff\xff\x88\x00",  # year unspecified
            b"\x07\xea\xff\x11\x01\x0e\x1e\x0f\xff\xff\x88\x00",  # month unspecified
            b"\x07\xea\x08\x11\x01\xff\x1e\x0f\xff\xff\x88\x00",  # hour unspecified
            b"\x07\xea\x08\x11\x01\x0e\x1e\x0f\xff\xff\x88\x01",  # clock marked invalid
            b"\x07\xea\x0d\x11\x01\x0e\x1e\x0f\xff\xff\x88\x00",  # month 13
        ],
    )
    def test_unusable_clock_returns_none_rather_than_raising(self, raw):
        assert axdr.parse_datetime(raw) is None

    def test_wrong_length_returns_none(self):
        assert axdr.parse_datetime(b"\x07\xea") is None
