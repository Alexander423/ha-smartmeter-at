"""The real serial port.

pyserial is blocking, so reads happen in a worker thread. The read is written to
return as soon as anything arrives rather than waiting for a whole frame,
because framing is the job of the layer above and a serial read that waits for a
fixed number of bytes deadlocks the moment the meter sends one byte fewer than
expected.

This module is the only one in the add-on that imports pyserial.
"""

from __future__ import annotations

import asyncio
import logging

import serial

from ..errors import SerialUnavailableError
from ..suppliers import SerialSettings
from . import Source

_LOGGER = logging.getLogger(__name__)

#: How long a read waits before returning nothing, so the caller can run its
#: housekeeping (reassembly timeouts, availability watchdog) on a regular beat.
READ_TIMEOUT = 1.0

_PARITY = {
    "N": serial.PARITY_NONE,
    "E": serial.PARITY_EVEN,
    "O": serial.PARITY_ODD,
}

_STOPBITS = {
    1: serial.STOPBITS_ONE,
    2: serial.STOPBITS_TWO,
}


class SerialSource(Source):
    def __init__(self, port: str, settings: SerialSettings) -> None:
        self._port = port
        self._settings = settings
        self._serial: serial.Serial | None = None

    @property
    def description(self) -> str:
        return f"{self._port} at {self._settings.describe()}"

    async def open(self) -> None:
        await asyncio.to_thread(self._open_blocking)
        _LOGGER.info("Listening on %s", self.description)

    def _open_blocking(self) -> None:
        settings = self._settings
        try:
            self._serial = serial.Serial(
                port=self._port,
                baudrate=settings.baudrate,
                bytesize=settings.bytesize,
                parity=_PARITY.get(settings.parity, serial.PARITY_EVEN),
                stopbits=_STOPBITS.get(settings.stopbits, serial.STOPBITS_ONE),
                timeout=READ_TIMEOUT,
            )
        except (serial.SerialException, OSError) as exc:
            raise SerialUnavailableError(
                f"cannot open {self._port}: {exc}",
                hint=(
                    f"Home Assistant cannot open {self._port}. Check that the M-Bus adapter "
                    "is plugged in, then reopen the add-on configuration and pick the device "
                    "from the list again: USB devices can change name after a reboot."
                ),
            ) from exc

    async def read(self) -> bytes:
        return await asyncio.to_thread(self._read_blocking)

    def _read_blocking(self) -> bytes:
        port = self._serial
        if port is None:
            raise SerialUnavailableError("serial port is not open")
        try:
            first = port.read(1)
            if not first:
                return b""
            waiting = port.in_waiting
            return first + (port.read(waiting) if waiting else b"")
        except (serial.SerialException, OSError) as exc:
            raise SerialUnavailableError(
                f"lost {self._port}: {exc}",
                hint=(
                    f"The connection to {self._port} dropped. This normally means the adapter "
                    "was unplugged. It will be picked up again automatically once it is back."
                ),
            ) from exc

    async def close(self) -> None:
        port, self._serial = self._serial, None
        if port is not None:
            await asyncio.to_thread(port.close)
