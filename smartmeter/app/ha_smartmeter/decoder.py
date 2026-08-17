"""Bytes in, telegrams out.

This is the whole protocol stack behind one method, with the transport left
out. `feed` never raises for a problem that the next telegram might not have:
a damaged frame, a lost segment or a telegram in an unexpected shape all get
counted and logged, and the loop carries on. Only a configuration fault, which
no amount of waiting will fix, propagates.

The one case worth shouting about is a wrong key, because it is both the most
common mistake and completely invisible otherwise: the add-on happily receives
frames forever and no entity ever appears. So consecutive decode failures are
counted and, once it is clear this is not a one-off, the user is told in one
sentence what to do.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from .dlms.apdu import parse_ciphered_apdu
from .dlms.crypto import decrypt
from .dlms.telegram import parse_telegram
from .errors import DecryptionError, FrameError, SmartmeterError
from .framing import Framer, build_framer
from .models import Telegram
from .obis import BY_KEY
from .suppliers import SupplierProfile

_LOGGER = logging.getLogger(__name__)

#: Failures in a row before the message is raised from DEBUG to ERROR.
#: Telegrams arrive every 5 s, so this is about fifteen seconds of patience.
FAILURES_BEFORE_ALARM = 3

#: Once the alarm has been raised it is not repeated until this many more
#: failures have gone by, which at 5 s a telegram is about an hour. Without it
#: the add-on log becomes a wall of the same sentence; with no repeat at all,
#: somebody who opens the log a day later sees nothing.
REPEAT_ALARM_EVERY = 720


@dataclass(slots=True)
class DecoderStats:
    telegrams: int = 0
    decode_failures: int = 0
    consecutive_failures: int = 0
    #: True once a telegram has been decoded, so the log can say so exactly once.
    ever_decoded: bool = False
    last_error: str = ""
    last_error_hint: str = ""


@dataclass(slots=True)
class Decoder:
    profile: SupplierProfile
    key: bytes
    auth_key: bytes | None = None
    #: Called with the raw bytes of every link-layer frame that passed its
    #: checksum, before reassembly. Capture mode writes these.
    on_frame: Callable[[bytes], None] | None = None

    framer: Framer = field(init=False)
    stats: DecoderStats = field(default_factory=DecoderStats)
    _warned_ranges: set[str] = field(default_factory=set)
    _alarmed: bool = False
    _warned_no_gak: bool = False

    def __post_init__(self) -> None:
        self.framer = build_framer(self.profile, on_frame=self.on_frame)

    # ------------------------------------------------------------------ public

    def feed(self, data: bytes) -> list[Telegram]:
        telegrams: list[Telegram] = []
        for message in self.framer.feed(data):
            try:
                telegrams.append(self.decode_message(message))
            except SmartmeterError as exc:
                self._record_failure(exc)
        return telegrams

    def decode_message(self, message: bytes) -> Telegram:
        """One reassembled DLMS message to a telegram. Raises on failure."""
        apdu = parse_ciphered_apdu(
            message,
            expected_security_control=self.profile.security_control,
            header_order=self.profile.header_order,
        )
        plaintext, authenticated = decrypt(apdu, self.key, self.auth_key)
        telegram = parse_telegram(
            plaintext,
            layout=self.profile.layout,
            obis_order=self.profile.obis_order,
            scales=self.profile.scales,
        )
        telegram.system_title = apdu.system_title
        telegram.frame_counter = apdu.frame_counter
        telegram.authenticated = authenticated

        self._check_ranges(telegram)
        self.stats.telegrams += 1
        self.stats.consecutive_failures = 0
        if self._alarmed:
            self._alarmed = False
            _LOGGER.info("Telegrams are decoding again")
        if not self.stats.ever_decoded:
            self.stats.ever_decoded = True
            self._log_first_success(telegram)
        return telegram

    def check_timeout(self) -> None:
        self.framer.check_timeout()

    def reset(self) -> None:
        """Called after the serial connection is re-established."""
        self.framer.reset()

    # ----------------------------------------------------------------- private

    def _log_first_success(self, telegram: Telegram) -> None:
        _LOGGER.info(
            "Reading meter %s, %d values per telegram: %s",
            telegram.meter_number or "(serial not reported)",
            len(telegram.readings),
            ", ".join(sorted(telegram.readings)),
        )
        if telegram.authenticated:
            _LOGGER.info("Telegram authenticity is verified against the GCM tag")
        elif self.profile.auth_key_expected and self.auth_key is None:
            # These meters do send a tag, so a missing second key is a setting
            # the user can fix rather than a limitation of the interface.
            _LOGGER.warning(
                "Your meter authenticates its telegrams but no authentication key is "
                "configured, so the tag is not being checked. Your operator's portal "
                "shows two keys: put the GUEK in 'key' and the GAK in 'auth_key'."
            )
        else:
            _LOGGER.info(
                "This meter sends no authentication tag, so telegrams are accepted on "
                "structure alone. That is normal for the Austrian customer interface."
            )
        missing = [
            code
            for code in self.profile.expected_obis
            if code not in {r.obis for r in telegram.readings.values()}
        ]
        if missing:
            _LOGGER.info(
                "The %s profile also expects %s. Missing values are normal on a "
                "single-phase meter.",
                self.profile.name,
                ", ".join(missing),
            )

    def _record_failure(self, exc: SmartmeterError) -> None:
        self.stats.decode_failures += 1
        self.stats.consecutive_failures += 1
        self.stats.last_error = str(exc)
        self.stats.last_error_hint = exc.hint

        if isinstance(exc, FrameError):
            _LOGGER.debug("Discarded a telegram: %s", exc)
            return

        if self._should_alarm(exc):
            self._alarmed = True
            _LOGGER.error("%s", exc.hint)
            _LOGGER.debug("Underlying decode error: %s", exc)
        else:
            _LOGGER.debug("Telegram could not be decoded: %s", exc)

    def _should_alarm(self, exc: SmartmeterError) -> bool:
        if self._alarmed:
            return self.stats.consecutive_failures % REPEAT_ALARM_EVERY == 0
        # A key problem on an add-on that has never decoded anything needs no
        # patience: nothing is going to improve on the next telegram. This
        # covers a wrong encryption key and a wrong authentication key alike.
        if isinstance(exc, DecryptionError) and not self.stats.ever_decoded:
            return True
        return self.stats.consecutive_failures >= FAILURES_BEFORE_ALARM

    def _check_ranges(self, telegram: Telegram) -> None:
        """Warn once per value when a reading is far outside anything plausible.

        A voltage of 2314 rather than 231.4 means the scaler is wrong, and the
        user has no way of knowing that from Home Assistant alone.
        """
        for key, reading in telegram.readings.items():
            entry = BY_KEY.get(key)
            if entry is None or entry.sane_range is None or key in self._warned_ranges:
                continue
            if not isinstance(reading.value, int | float):
                continue
            low, high = entry.sane_range
            if not (low <= reading.value <= high):
                self._warned_ranges.add(key)
                _LOGGER.warning(
                    "%s reads %s %s, which is outside the plausible range %s to %s. "
                    "The scaling for your meter is probably wrong: please open an issue "
                    "with a capture.",
                    entry.name,
                    reading.value,
                    entry.unit or "",
                    low,
                    high,
                )
