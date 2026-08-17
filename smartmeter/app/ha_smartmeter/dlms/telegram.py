"""Decrypted bytes to a `Telegram`.

The plaintext is a COSEM data-notification:

    0F                  data-notification
    xx xx xx xx         long-invoke-id-and-priority
    LL <LL bytes>       date-time, length 0 when the meter sends no clock
    ...                 notification body, one A-XDR item

What is inside the notification body is the part operators disagree on. Two
layouts are supported and both are chosen in the supplier profile, not here:

`obis_tagged`
    Each value is preceded by its OBIS code as a six byte octet string. This is
    self-describing, so a meter that omits the L2 and L3 values because it is
    single-phase simply produces fewer readings.

`positional`
    Values arrive in a fixed order with no codes, and the order comes from the
    profile. Getting this wrong silently mislabels every value, so a profile
    using it should say where the order was verified.

Trailing bytes after the notification body are ignored on purpose. When the
security control byte has the authentication bit clear there is no tag field,
but some meters append the 12 GCM tag bytes anyway, and they land here as noise.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime

from ..errors import KeyMismatchError, ParseError
from ..models import Reading, Telegram
from ..obis import BY_OBIS, ObisEntry, format_obis, looks_like_obis
from . import axdr
from .axdr import Node, Tag

_LOGGER = logging.getLogger(__name__)

DATA_NOTIFICATION = 0x0F
INVOKE_ID_LENGTH = 4

#: The subset of the DLMS unit table this interface can produce. Used to check
#: the profile against the meter, not to override it.
UNIT_BY_ENUM: dict[int, str] = {
    27: "W",
    28: "VA",
    29: "var",
    30: "Wh",
    31: "VAh",
    32: "varh",
    33: "A",
    35: "V",
    44: "Hz",
}


def parse_telegram(
    plaintext: bytes,
    *,
    layout: str = "obis_tagged",
    obis_order: Sequence[str] = (),
    scales: Mapping[str, float] | None = None,
) -> Telegram:
    body, clock = _split_notification(plaintext)
    scales = scales or {}

    pairs: list[tuple[str, Node, Node | None]] = []
    if layout == "positional":
        _collect_positional(body, obis_order, pairs)
    else:
        _collect_tagged(body, pairs)

    telegram = Telegram(timestamp=clock)
    for obis, value_node, scaler_node in pairs:
        entry = BY_OBIS.get(obis)
        if entry is None:
            _LOGGER.debug("Ignoring OBIS %s, not a value this add-on publishes", obis)
            continue
        try:
            value = _coerce(entry, value_node, scaler_node, scales)
        except (ValueError, TypeError, OverflowError) as exc:
            _LOGGER.debug("Could not read a value for %s (%s): %s", entry.key, obis, exc)
            continue
        if value is None:
            continue
        telegram.readings[entry.key] = Reading(
            key=entry.key, obis=obis, value=value, unit=entry.unit
        )

    if telegram.timestamp is None:
        clock_reading = telegram.readings.get("clock")
        if clock_reading is not None and isinstance(clock_reading.value, datetime):
            telegram.timestamp = clock_reading.value

    if not telegram.readings:
        raise KeyMismatchError(
            "the telegram parsed but carried no recognised OBIS codes",
            hint=(
                "The telegram decoded to something that is not a meter reading, which almost "
                "always means the key is wrong. If the key is definitely right, your meter uses "
                "a layout this add-on does not know yet: enable 'capture_raw' and open an issue."
            ),
        )
    return telegram


# ------------------------------------------------------------------- APDU head


def _split_notification(plaintext: bytes) -> tuple[Node, datetime | None]:
    if not plaintext:
        raise KeyMismatchError("decryption produced no data")
    if plaintext[0] != DATA_NOTIFICATION:
        raise KeyMismatchError(
            f"plaintext starts with 0x{plaintext[0]:02X}, expected a data-notification (0x0F)"
        )
    offset = 1 + INVOKE_ID_LENGTH
    if len(plaintext) <= offset:
        raise ParseError("data-notification ends before the clock field")

    clock_length = plaintext[offset]
    offset += 1
    if offset + clock_length > len(plaintext):
        raise ParseError("data-notification clock field runs past the end of the message")
    clock_raw = plaintext[offset : offset + clock_length]
    offset += clock_length
    clock = axdr.parse_datetime(clock_raw) if clock_length == 12 else None

    try:
        body, consumed = axdr.decode(plaintext, offset)
    except ParseError as exc:
        raise KeyMismatchError(f"notification body is not valid A-XDR: {exc}") from exc
    if consumed < len(plaintext):
        _LOGGER.debug("Ignoring %d bytes after the notification body", len(plaintext) - consumed)
    return body, clock


# ------------------------------------------------------------- value collection


def _collect_tagged(node: Node, out: list[tuple[str, Node, Node | None]]) -> None:
    """Find every OBIS code in the tree and pair it with the item after it."""
    if not node.is_container:
        return
    children = node.children

    # A container of exactly [obis, value, scaler_unit] is the register form.
    if (
        len(children) == 3
        and _is_obis(children[0])
        and children[1].is_number
        and children[2].is_container
    ):
        out.append((format_obis(children[0].value), children[1], children[2]))
        return

    index = 0
    while index < len(children):
        child = children[index]
        if _is_obis(child) and index + 1 < len(children):
            out.append((format_obis(child.value), children[index + 1], None))
            index += 2
            continue
        _collect_tagged(child, out)
        index += 1


def _collect_positional(
    node: Node, obis_order: Sequence[str], out: list[tuple[str, Node, Node | None]]
) -> None:
    if not obis_order:
        raise ParseError(
            "the profile uses the positional layout but lists no OBIS order",
            hint="Pick a different grid operator, or fix the supplier profile.",
        )
    values = [child for child in node.children if not _is_obis(child)]
    if len(values) < len(obis_order):
        _LOGGER.debug(
            "Telegram has %d values, the profile expects %d", len(values), len(obis_order)
        )
    for obis, value_node in zip(obis_order, values, strict=False):
        out.append((obis, value_node, None))


def _is_obis(node: Node) -> bool:
    return node.tag == Tag.OCTET_STRING and looks_like_obis(node.value)


# ------------------------------------------------------------- value conversion


def _coerce(
    entry: ObisEntry,
    node: Node,
    scaler_node: Node | None,
    scales: Mapping[str, float],
) -> float | int | str | datetime | None:
    if node.tag == Tag.NULL:
        return None

    if node.tag == Tag.DATE_TIME:
        return axdr.parse_datetime(bytes(node.value))

    if node.is_container:
        return _coerce_register(entry, node, scales)

    if node.tag in (Tag.OCTET_STRING, Tag.VISIBLE_STRING, Tag.UTF8_STRING):
        raw = bytes(node.value)
        if entry.device_class == "timestamp":
            return axdr.parse_datetime(raw)
        return raw.decode("ascii", errors="replace").strip("\x00").strip()

    if node.is_number:
        scale = _scale_for(entry, scaler_node, scales)
        value = node.value * scale
        return round(value, 6) if isinstance(value, float) else value

    if node.tag == Tag.BOOLEAN:
        return int(node.value)

    _LOGGER.debug("No conversion for A-XDR tag 0x%02X on %s", node.tag, entry.key)
    return None


def _coerce_register(
    entry: ObisEntry, node: Node, scales: Mapping[str, float]
) -> float | int | str | datetime | None:
    """A register comes as a structure of the value and its scaler and unit."""
    children = node.children
    value_node = next((c for c in children if c.is_number), None)
    if value_node is None:
        return None
    scaler_node = next((c for c in children if c.is_container), None)
    return _coerce(entry, value_node, scaler_node, scales)


def _scale_for(entry: ObisEntry, scaler_node: Node | None, scales: Mapping[str, float]) -> float:
    override = scales.get(entry.key)
    if override is not None:
        return override
    if scaler_node is not None:
        scale = _scale_from_node(entry, scaler_node)
        if scale is not None:
            return scale
    return entry.scale


def _scale_from_node(entry: ObisEntry, node: Node) -> float | None:
    """Read a scaler_unit structure: an int8 exponent and a unit enum."""
    children = node.children
    if len(children) < 2:
        return None
    exponent, unit = children[0], children[1]
    if not exponent.is_number or unit.tag not in (Tag.ENUM, Tag.UINT8):
        return None
    reported = UNIT_BY_ENUM.get(int(unit.value))
    if reported is not None and entry.unit is not None and reported != entry.unit:
        _LOGGER.debug("%s arrives in %s but is published as %s", entry.key, reported, entry.unit)
    return float(10 ** int(exponent.value))
