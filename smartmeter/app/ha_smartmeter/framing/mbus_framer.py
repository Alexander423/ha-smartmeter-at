"""Wired M-Bus, the interface the KSM West operators use.

This is a thin wrapper: the scanner and the segment reassembler already existed
as separate pieces and are worth keeping that way, because between them they
carry all the awkward cases. All this adds is the common Framer shape.
"""

from __future__ import annotations

from collections.abc import Callable

from ..mbus.reader import FrameReader
from ..mbus.reassembly import Reassembler, TsapSetting
from . import Framer


class MBusFramer(Framer):
    description = "wired M-Bus, meter is bus master"

    def __init__(
        self,
        tsap: TsapSetting = "auto",
        timeout: float = 15.0,
        on_frame: Callable[[bytes], None] | None = None,
    ) -> None:
        super().__init__(on_frame)
        self.reader = FrameReader()
        self.reassembler = Reassembler(tsap=tsap, timeout=timeout)

    def feed(self, data: bytes) -> list[bytes]:
        messages = []
        for frame in self.reader.feed(data):
            self._accept_frame(frame.raw)
            message = self.reassembler.push(frame)
            if message is not None:
                self.stats.messages += 1
                messages.append(message)
        self._sync()
        return messages

    def check_timeout(self) -> None:
        self.reassembler.check_timeout()
        self._sync()

    def reset(self) -> None:
        self.reader.reset()
        self.reassembler.reset()

    @property
    def resolved_tsap(self) -> tuple[int, int] | None:
        return self.reassembler.resolved_tsap

    def _sync(self) -> None:
        self.stats.checksum_errors = self.reader.stats.checksum_errors
        self.stats.discarded = self.reader.stats.discarded
        self.stats.timeouts = self.reassembler.stats.timeouts
        self.stats.out_of_order = self.reassembler.stats.out_of_order
