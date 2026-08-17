"""DSMR P1: the DLMS APDU with no wrapper around it at all.

Energienetze Steiermark, Energienetze Graz and Kaernten Netz put the ciphering
APDU straight onto the line at 115200 8N1 and nothing else. There is no start
byte, no checksum and no length prefix outside the APDU, so the APDU has to
delimit itself, which it does:

    DB              general-glo-ciphering
    08              system title length, always 8 here
    xx * 8          system title
    LL | 82 LL LL   length of everything after this field
    ...             security control, frame counter, ciphertext, GCM tag

Total length is therefore 10 + the size of the length field + the declared
length, and that is knowable from the first 13 bytes. Anything that does not
parse as that header costs one byte and the scan starts again, which is the same
resynchronisation the M-Bus reader does and for the same reason: the line is
noisy when the cable is first plugged in, and a real APDU can follow the noise
immediately.

These meters use security control 0x30, meaning authenticated as well as
encrypted, so the operator issues two keys: the GUEK for decryption and the GAK
for the tag. With both set the authenticity of every telegram is checked for
real, which is a stronger guarantee than the M-Bus operators can offer.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..dlms.apdu import GENERAL_DED_CIPHERING, GENERAL_GLO_CIPHERING
from ..dlms.axdr import read_length
from ..errors import ParseError
from . import Framer

_LOGGER = logging.getLogger(__name__)

SYSTEM_TITLE_LENGTH = 8

#: A DSMR telegram in the readable ASCII form starts with this. Austrian
#: operators encrypt instead, so seeing it means something is misconfigured.
ASCII_TELEGRAM_START = 0x2F  # "/"

#: Guards against a corrupt length field asking us to buffer for ever.
MAX_APDU = 4096


class RawApduFramer(Framer):
    description = "DSMR P1, unwrapped DLMS"

    def __init__(self, on_frame: Callable[[bytes], None] | None = None) -> None:
        super().__init__(on_frame)
        self._buffer = bytearray()
        self._warned_ascii = False

    def feed(self, data: bytes) -> list[bytes]:
        self._buffer.extend(data)
        self._warn_if_unencrypted()
        if len(self._buffer) > 2 * MAX_APDU:
            dropped = len(self._buffer) - 2 * MAX_APDU
            del self._buffer[:dropped]
            self.stats.discarded += dropped

        messages = []
        while True:
            message = self._take_one()
            if message is None:
                return messages
            self._accept_frame(message)
            self.stats.messages += 1
            messages.append(message)

    def reset(self) -> None:
        self._buffer.clear()

    @property
    def pending(self) -> int:
        return len(self._buffer)

    # ----------------------------------------------------------------- private

    def _take_one(self) -> bytes | None:
        buf = self._buffer
        while True:
            start = _find_start(buf)
            if start is None:
                # Keep the last byte: it may be the 0xDB of a header that is
                # still arriving.
                keep = 1 if buf[-1:] in (b"\xdb", b"\xdc") else 0
                self.stats.discarded += len(buf) - keep
                del buf[: len(buf) - keep]
                return None
            if start:
                self.stats.discarded += start
                del buf[:start]

            if len(buf) < 2 + SYSTEM_TITLE_LENGTH + 1:
                return None  # header still arriving
            if buf[1] != SYSTEM_TITLE_LENGTH:
                self._skip_one()
                continue

            try:
                declared, after_length = read_length(bytes(buf), 2 + SYSTEM_TITLE_LENGTH)
            except ParseError:
                if len(buf) < 2 + SYSTEM_TITLE_LENGTH + 5:
                    return None  # length field still arriving
                self._skip_one()
                continue

            total = after_length + declared
            if declared < 5 or total > MAX_APDU:
                self.stats.checksum_errors += 1
                self._skip_one()
                continue
            if len(buf) < total:
                return None  # body still arriving

            message = bytes(buf[:total])
            del buf[:total]
            return message

    def _skip_one(self) -> None:
        del self._buffer[:1]
        self.stats.discarded += 1

    def _warn_if_unencrypted(self) -> None:
        if self._warned_ascii or self._buffer[:1] != bytes([ASCII_TELEGRAM_START]):
            return
        self._warned_ascii = True
        _LOGGER.error(
            "The meter is sending readable DSMR text telegrams rather than encrypted "
            "DLMS. That means the customer interface is running in an older mode than "
            "this add-on reads. Ask your operator to enable the encrypted interface, or "
            "open an issue with a capture."
        )


def _find_start(buf: bytearray) -> int | None:
    """Earliest ciphering tag in the buffer, or None."""
    found = [buf.find(tag) for tag in (GENERAL_GLO_CIPHERING, GENERAL_DED_CIPHERING)]
    candidates = [index for index in found if index != -1]
    return min(candidates) if candidates else None
