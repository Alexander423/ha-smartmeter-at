"""Capture mode: writes received frames to a file so they can be sent to us.

This is how a new operator gets supported. The user turns on `capture_raw`,
waits a minute, and attaches the file to an issue; it replays through
`ReplaySource` exactly as if it had come off the wire.

Two rules the code enforces rather than trusts:

The key is never written. Frames do not contain it, but a header line easily
could, so everything written goes through the same redaction the log uses, and
there is a test for it.

The file cannot grow without limit. A capture writes about 60 frames a minute
forever if the user forgets to switch it off, on a device whose storage is an SD
card. So it stops at a fixed number of frames and says so.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from .logging_setup import redact

_LOGGER = logging.getLogger(__name__)

#: About twenty minutes of telegrams, and a few hundred kilobytes.
DEFAULT_MAX_FRAMES = 2000

CAPTURE_SUBDIR = "captures"


class FrameCapture:
    """Appends frames to a hex file, redacted and bounded."""

    def __init__(
        self,
        directory: Path,
        secrets: Iterable[str] = (),
        max_frames: int = DEFAULT_MAX_FRAMES,
        header: str = "",
        now: datetime | None = None,
    ) -> None:
        self._secrets = tuple(secrets)
        self._max_frames = max_frames
        self._count = 0
        self._closed = False
        stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
        self._directory = Path(directory) / CAPTURE_SUBDIR
        self._directory.mkdir(parents=True, exist_ok=True)
        self.path = self._directory / f"capture-{stamp}.hex"
        self._handle = self.path.open("w", encoding="utf-8")
        self._write_header(header)
        _LOGGER.info(
            "Capturing raw frames to %s. Turn capture_raw off again when you have enough; "
            "it stops on its own after %d frames.",
            self.path,
            max_frames,
        )

    @property
    def frames_written(self) -> int:
        return self._count

    @property
    def finished(self) -> bool:
        return self._closed

    def write(self, raw: bytes) -> None:
        """`raw` is one link-layer frame exactly as it arrived on the wire."""
        if self._closed:
            return
        if self._count >= self._max_frames:
            self._finish(f"stopped after {self._max_frames} frames")
            return
        self._line(raw.hex())
        self._count += 1

    def close(self) -> None:
        if not self._closed:
            self._finish(f"{self._count} frames")

    # ----------------------------------------------------------------- private

    def _write_header(self, header: str) -> None:
        self._line("# M-Bus frames from an Austrian smart meter customer interface.")
        self._line("# One frame per line as hex. Encrypted: the payload is unreadable")
        self._line("# without the key, and the key is not in this file.")
        for line in header.splitlines():
            self._line(f"# {line}")
        self._line("#")

    def _line(self, text: str) -> None:
        self._handle.write(redact(text, self._secrets) + "\n")
        self._handle.flush()

    def _finish(self, reason: str) -> None:
        self._closed = True
        self._line(f"# end of capture, {reason}")
        self._handle.close()
        _LOGGER.info("Capture finished: %s written to %s", reason, self.path)
