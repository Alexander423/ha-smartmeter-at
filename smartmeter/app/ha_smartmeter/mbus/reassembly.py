"""Reassembles a DLMS message that was split across several M-Bus frames.

One frame carries at most 250 bytes of DLMS payload. A three-phase meter
reporting all three phases routinely exceeds that, so segmentation is the normal
case and not an edge case.

The CI field says how the frame fits into the message:

    bits 7-6-5   must be zero; anything else means a separate M-Bus data header
                 is present, which this interface never uses
    bit 4 (FIN)  0 = more segments follow, 1 = last or only segment
    bits 3-0     segment number, counting from zero

A single-segment message therefore has CI = 0x10. A two-segment message has
CI = 0x00 followed by CI = 0x11.

Everything here resets rather than waits. A reassembly buffer that is never
cleared is the easiest way to make this program hang silently, so an
out-of-order segment, a stale buffer or a foreign CI field all throw the partial
message away and start again.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..models import MBusFrame

_LOGGER = logging.getLogger(__name__)

#: Frames arrive 5 s apart, so a message still incomplete after this long is
#: never going to be completed.
DEFAULT_TIMEOUT = 15.0

TsapSetting = tuple[int, int] | None | str


@dataclass(slots=True)
class ReassemblyStats:
    messages: int = 0
    segments: int = 0
    out_of_order: int = 0
    timeouts: int = 0
    foreign_ci: int = 0


@dataclass(slots=True)
class Reassembler:
    """Frames in, complete DLMS messages out.

    `tsap` is the (STSAP, DTSAP) pair the meter puts in front of the DLMS data,
    normally (0x01, 0x67). Pass None if the operator's meter omits them, or
    "auto" to decide from the first segment of the first message.
    """

    tsap: TsapSetting = "auto"
    timeout: float = DEFAULT_TIMEOUT
    clock: Callable[[], float] = time.monotonic

    stats: ReassemblyStats = field(default_factory=ReassemblyStats)
    _buffer: bytearray = field(default_factory=bytearray)
    _next_segment: int = 0
    _started_at: float = 0.0
    _active: bool = False
    _resolved_tsap: tuple[int, int] | None = None
    _tsap_resolved: bool = False

    def __post_init__(self) -> None:
        if self.tsap != "auto":
            self._resolved_tsap = self.tsap  # type: ignore[assignment]
            self._tsap_resolved = True

    # ------------------------------------------------------------------ public

    def push(self, frame: MBusFrame) -> bytes | None:
        """Returns the complete DLMS message once the last segment arrives."""
        if frame.has_mbus_data_header:
            self.stats.foreign_ci += 1
            _LOGGER.debug(
                "Ignoring frame with CI 0x%02X: this interface never sets the upper CI bits",
                frame.ci_field,
            )
            return None

        self._expire_if_stale()
        segment = frame.segment_number
        self.stats.segments += 1

        if segment == 0:
            if self._active:
                self.stats.out_of_order += 1
                _LOGGER.warning(
                    "A new telegram started before the previous one finished. "
                    "Discarding %d incomplete bytes.",
                    len(self._buffer),
                )
            self._start()
        elif not self._active:
            # Joined mid-message, which happens on every start-up. Not worth a warning.
            _LOGGER.debug("Ignoring segment %d, waiting for the start of a telegram", segment)
            return None
        elif segment != self._next_segment:
            self.stats.out_of_order += 1
            _LOGGER.warning(
                "Telegram segment %d arrived where %d was expected. Discarding the telegram.",
                segment,
                self._next_segment,
            )
            self._reset()
            return None

        self._buffer.extend(self._strip_tsap(frame.payload, first=segment == 0))
        self._next_segment = (segment + 1) & 0x0F

        if not frame.is_final:
            return None

        message = bytes(self._buffer)
        self._reset()
        self.stats.messages += 1
        return message

    def check_timeout(self) -> None:
        """Called from the main loop so a stalled message clears without new frames."""
        self._expire_if_stale()

    def reset(self) -> None:
        self._reset()

    @property
    def pending_bytes(self) -> int:
        return len(self._buffer)

    @property
    def resolved_tsap(self) -> tuple[int, int] | None:
        return self._resolved_tsap

    # ----------------------------------------------------------------- private

    def _start(self) -> None:
        self._buffer.clear()
        self._next_segment = 0
        self._started_at = self.clock()
        self._active = True

    def _reset(self) -> None:
        self._buffer.clear()
        self._next_segment = 0
        self._active = False

    def _expire_if_stale(self) -> None:
        if self._active and self.clock() - self._started_at > self.timeout:
            self.stats.timeouts += 1
            _LOGGER.warning(
                "Gave up on a telegram that was still incomplete after %.0f s. "
                "If this repeats, the link to the meter is losing bytes.",
                self.timeout,
            )
            self._reset()

    def _strip_tsap(self, payload: bytes, first: bool) -> bytes:
        if not self._tsap_resolved:
            if not first:
                return payload  # cannot tell from a continuation segment
            self._resolve_tsap(payload)
        if self._resolved_tsap is None:
            return payload
        stsap, dtsap = self._resolved_tsap
        if len(payload) >= 2 and payload[0] == stsap and payload[1] == dtsap:
            return payload[2:]
        _LOGGER.debug(
            "Expected TSAP bytes %02X %02X but the frame starts with %s; passing it through as is",
            stsap,
            dtsap,
            payload[:2].hex(),
        )
        return payload

    def _resolve_tsap(self, payload: bytes) -> None:
        """Decide once whether this meter prefixes the DLMS data with TSAP bytes.

        The DLMS ciphering APDU always starts with 0xDB, so if the payload does
        not, the two bytes in front of it are the TSAPs.
        """
        self._tsap_resolved = True
        if payload[:1] == b"\xdb":
            self._resolved_tsap = None
            _LOGGER.info("Meter sends DLMS data without TSAP bytes")
        elif len(payload) >= 3 and payload[2] == 0xDB:
            self._resolved_tsap = (payload[0], payload[1])
            _LOGGER.info(
                "Meter sends TSAP bytes %02X %02X before the DLMS data",
                payload[0],
                payload[1],
            )
        else:
            # Unrecognised. Assume the documented pair and let decryption complain.
            self._resolved_tsap = (0x01, 0x67)
            _LOGGER.debug(
                "Could not tell whether TSAP bytes are present, assuming 01 67. Payload starts %s",
                payload[:4].hex(),
            )
