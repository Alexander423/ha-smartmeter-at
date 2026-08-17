"""The read loop.

Reads from the source, decodes, publishes, repeats. Everything it can recover
from, it recovers from: a serial device that disappears is retried with a
backoff that tops out at a minute, a broker that goes away is paho's business,
and a telegram that will not decode costs one telegram. The container exits only
when the user stops the add-on or when the configuration is wrong in a way that
waiting cannot fix.

The watchdog is the other half of availability. The last will covers the add-on
dying; this covers the case where the add-on is fine but the meter has gone
quiet, which looks identical from Home Assistant unless somebody says so.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from . import __version__
from .capture import FrameCapture
from .config import MqttSettings, Options
from .decoder import Decoder
from .errors import SerialUnavailableError, SmartmeterError
from .models import MBusFrame
from .mqtt.publisher import MqttPublisher
from .status import Status
from .transport import Source
from .transport.replay_source import ReplaySource
from .transport.serial_source import SerialSource

_LOGGER = logging.getLogger(__name__)

RETRY_MIN = 1.0
RETRY_MAX = 60.0

#: How often the loop does its housekeeping even if no bytes arrive.
HOUSEKEEPING_INTERVAL = 1.0


def build_source(options: Options) -> Source:
    if options.replay_mode:
        return ReplaySource(options.replay_file)
    return SerialSource(options.port, options.profile.serial)


class Runner:
    def __init__(
        self,
        options: Options,
        mqtt_settings: MqttSettings,
        status: Status | None = None,
        source_factory=build_source,
    ) -> None:
        self.options = options
        self.status = status or Status()
        self._source_factory = source_factory
        self._profile = options.profile

        self._capture: FrameCapture | None = None
        self._decoder = Decoder(
            profile=self._profile,
            key=options.encryption_key,
            auth_key=options.authentication_key,
            on_frame=self._on_frame,
        )
        self._publisher = MqttPublisher(
            mqtt_settings,
            self._profile,
            device_name=options.device_name,
            min_publish_interval=options.min_publish_interval,
        )
        self._stopping = asyncio.Event()
        #: The read that is currently in flight, kept across housekeeping ticks.
        self._reader: asyncio.Future[bytes] | None = None

    # ------------------------------------------------------------------ public

    async def run(self) -> None:
        self._describe()
        self._start_capture()
        self._publisher.start()
        try:
            await self._loop()
        finally:
            await self._shutdown()

    def request_stop(self) -> None:
        self._stopping.set()

    # -------------------------------------------------------------- main loop

    async def _loop(self) -> None:
        delay = RETRY_MIN
        while not self._stopping.is_set():
            source = self._source_factory(self.options)
            try:
                await source.open()
            except SerialUnavailableError as exc:
                self._set_error("no serial device", exc)
                _LOGGER.error("%s", exc.hint)
                _LOGGER.debug("Underlying error: %s", exc)
                if await self._sleep(delay):
                    return
                delay = min(delay * 2, RETRY_MAX)
                continue

            delay = RETRY_MIN
            self.status.source = source.description
            self.status.state = "waiting for the first telegram"
            self.status.detail = "The meter sends one telegram every 5 seconds."
            self._decoder.reset()
            try:
                await self._read_until_lost(source)
            except SerialUnavailableError as exc:
                self._set_error("serial device lost", exc)
                _LOGGER.warning("%s", exc.hint)
                if await self._sleep(delay):
                    return
                delay = min(delay * 2, RETRY_MAX)
            finally:
                self._cancel_read()
                with contextlib.suppress(OSError, RuntimeError):
                    await source.close()

    async def _read_until_lost(self, source: Source) -> None:
        last_housekeeping = time.monotonic()
        while not self._stopping.is_set():
            data = await self._read(source)
            if data:
                self._handle(data)
            now = time.monotonic()
            if now - last_housekeeping >= HOUSEKEEPING_INTERVAL:
                last_housekeeping = now
                self._housekeeping()

    async def _read(self, source: Source) -> bytes:
        """Read, but come back regularly so housekeeping runs even when idle.

        The read is shielded, so a timeout leaves it in flight and the next pass
        waits on the same one. Starting a second concurrent read on the same
        serial port would interleave the bytes and corrupt every frame.
        """
        if self._reader is None:
            self._reader = asyncio.ensure_future(source.read())
        try:
            data = await asyncio.wait_for(
                asyncio.shield(self._reader), timeout=HOUSEKEEPING_INTERVAL
            )
        except TimeoutError:
            # The read is kept, not dropped. It may even have finished a moment
            # after the timer fired, in which case the next pass picks the bytes
            # up immediately. Clearing it here would throw away a whole read
            # every time the timer won the race, which on a slow serial line is
            # every time.
            return b""
        except Exception:
            self._reader = None
            raise
        self._reader = None
        return data

    def _cancel_read(self) -> None:
        reader, self._reader = self._reader, None
        if reader is not None and not reader.done():
            reader.cancel()

    def _handle(self, data: bytes) -> None:
        for telegram in self._decoder.feed(data):
            self.status.last_telegram_at = time.time()
            self.status.frame_counter = telegram.frame_counter
            self.status.meter_number = telegram.meter_number or ""
            self.status.authenticated = telegram.authenticated
            self.status.values = telegram.values()
            self.status.state = "reading"
            self.status.detail = ""
            self.status.last_error = ""
            self.status.last_error_hint = ""
            self._publisher.publish_telegram(telegram)
        self._sync_counters()

    def _housekeeping(self) -> None:
        self._decoder.check_timeout()
        self._sync_counters()
        self._check_staleness()

    def _check_staleness(self) -> None:
        last = self.status.last_telegram_at
        if last is None:
            return
        fresh = time.time() - last <= self.options.stale_after
        self._publisher.set_available(fresh)
        if not fresh and self.status.state == "reading":
            self.status.state = "no telegrams"
            self.status.detail = (
                f"Nothing has arrived for over {self.options.stale_after} seconds. "
                "Check the cable between the meter and the adapter."
            )

    def _sync_counters(self) -> None:
        status, decoder, publisher = self.status, self._decoder, self._publisher
        status.frames = decoder.reader.stats.frames
        status.checksum_errors = decoder.reader.stats.checksum_errors
        status.discarded_bytes = decoder.reader.stats.discarded
        status.reassembly_timeouts = decoder.reassembler.stats.timeouts
        status.out_of_order = decoder.reassembler.stats.out_of_order
        status.telegrams = decoder.stats.telegrams
        status.decode_failures = decoder.stats.decode_failures
        if decoder.stats.last_error and not status.last_error:
            status.last_error = decoder.stats.last_error
            status.last_error_hint = decoder.stats.last_error_hint
        status.mqtt_connected = publisher.connected
        status.mqtt_published = publisher.published
        status.mqtt_skipped = publisher.skipped
        status.entities = publisher.entity_count
        if self._capture is not None:
            status.capture_frames = self._capture.frames_written

    # ---------------------------------------------------------------- plumbing

    def _on_frame(self, frame: MBusFrame) -> None:
        self.status.note_frame(frame.raw)
        if self._capture is not None and not self._capture.finished:
            self._capture.write(frame)

    def _start_capture(self) -> None:
        if not self.options.capture_raw:
            return
        try:
            self._capture = FrameCapture(
                self.options.output_dir(),
                secrets=self.options.secrets,
                header=(
                    f"operator profile: {self._profile.id} ({self._profile.status})\n"
                    f"add-on version: {__version__}\n"
                    f"serial: {self._profile.serial.describe()}"
                ),
            )
            self.status.capture_path = str(self._capture.path)
        except OSError as exc:
            _LOGGER.error(
                "Could not start the capture file (%s). Everything else carries on as "
                "normal; switch capture_raw off to stop this message.",
                exc,
            )

    def _set_error(self, state: str, exc: SmartmeterError) -> None:
        self.status.state = state
        self.status.detail = exc.hint
        self.status.last_error = str(exc)
        self.status.last_error_hint = exc.hint
        self._publisher.set_available(False)

    def _describe(self) -> None:
        profile = self._profile
        self.status.version = __version__
        self.status.profile_name = profile.name
        self.status.profile_status = profile.status
        self.status.profile_notes = profile.notes
        self.status.source = self.options.describe()

        _LOGGER.info("Austrian smart meter add-on %s", __version__)
        _LOGGER.info("Operator profile: %s", profile.name)
        if profile.status != "verified":
            _LOGGER.warning(
                "The %s profile has not been confirmed against a physical meter (%s). "
                "If the values look wrong, that is why. Enable capture_raw and open an "
                "issue so it can be fixed.",
                profile.name,
                profile.status,
            )
        if profile.notes:
            _LOGGER.info("%s", profile.notes)
        if self.options.replay_mode:
            _LOGGER.warning(
                "Replay mode is on, so the values below come from %s and not from your meter.",
                self.options.replay_file,
            )
        if self.options.min_publish_interval:
            _LOGGER.info(
                "Publishing at most one update every %d s. The meter still sends every "
                "5 s; the extra telegrams are dropped.",
                self.options.min_publish_interval,
            )

    async def _sleep(self, seconds: float) -> bool:
        """Wait, returning True if a stop was requested while waiting."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)
        return self._stopping.is_set()

    async def _shutdown(self) -> None:
        _LOGGER.info(
            "Stopping after %d telegrams (%d frames, %d decode failures)",
            self.status.telegrams,
            self.status.frames,
            self.status.decode_failures,
        )
        self.status.state = "stopped"
        self._cancel_read()
        if self._capture is not None:
            with contextlib.suppress(OSError):
                self._capture.close()
        await asyncio.to_thread(self._publisher.stop)


async def run_from_options(options: Options, status: Status | None = None) -> Runner:
    """Used by the CLI. Kept separate so tests can drive Runner directly."""
    runner = Runner(options, MqttSettings.from_env(), status=status)
    await runner.run()
    return runner
