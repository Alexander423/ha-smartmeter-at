"""Turns a stream of bytes from the serial port into whole M-Bus frames.

A serial line gives you bytes at arbitrary boundaries, and it gives you noise
when the adapter is plugged in mid-telegram or the parity setting is wrong. So
this is a resynchronising scanner rather than a parser: it looks for a plausible
frame header, verifies the checksum, and on any failure drops a single byte and
looks again. Dropping one byte rather than the whole buffer matters, because a
valid start byte can appear inside the noise ahead of a real frame.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..models import MBusFrame
from .frame import START, STOP, checksum

_LOGGER = logging.getLogger(__name__)

#: A frame is at most 6 + 255 bytes. Twice that is enough to hold a partial
#: frame plus a full one; beyond that we are accumulating noise.
_MAX_BUFFER = 2 * (6 + 0xFF)


@dataclass(slots=True)
class ReaderStats:
    frames: int = 0
    checksum_errors: int = 0
    #: Bytes thrown away while looking for a frame start.
    discarded: int = 0


@dataclass(slots=True)
class FrameReader:
    """Feed bytes in, get complete frames out."""

    _buffer: bytearray = field(default_factory=bytearray)
    stats: ReaderStats = field(default_factory=ReaderStats)

    def feed(self, data: bytes) -> list[MBusFrame]:
        self._buffer.extend(data)
        if len(self._buffer) > _MAX_BUFFER:
            dropped = len(self._buffer) - _MAX_BUFFER
            del self._buffer[:dropped]
            self.stats.discarded += dropped
            _LOGGER.warning(
                "Dropped %d bytes of unparseable input. If this keeps happening the serial "
                "settings do not match the meter.",
                dropped,
            )
        return list(self._drain())

    def reset(self) -> None:
        """Called when the serial connection is re-established."""
        self._buffer.clear()

    @property
    def pending(self) -> int:
        return len(self._buffer)

    def _drain(self):
        while True:
            frame = self._take_one()
            if frame is None:
                return
            yield frame

    def _take_one(self) -> MBusFrame | None:
        buf = self._buffer
        while True:
            start = buf.find(START)
            if start == -1:
                # Nothing usable at all.
                self.stats.discarded += len(buf)
                buf.clear()
                return None
            if start:
                self.stats.discarded += start
                del buf[:start]

            if len(buf) < 4:
                return None  # wait for the header
            length = buf[1]
            if buf[2] != length or buf[3] != START or length < 3:
                self._skip_one()
                continue

            total = 6 + length
            if len(buf) < total:
                return None  # wait for the body

            body = bytes(buf[4 : 4 + length])
            if buf[4 + length] != checksum(body) or buf[total - 1] != STOP:
                self.stats.checksum_errors += 1
                _LOGGER.debug(
                    "Discarding a frame that failed its checksum: %s", bytes(buf[:total]).hex()
                )
                self._skip_one()
                continue

            del buf[:total]
            self.stats.frames += 1
            return MBusFrame(
                c_field=body[0],
                a_field=body[1],
                ci_field=body[2],
                payload=body[3:],
                raw=bytes(b"\x68" + bytes([length, length]) + b"\x68" + body)
                + bytes([checksum(body), STOP]),
            )

    def _skip_one(self) -> None:
        """Advance past a false start byte."""
        del self._buffer[:1]
        self.stats.discarded += 1
