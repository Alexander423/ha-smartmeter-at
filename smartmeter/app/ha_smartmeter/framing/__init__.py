"""Link layers.

Austria does not have one customer interface, it has five, and which one you get
depends on your grid operator rather than on anything you can choose. Three of
them carry the same DLMS/COSEM payload underneath and differ only in how the
bytes are wrapped:

    mbus      Wired M-Bus long frames, 2400 8E1. The meter is the bus master.
              KSM West (Kaifa MA309M), Netz NOE, Netz Burgenland.
    p1        DSMR P1, 115200 8N1. No wrapper at all: the DLMS APDU goes
              straight onto the line and delimits itself.
              Energienetze Steiermark and Graz, Kaernten Netz.
    hdlc      DLMS over HDLC through an optical read head, 9600 8N1.
              Wiener Netze.

The other two carry something else entirely and are not handled here:

    oms-ir    OMS 3.0.1 over M-Bus with AES-128 in CBC mode, not DLMS.
              Netz Oberoesterreich (Siemens TD-3511 AMIS).
    mep       ANSI C12.19 over the Multipurpose Expansion Port.
              Linz Netz, Energie Klagenfurt.

A framer takes whatever arrives on the wire and gives back complete DLMS
messages. Everything above it is identical for all three supported interfaces,
which is the whole reason the layers are split this way.
"""

from __future__ import annotations

import abc
from collections.abc import Callable
from dataclasses import dataclass

#: Interfaces that produce a DLMS message this add-on can decrypt.
DLMS_INTERFACES = ("mbus", "p1", "hdlc")

#: Interfaces that exist in Austria but carry a different protocol.
FOREIGN_INTERFACES = ("oms-ir", "mep", "wmbus")

INTERFACES = DLMS_INTERFACES + FOREIGN_INTERFACES


@dataclass(slots=True)
class FramerStats:
    #: Link-layer frames that passed their checksum.
    frames: int = 0
    #: Complete DLMS messages handed upwards.
    messages: int = 0
    checksum_errors: int = 0
    #: Bytes thrown away while resynchronising.
    discarded: int = 0
    #: Partial messages abandoned because the rest never arrived.
    timeouts: int = 0
    #: Segments that turned up in the wrong order.
    out_of_order: int = 0


class Framer(abc.ABC):
    """Bytes from the wire in, complete DLMS messages out.

    `on_frame` is called with every link-layer frame that passed its checksum,
    before reassembly. Capture mode writes those, because a capture has to
    contain what was on the wire and not what this add-on made of it.
    """

    #: For the log and the docs.
    description = "unknown interface"

    def __init__(self, on_frame: Callable[[bytes], None] | None = None) -> None:
        self.stats = FramerStats()
        self._on_frame = on_frame

    @abc.abstractmethod
    def feed(self, data: bytes) -> list[bytes]:
        """Never raises for damaged input: it counts it and resynchronises."""

    @abc.abstractmethod
    def reset(self) -> None:
        """Throw away partial state, after the serial connection is remade."""

    def check_timeout(self) -> None:  # noqa: B027
        """Called on a regular beat so a stalled message cannot sit for ever.

        Deliberately optional rather than abstract: P1 has no partial state to
        expire, so there is nothing for it to do and nothing to implement.
        """

    def _accept_frame(self, raw: bytes) -> None:
        self.stats.frames += 1
        if self._on_frame is not None:
            self._on_frame(raw)


def build_framer(profile, on_frame: Callable[[bytes], None] | None = None) -> Framer:
    """Pick the framer the operator's interface needs."""
    from ..errors import ProfileError
    from .hdlc_framer import HdlcFramer
    from .mbus_framer import MBusFramer
    from .raw_framer import RawApduFramer

    interface = profile.interface
    if interface == "mbus":
        return MBusFramer(tsap=profile.tsap, timeout=profile.reassembly_timeout, on_frame=on_frame)
    if interface == "p1":
        return RawApduFramer(on_frame=on_frame)
    if interface == "hdlc":
        return HdlcFramer(timeout=profile.reassembly_timeout, on_frame=on_frame)
    raise ProfileError(
        f"profile {profile.id} uses the {interface} interface, which carries no DLMS",
        hint=profile.unsupported_hint(),
    )


__all__ = [
    "DLMS_INTERFACES",
    "FOREIGN_INTERFACES",
    "INTERFACES",
    "Framer",
    "FramerStats",
    "build_framer",
]
