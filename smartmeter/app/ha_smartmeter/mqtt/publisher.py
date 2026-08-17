"""Publishes telegrams to the broker.

paho runs its own network thread, so nothing here blocks the read loop, and a
broker that is down or restarting is paho's problem rather than ours: the
connection is made asynchronously and retried on its own. Losing the broker must
never stop the add-on reading the meter.

Availability uses a retained last will. When the add-on stops, is killed, or
loses the network, the broker publishes "offline" for it and every entity goes
unavailable, instead of showing a power reading from three days ago as if it
were current.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt

from ..config import MqttSettings
from ..models import Telegram
from ..suppliers import SupplierProfile
from .discovery import (
    availability_topic,
    device_payload,
    discovery_topic,
    node_id,
    payloads_for,
    state_topic,
)

_LOGGER = logging.getLogger(__name__)

ONLINE = "online"
OFFLINE = "offline"

#: Reconnect backoff handed to paho, in seconds.
RECONNECT_MIN = 1
RECONNECT_MAX = 60


class MqttPublisher:
    def __init__(
        self,
        settings: MqttSettings,
        profile: SupplierProfile,
        device_name: str = "",
        min_publish_interval: int = 0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._profile = profile
        self._device_name = device_name
        self._min_interval = min_publish_interval
        self._clock = clock

        self._availability = availability_topic(settings.base_topic)
        self._node: str | None = None
        self._announced: set[str] = set()
        self._last_publish = float("-inf")
        self._available = False
        self._lock = threading.Lock()

        self.connected = False
        self.published = 0
        self.skipped = 0
        self.last_error = ""

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"{settings.base_topic}-{id(self):x}",
        )
        if settings.username:
            self._client.username_pw_set(settings.username, settings.password)
        self._client.will_set(self._availability, OFFLINE, qos=1, retain=True)
        self._client.reconnect_delay_set(RECONNECT_MIN, RECONNECT_MAX)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        _LOGGER.info(
            "Connecting to the MQTT broker at %s:%d", self._settings.host, self._settings.port
        )
        self._client.connect_async(self._settings.host, self._settings.port, keepalive=60)
        self._client.loop_start()

    def stop(self) -> None:
        """Say goodbye properly so entities go unavailable straight away."""
        try:
            if self.connected:
                self._client.publish(
                    self._availability, OFFLINE, qos=1, retain=True
                ).wait_for_publish(timeout=2)
        except (RuntimeError, ValueError, OSError) as exc:
            _LOGGER.debug("Could not publish the offline message: %s", exc)
        finally:
            self._client.loop_stop()
            self._client.disconnect()

    def publish_telegram(self, telegram: Telegram) -> bool:
        """Publish one telegram. Returns False when the throttle skipped it."""
        node = self._ensure_node(telegram)
        self._announce(node, telegram)

        now = self._clock()
        if self._min_interval and now - self._last_publish < self._min_interval:
            self.skipped += 1
            return False
        self._last_publish = now

        payload = json.dumps(telegram.values(), separators=(",", ":"))
        self._client.publish(state_topic(self._settings.base_topic, node), payload, retain=True)
        self.published += 1
        self.set_available(True)
        return True

    def set_available(self, available: bool) -> None:
        """Drives the availability topic from the read loop's watchdog."""
        with self._lock:
            if available == self._available:
                return
            self._available = available
        self._client.publish(
            self._availability, ONLINE if available else OFFLINE, qos=1, retain=True
        )
        if not available:
            _LOGGER.warning(
                "No telegram from the meter recently, entities are now unavailable in "
                "Home Assistant."
            )

    @property
    def node(self) -> str | None:
        return self._node

    @property
    def entity_count(self) -> int:
        return len(self._announced)

    # ----------------------------------------------------------------- private

    def _ensure_node(self, telegram: Telegram) -> str:
        if self._node is None:
            self._node = node_id(telegram.meter_number, telegram.system_title)
        return self._node

    def _announce(self, node: str, telegram: Telegram) -> None:
        """Publish discovery for values that have not been announced yet.

        Some meters only report export power once the sun is up, so this is
        checked on every telegram rather than only on the first.
        """
        new = [key for key in telegram.readings if key not in self._announced]
        if not new:
            return
        device = device_payload(self._profile, node, telegram.meter_number, self._device_name)
        for key, payload in payloads_for(new, node, self._settings.base_topic, device):
            self._publish_json(discovery_topic(self._settings.discovery_prefix, node, key), payload)
            self._announced.add(key)
        _LOGGER.info(
            "Announced %d %s to Home Assistant (%d in total)",
            len(new),
            "entity" if len(new) == 1 else "entities",
            len(self._announced),
        )

    def _publish_json(self, topic: str, payload: dict[str, Any]) -> None:
        self._client.publish(topic, json.dumps(payload, separators=(",", ":")), qos=1, retain=True)

    # ---------------------------------------------------------------- callbacks

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if reason_code != 0:
            self.connected = False
            self.last_error = str(reason_code)
            _LOGGER.error(
                "The MQTT broker refused the connection (%s). If you are not using the "
                "Mosquitto add-on, check that the broker allows the Home Assistant user in.",
                reason_code,
            )
            return
        self.connected = True
        self.last_error = ""
        _LOGGER.info("Connected to the MQTT broker")
        # A reconnect means the broker may have lost our retained discovery, and
        # it certainly published our last will. Announce everything again.
        self._announced.clear()
        with self._lock:
            self._available = False

    def _on_disconnect(self, client, userdata, flags=None, reason_code=None, properties=None):
        self.connected = False
        if reason_code:
            _LOGGER.warning("Lost the MQTT broker (%s), reconnecting", reason_code)
