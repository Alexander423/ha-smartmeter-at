"""The general-glo-ciphering wrapper around the encrypted telegram.

    DB                  general-glo-ciphering
    08                  length of the system title
    xx * 8              system title, unique per meter
    LL [LL]             length of everything that follows
    21                  security control byte
    xx * 4              frame counter
    ...                 ciphertext, and a 12 byte GCM tag if the security
                        control byte says the message is authenticated

On the security control byte: bits 0-3 are the security suite, bit 4 means
authentication is applied, bit 5 means encryption is applied, bit 6 selects the
broadcast key set. So the 0x21 the Austrian meters send is security suite 1 with
encryption and no authentication, which means there is no GCM tag on the wire to
verify. See `crypto.py` for what is done instead. If a meter turns out to set
bit 4 as well, the tag is picked up here and verified there, so both cases work.

Some operators are documented with the frame counter in front of the security
control byte. `header_order="auto"` decides per message by looking for the
expected security control byte in both positions.
"""

from __future__ import annotations

import logging
from typing import Literal

from ..errors import ParseError
from ..models import CipheredApdu
from .axdr import encode_length, read_length

_LOGGER = logging.getLogger(__name__)

GENERAL_GLO_CIPHERING = 0xDB
GENERAL_DED_CIPHERING = 0xDC

SYSTEM_TITLE_LENGTH = 8
FRAME_COUNTER_LENGTH = 4
GCM_TAG_LENGTH = 12

HeaderOrder = Literal["sc_fc", "fc_sc", "auto"]


def parse_ciphered_apdu(
    data: bytes,
    *,
    expected_security_control: int | None = None,
    header_order: HeaderOrder = "sc_fc",
) -> CipheredApdu:
    """Split a reassembled DLMS message into its ciphering fields."""
    if len(data) < 16:
        raise ParseError(f"ciphered APDU of {len(data)} bytes is too short")

    tag = data[0]
    if tag not in (GENERAL_GLO_CIPHERING, GENERAL_DED_CIPHERING):
        raise ParseError(
            f"expected a general-glo-ciphering APDU (0xDB) but the message starts with 0x{tag:02X}"
        )

    title_length = data[1]
    if title_length != SYSTEM_TITLE_LENGTH:
        raise ParseError(f"system title is {title_length} bytes, expected {SYSTEM_TITLE_LENGTH}")
    system_title = data[2 : 2 + title_length]
    offset = 2 + title_length

    declared, offset = read_length(data, offset)
    available = len(data) - offset
    if declared > available:
        raise ParseError(
            f"APDU declares {declared} bytes after the length field but only {available} are there"
        )
    if declared < available:
        _LOGGER.debug("Ignoring %d bytes after the declared APDU length", available - declared)
    body = data[offset : offset + declared]

    if len(body) < 1 + FRAME_COUNTER_LENGTH:
        raise ParseError(f"APDU body of {len(body)} bytes has no room for a header")

    security_control, frame_counter, ciphertext = _split_header(
        body, expected_security_control, header_order
    )

    if expected_security_control is not None and security_control != expected_security_control:
        _LOGGER.debug(
            "Security control byte is 0x%02X, the profile expects 0x%02X",
            security_control,
            expected_security_control,
        )

    authenticated = bool(security_control & 0x10)
    tag_bytes: bytes | None = None
    if authenticated:
        if len(ciphertext) <= GCM_TAG_LENGTH:
            raise ParseError("message claims authentication but is too short to hold a tag")
        tag_bytes = ciphertext[-GCM_TAG_LENGTH:]
        ciphertext = ciphertext[:-GCM_TAG_LENGTH]

    if not ciphertext:
        raise ParseError("APDU carries no ciphertext")

    return CipheredApdu(
        system_title=system_title,
        security_control=security_control,
        frame_counter=frame_counter,
        ciphertext=ciphertext,
        tag=tag_bytes,
    )


def _split_header(body: bytes, expected: int | None, order: HeaderOrder) -> tuple[int, int, bytes]:
    if order == "auto":
        order = _detect_order(body, expected)
    if order == "fc_sc":
        frame_counter = int.from_bytes(body[:FRAME_COUNTER_LENGTH], "big")
        return body[FRAME_COUNTER_LENGTH], frame_counter, body[FRAME_COUNTER_LENGTH + 1 :]
    frame_counter = int.from_bytes(body[1 : 1 + FRAME_COUNTER_LENGTH], "big")
    return body[0], frame_counter, body[1 + FRAME_COUNTER_LENGTH :]


def _detect_order(body: bytes, expected: int | None) -> HeaderOrder:
    """Pick the header layout by looking for the expected security control byte.

    Falls back to the documented order, which is the one every profile shipped
    here uses.
    """
    if expected is None:
        return "sc_fc"
    if body[0] == expected:
        return "sc_fc"
    if body[FRAME_COUNTER_LENGTH] == expected:
        _LOGGER.info("Meter sends the frame counter before the security control byte")
        return "fc_sc"
    return "sc_fc"


def build_ciphered_apdu(
    system_title: bytes,
    security_control: int,
    frame_counter: int,
    ciphertext: bytes,
    tag: bytes | None = None,
) -> bytes:
    """Inverse of `parse_ciphered_apdu`. Used by the simulator."""
    if len(system_title) != SYSTEM_TITLE_LENGTH:
        raise ValueError(f"system title must be {SYSTEM_TITLE_LENGTH} bytes")
    body = (
        bytes([security_control])
        + frame_counter.to_bytes(FRAME_COUNTER_LENGTH, "big")
        + ciphertext
        + (tag or b"")
    )
    return (
        bytes([GENERAL_GLO_CIPHERING, SYSTEM_TITLE_LENGTH])
        + system_title
        + encode_length(len(body))
        + body
    )
