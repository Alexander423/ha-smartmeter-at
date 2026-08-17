"""Replays captured frames from a file instead of reading a serial port.

The file holds one frame per line as hex. Blank lines and lines starting with #
are ignored, so a capture can carry a comment saying where it came from. This is
the same format capture mode writes, so a user's capture goes straight back in.

Frames are grouped into telegrams by the FIN bit in the CI field and released
one telegram at a time, which reproduces the 5 second push interval closely
enough to test everything above the serial layer.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..errors import ConfigError
from . import Source

_LOGGER = logging.getLogger(__name__)

DEFAULT_INTERVAL = 5.0


def parse_hex_file(text: str) -> list[bytes]:
    frames: list[bytes] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.split("#", 1)[0].strip().replace(" ", "")
        if not stripped:
            continue
        try:
            frames.append(bytes.fromhex(stripped))
        except ValueError as exc:
            raise ConfigError(
                f"line {number} of the replay file is not hexadecimal: {line.strip()!r}",
                hint="A replay file holds one frame per line, written as hex.",
            ) from exc
    return frames


def group_telegrams(frames: list[bytes]) -> list[list[bytes]]:
    """Split a flat list of frames into telegrams.

    The capture file does not say which interface it came from, but the frames
    do: an M-Bus frame starts with 0x68 and carries its FIN bit in the CI field,
    an HDLC frame starts with 0x7E and carries a segmentation bit in the frame
    format field, and a P1 telegram is a bare DLMS APDU starting with 0xDB that
    is complete on its own.
    """
    telegrams: list[list[bytes]] = []
    current: list[bytes] = []
    for frame in frames:
        current.append(frame)
        if _is_last_frame(frame):
            telegrams.append(current)
            current = []
    if current:
        telegrams.append(current)
    return telegrams


def _is_last_frame(frame: bytes) -> bool:
    if frame[:1] == b"\x68":
        return len(frame) <= 6 or bool(frame[6] & 0x10)  # CI FIN bit
    if frame[:1] == b"\x7e":
        return len(frame) <= 1 or not frame[1] & 0x08  # HDLC segmentation bit
    return True  # a bare APDU is a whole telegram


class ReplaySource(Source):
    def __init__(
        self,
        path: str | Path,
        interval: float = DEFAULT_INTERVAL,
        loop_forever: bool = True,
    ) -> None:
        self._path = Path(path)
        self._interval = interval
        self._loop_forever = loop_forever
        self._telegrams: list[list[bytes]] = []
        self._index = 0
        self._first = True

    @property
    def description(self) -> str:
        return f"replay of {self._path} every {self._interval:.0f} s"

    async def open(self) -> None:
        if not self._path.is_file():
            raise ConfigError(
                f"replay file {self._path} does not exist",
                hint=(
                    "Put a capture file at that path, or turn off replay mode in the "
                    "add-on configuration."
                ),
            )
        frames = parse_hex_file(self._path.read_text(encoding="utf-8"))
        if not frames:
            raise ConfigError(
                f"replay file {self._path} contains no frames",
                hint="The file needs at least one line of frame hex.",
            )
        self._telegrams = group_telegrams(frames)
        self._index = 0
        self._first = True
        _LOGGER.info(
            "Replaying %d telegrams (%d frames) from %s",
            len(self._telegrams),
            len(frames),
            self._path,
        )

    async def read(self) -> bytes:
        if not self._telegrams:
            raise ConfigError("replay source was not opened")
        if self._index >= len(self._telegrams):
            if not self._loop_forever:
                await asyncio.sleep(self._interval)
                return b""
            self._index = 0
        if self._first:
            self._first = False
        else:
            await asyncio.sleep(self._interval)
        telegram = self._telegrams[self._index]
        self._index += 1
        return b"".join(telegram)

    async def close(self) -> None:
        self._telegrams = []
