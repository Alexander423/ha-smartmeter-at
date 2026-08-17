"""The add-on's live state, as one object.

The read loop writes it, the ingress page reads it. Keeping it in one place
means the status page cannot drift out of step with what the add-on is actually
doing, and it gives the log something to summarise on shutdown.

`snapshot` returns plain JSON types. It never contains the key: the only fields
copied out of the configuration are the ones a user would paste into an issue.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

#: Encrypted frame hex kept for the status page. Enough to see the shape of a
#: segmented telegram, small enough not to matter.
RECENT_FRAMES = 6


@dataclass(slots=True)
class Status:
    profile_name: str = ""
    profile_status: str = "assumed"
    profile_notes: str = ""
    source: str = ""
    version: str = ""

    started_at: float = field(default_factory=time.time)
    #: "starting", "waiting for the meter", "reading", "no serial device",
    #: "wrong key" and so on. Written as a sentence, shown verbatim.
    state: str = "starting"
    detail: str = ""

    last_telegram_at: float | None = None
    last_frame_at: float | None = None
    frame_counter: int = 0
    meter_number: str = ""
    authenticated: bool = False
    values: dict[str, Any] = field(default_factory=dict)

    frames: int = 0
    telegrams: int = 0
    checksum_errors: int = 0
    discarded_bytes: int = 0
    reassembly_timeouts: int = 0
    out_of_order: int = 0
    decode_failures: int = 0

    mqtt_connected: bool = False
    mqtt_published: int = 0
    mqtt_skipped: int = 0
    entities: int = 0

    capture_path: str = ""
    capture_frames: int = 0

    last_error: str = ""
    last_error_hint: str = ""

    recent_frames: deque[str] = field(default_factory=lambda: deque(maxlen=RECENT_FRAMES))

    def note_frame(self, raw: bytes) -> None:
        self.last_frame_at = time.time()
        self.recent_frames.appendleft(raw.hex())

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        return {
            "state": self.state,
            "detail": self.detail,
            "version": self.version,
            "profile": {
                "name": self.profile_name,
                "status": self.profile_status,
                "notes": self.profile_notes,
            },
            "source": self.source,
            "uptime_seconds": round(now - self.started_at),
            "meter": {
                "number": self.meter_number,
                "frame_counter": self.frame_counter,
                "authenticated": self.authenticated,
            },
            "last_telegram": _iso(self.last_telegram_at),
            "seconds_since_telegram": _age(self.last_telegram_at, now),
            "last_frame": _iso(self.last_frame_at),
            "seconds_since_frame": _age(self.last_frame_at, now),
            "values": self.values,
            "counters": {
                "frames": self.frames,
                "telegrams": self.telegrams,
                "checksum_errors": self.checksum_errors,
                "discarded_bytes": self.discarded_bytes,
                "reassembly_timeouts": self.reassembly_timeouts,
                "out_of_order_segments": self.out_of_order,
                "decode_failures": self.decode_failures,
            },
            "mqtt": {
                "connected": self.mqtt_connected,
                "published": self.mqtt_published,
                "skipped_by_throttle": self.mqtt_skipped,
                "entities": self.entities,
            },
            "capture": {"path": self.capture_path, "frames": self.capture_frames},
            "error": {"message": self.last_error, "hint": self.last_error_hint},
            "recent_frames": list(self.recent_frames),
        }


def _iso(value: float | None) -> str | None:
    return datetime.fromtimestamp(value, UTC).isoformat() if value else None


def _age(value: float | None, now: float) -> int | None:
    return round(now - value) if value else None
