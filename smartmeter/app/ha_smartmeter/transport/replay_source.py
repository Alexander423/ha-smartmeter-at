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
    """Split a flat list of frames into telegrams using the CI FIN bit."""
    telegrams: list[list[bytes]] = []
    current: list[bytes] = []
    for frame in frames:
        current.append(frame)
        ci = frame[6] if len(frame) > 6 else 0x10
        if ci & 0x10:
            telegrams.append(current)
            current = []
    if current:
        telegrams.append(current)
    return telegrams


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
