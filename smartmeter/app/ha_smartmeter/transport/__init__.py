"""Where the bytes come from.

`Source` is the only interface the rest of the add-on knows about. The serial
implementation is the real one; the replay implementation reads captured hex
from a file at the same pace, which is how this add-on is developed without an
M-Bus adapter on the desk.
"""

from __future__ import annotations

import abc


class Source(abc.ABC):
    """A stream of bytes arriving from somewhere."""

    @property
    @abc.abstractmethod
    def description(self) -> str:
        """One line for the log and the status page."""

    @abc.abstractmethod
    async def open(self) -> None:
        """Raises SerialUnavailableError if the source cannot be opened."""

    @abc.abstractmethod
    async def read(self) -> bytes:
        """Wait for bytes and return them. May return an empty chunk on timeout."""

    @abc.abstractmethod
    async def close(self) -> None: ...


__all__ = ["Source"]
