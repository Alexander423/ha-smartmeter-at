"""DLMS over HDLC, the interface Wiener Netze uses through an optical read head.

    7E              opening flag
    FF FL           frame format: 0xA in the top nibble, then the segmentation
                    bit, then an 11 bit length
    dest addr       1 to 4 bytes, the last one has bit 0 set
    src addr        1 to 4 bytes, same rule
    C               control
    HCS             CRC-16/X.25 over everything since the opening flag,
                    present only when there is an information field
    info            LLC header on the first segment, then the DLMS APDU
    FCS             CRC-16/X.25 over everything since the opening flag
    7E              closing flag

The length counts from the format field through the FCS, so a frame occupies
length + 2 bytes on the wire. Adjacent frames are allowed to share a flag byte,
which is why the scan looks for 0x7E rather than assuming a gap.

Long telegrams are split with the segmentation bit rather than by the M-Bus CI
field, so the same "reset rather than wait" rule applies here: a segment that
never gets its continuation is dropped after a timeout instead of poisoning
every telegram that follows.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from . import Framer

_LOGGER = logging.getLogger(__name__)

FLAG = 0x7E

#: Top nibble of the frame format field. Type 3 is the only one DLMS uses.
FORMAT_TYPE_3 = 0xA0
SEGMENTATION_BIT = 0x08

#: Logical service access points. The meter is the server, so it sends E6 E7 00.
LLC_FROM_SERVER = b"\xe6\xe7\x00"
LLC_FROM_CLIENT = b"\xe6\xe6\x00"

#: A type 3 frame cannot be longer than the 11 bit length field allows.
MAX_FRAME = 2 + 0x7FF


def crc16_x25(data: bytes) -> int:
    """CRC-16/X.25: reflected 0x1021, initial 0xFFFF, final xor 0xFFFF."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


class HdlcFramer(Framer):
    description = "DLMS over HDLC, optical read head"

    def __init__(
        self,
        timeout: float = 15.0,
        on_frame: Callable[[bytes], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(on_frame)
        self._buffer = bytearray()
        self._segments = bytearray()
        self._active = False
        self._started_at = 0.0
        self._timeout = timeout
        self._clock = clock

    def feed(self, data: bytes) -> list[bytes]:
        self._buffer.extend(data)
        if len(self._buffer) > 2 * MAX_FRAME:
            dropped = len(self._buffer) - 2 * MAX_FRAME
            del self._buffer[:dropped]
            self.stats.discarded += dropped
        self.check_timeout()

        messages = []
        while True:
            frame = self._take_frame()
            if frame is None:
                return messages
            message = self._reassemble(*frame)
            if message is not None:
                self.stats.messages += 1
                messages.append(message)

    def check_timeout(self) -> None:
        if self._active and self._clock() - self._started_at > self._timeout:
            self.stats.timeouts += 1
            _LOGGER.warning(
                "Gave up on an HDLC telegram that was still incomplete after %.0f s.",
                self._timeout,
            )
            self._segments.clear()
            self._active = False

    def reset(self) -> None:
        self._buffer.clear()
        self._segments.clear()
        self._active = False

    @property
    def pending(self) -> int:
        return len(self._buffer)

    # ----------------------------------------------------------------- private

    def _take_frame(self) -> tuple[bytes, bool] | None:
        """Returns (information field, more segments follow) or None."""
        buf = self._buffer
        while True:
            start = buf.find(FLAG)
            if start == -1:
                self.stats.discarded += len(buf)
                buf.clear()
                return None
            if start:
                self.stats.discarded += start
                del buf[:start]
            if len(buf) < 2:
                return None
            # A run of flags is idle line, not a frame.
            if buf[1] == FLAG:
                del buf[:1]
                continue
            if len(buf) < 4:
                return None

            if buf[1] & 0xF0 != FORMAT_TYPE_3:
                self._skip_one()
                continue
            length = ((buf[1] & 0x07) << 8) | buf[2]
            segmented = bool(buf[1] & SEGMENTATION_BIT)
            # The length counts the format field through the FCS, so the closing
            # flag sits one past that, at index 1 + length.
            end = 1 + length
            if length < 5 or length + 2 > MAX_FRAME:
                self._skip_one()
                continue
            if len(buf) <= end:
                return None  # closing flag not here yet
            if buf[end] != FLAG:
                self._skip_one()
                continue

            frame = bytes(buf[1:end])  # format field through FCS
            del buf[:end]  # leave the closing flag, it may open the next frame

            info = self._check_and_extract(frame)
            if info is None:
                self.stats.checksum_errors += 1
                continue
            self._accept_frame(bytes([FLAG]) + frame + bytes([FLAG]))
            return info, segmented

    def _check_and_extract(self, frame: bytes) -> bytes | None:
        """Verify the check sequences and return the information field."""
        offset = 2
        for _ in range(2):  # destination then source address
            offset = _skip_address(frame, offset)
            if offset is None:
                return None
        offset += 1  # control byte
        if offset + 2 > len(frame):
            return None

        if crc16_x25(frame[: len(frame) - 2]) != int.from_bytes(frame[-2:], "little"):
            _LOGGER.debug("HDLC frame check sequence mismatch: %s", frame.hex())
            return None

        if len(frame) <= offset + 2:
            return b""  # a control frame with no information field
        if crc16_x25(frame[:offset]) != int.from_bytes(frame[offset : offset + 2], "little"):
            _LOGGER.debug("HDLC header check sequence mismatch: %s", frame.hex())
            return None
        return frame[offset + 2 : -2]

    def _reassemble(self, info: bytes, segmented: bool) -> bytes | None:
        if not info:
            return None
        if not self._active:
            self._segments.clear()
            self._active = True
            self._started_at = self._clock()
            info = _strip_llc(info)
        self._segments.extend(info)
        if segmented:
            return None
        message = bytes(self._segments)
        self._segments.clear()
        self._active = False
        return message

    def _skip_one(self) -> None:
        del self._buffer[:1]
        self.stats.discarded += 1


def _skip_address(frame: bytes, offset: int) -> int | None:
    """Addresses run until a byte with bit 0 set, and are at most four bytes."""
    for length in range(1, 5):
        if offset + length > len(frame):
            return None
        if frame[offset + length - 1] & 0x01:
            return offset + length
    return None


def _strip_llc(info: bytes) -> bytes:
    for header in (LLC_FROM_SERVER, LLC_FROM_CLIENT):
        if info.startswith(header):
            return info[len(header) :]
    return info
