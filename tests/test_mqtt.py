from __future__ import annotations

import json

import pytest

from conftest import TEST_KEY
from ha_smartmeter.config import MqttSettings
from ha_smartmeter.decoder import Decoder
from ha_smartmeter.mqtt import discovery, publisher
from ha_smartmeter.mqtt.publisher import OFFLINE, ONLINE, MqttPublisher
from ha_smartmeter.obis import BY_KEY

SETTINGS = MqttSettings(host="core-mosquitto", username="addons", password="x")


class FakeClient:
    """Stands in for paho. Records instead of connecting."""

    def __init__(self, *args, **kwargs):
        self.published: list[tuple[str, str, bool]] = []
        self.will: tuple[str, str, bool] | None = None
        self.started = False
        self.on_connect = None
        self.on_disconnect = None

    def username_pw_set(self, username, password):
        self.credentials = (username, password)

    def will_set(self, topic, payload, qos=0, retain=False):
        self.will = (topic, payload, retain)

    def reconnect_delay_set(self, *args, **kwargs):
        pass

    def connect_async(self, host, port, keepalive=60):
        self.target = (host, port)

    def loop_start(self):
        self.started = True

    def loop_stop(self):
        self.started = False

    def disconnect(self):
        pass

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.published.append((topic, payload, retain))
        return FakeInfo()

    def topics(self, prefix=""):
        return [t for t, _, _ in self.published if t.startswith(prefix)]

    def payload_for(self, topic):
        for candidate, payload, _ in reversed(self.published):
            if candidate == topic:
                return payload
        raise AssertionError(f"nothing published to {topic}")


class FakeInfo:
    def wait_for_publish(self, timeout=None):
        return True


@pytest.fixture
def fake_client(monkeypatch):
    holder = {}

    def factory(*args, **kwargs):
        holder["client"] = FakeClient()
        return holder["client"]

    monkeypatch.setattr(publisher.mqtt, "Client", factory)
    return holder


@pytest.fixture
def telegram(tinetz, sim):
    return Decoder(profile=tinetz, key=TEST_KEY).feed(sim.next_telegram())[0]


@pytest.fixture
def single_phase_telegram(tinetz, single_phase_sim):
    return Decoder(profile=tinetz, key=TEST_KEY).feed(single_phase_sim.next_telegram())[0]


class TestDiscoveryPayloads:
    def test_energy_sensors_work_in_the_energy_dashboard(self):
        payload = discovery.sensor_payload(BY_KEY["active_energy_plus"], "meter1", "smartmeter", {})
        assert payload["device_class"] == "energy"
        assert payload["state_class"] == "total_increasing"
        assert payload["unit_of_measurement"] == "Wh"

    def test_power_sensors_are_measurements(self):
        payload = discovery.sensor_payload(BY_KEY["active_power_plus"], "m", "smartmeter", {})
        assert payload["device_class"] == "power"
        assert payload["state_class"] == "measurement"
        assert payload["unit_of_measurement"] == "W"

    def test_reactive_energy_has_no_device_class(self):
        # Home Assistant only learned about reactive energy recently and an
        # unknown device class breaks the entity outright.
        payload = discovery.sensor_payload(BY_KEY["reactive_energy_plus"], "m", "smartmeter", {})
        assert "device_class" not in payload
        assert payload["state_class"] == "total_increasing"

    def test_diagnostics_are_categorised(self):
        payload = discovery.sensor_payload(BY_KEY["meter_number"], "m", "smartmeter", {})
        assert payload["entity_category"] == "diagnostic"

    def test_the_logical_device_name_is_off_until_asked_for(self):
        payload = discovery.sensor_payload(BY_KEY["logical_device_name"], "m", "smartmeter", {})
        assert payload["enabled_by_default"] is False

    def test_every_sensor_has_availability_and_a_unique_id(self):
        for entry in BY_KEY.values():
            payload = discovery.sensor_payload(entry, "meter1", "smartmeter", {})
            assert payload["availability_topic"] == "smartmeter/status"
            assert payload["unique_id"] == f"meter1_{entry.key}"
            assert payload["state_topic"] == "smartmeter/meter1/state"

    def test_the_device_carries_the_meter_serial(self, tinetz):
        device = discovery.device_payload(tinetz, "1SAG123", "1SAG123")
        assert device["identifiers"] == ["1SAG123"]
        assert device["serial_number"] == "1SAG123"
        assert device["manufacturer"] == "Kaifa"

    def test_a_device_name_can_be_overridden(self, tinetz):
        device = discovery.device_payload(tinetz, "n", "1SAG123", "Cellar meter")
        assert device["name"] == "Cellar meter"

    @pytest.mark.parametrize(
        "meter_number, system_title, expected",
        [
            ("1SAG1234567890", b"\x00" * 8, "1SAG1234567890"),
            ("1 SAG/12", b"\x00" * 8, "1_SAG_12"),
            (None, bytes.fromhex("5341470102030405"), "meter_5341470102030405"),
            ("", bytes.fromhex("5341470102030405"), "meter_5341470102030405"),
        ],
    )
    def test_node_ids_are_stable_and_topic_safe(self, meter_number, system_title, expected):
        assert discovery.node_id(meter_number, system_title) == expected


class TestPublishing:
    def test_the_last_will_is_set_before_connecting(self, fake_client):
        pub = MqttPublisher(SETTINGS, None)
        pub.start()
        client = fake_client["client"]
        assert client.will == ("smartmeter/status", OFFLINE, True)
        assert client.started

    def test_a_telegram_becomes_one_state_message(self, fake_client, tinetz, telegram):
        pub = MqttPublisher(SETTINGS, tinetz)
        pub.start()
        assert pub.publish_telegram(telegram) is True
        client = fake_client["client"]
        state = json.loads(client.payload_for("smartmeter/1SAG1234567890/state"))
        assert state["active_power_plus"] == 412
        assert state["voltage_l1"] == pytest.approx(231.4)

    def test_discovery_is_published_once_per_value(self, fake_client, tinetz, telegram):
        pub = MqttPublisher(SETTINGS, tinetz)
        pub.start()
        pub.publish_telegram(telegram)
        first = len(fake_client["client"].topics("homeassistant/"))
        assert first == len(telegram.readings)
        pub.publish_telegram(telegram)
        assert len(fake_client["client"].topics("homeassistant/")) == first

    def test_a_single_phase_meter_gets_no_l2_entities(
        self, fake_client, tinetz, single_phase_telegram
    ):
        pub = MqttPublisher(SETTINGS, tinetz)
        pub.start()
        pub.publish_telegram(single_phase_telegram)
        topics = fake_client["client"].topics("homeassistant/")
        assert not any("voltage_l2" in topic for topic in topics)
        assert any("voltage_l1" in topic for topic in topics)

    def test_a_value_that_turns_up_later_gets_announced_then(
        self, fake_client, tinetz, telegram, single_phase_telegram
    ):
        pub = MqttPublisher(SETTINGS, tinetz)
        pub.start()
        pub.publish_telegram(single_phase_telegram)
        before = len(fake_client["client"].topics("homeassistant/"))
        pub.publish_telegram(telegram)  # now with all three phases
        assert len(fake_client["client"].topics("homeassistant/")) > before

    def test_discovery_payloads_are_retained(self, fake_client, tinetz, telegram):
        pub = MqttPublisher(SETTINGS, tinetz)
        pub.start()
        pub.publish_telegram(telegram)
        for topic, _, retain in fake_client["client"].published:
            if topic.startswith("homeassistant/"):
                assert retain is True


class TestThrottle:
    def test_zero_publishes_every_telegram(self, fake_client, tinetz, telegram):
        clock = iter([0, 1, 2, 3]).__next__
        pub = MqttPublisher(SETTINGS, tinetz, min_publish_interval=0, clock=clock)
        pub.start()
        assert [pub.publish_telegram(telegram) for _ in range(3)] == [True, True, True]

    def test_an_interval_drops_the_telegrams_in_between(self, fake_client, tinetz, telegram):
        times = iter([0, 5, 10, 35]).__next__
        pub = MqttPublisher(SETTINGS, tinetz, min_publish_interval=30, clock=times)
        pub.start()
        assert [pub.publish_telegram(telegram) for _ in range(4)] == [True, False, False, True]
        assert pub.published == 2
        assert pub.skipped == 2

    def test_throttled_telegrams_still_produce_discovery(self, fake_client, tinetz, telegram):
        times = iter([0, 1]).__next__
        pub = MqttPublisher(SETTINGS, tinetz, min_publish_interval=30, clock=times)
        pub.start()
        pub.publish_telegram(telegram)
        assert pub.entity_count == len(telegram.readings)


class TestAvailability:
    def test_availability_flips_only_when_it_changes(self, fake_client, tinetz):
        pub = MqttPublisher(SETTINGS, tinetz)
        pub.start()
        pub.set_available(True)
        pub.set_available(True)
        pub.set_available(False)
        messages = [p for t, p, _ in fake_client["client"].published if t == "smartmeter/status"]
        assert messages == [ONLINE, OFFLINE]

    def test_stopping_publishes_offline(self, fake_client, tinetz):
        pub = MqttPublisher(SETTINGS, tinetz)
        pub.start()
        pub.connected = True
        pub.stop()
        assert fake_client["client"].published[-1] == ("smartmeter/status", OFFLINE, True)

    def test_a_reconnect_re_announces_everything(self, fake_client, tinetz, telegram):
        pub = MqttPublisher(SETTINGS, tinetz)
        pub.start()
        pub.publish_telegram(telegram)
        pub._on_connect(None, None, None, 0)  # broker came back
        assert pub.entity_count == 0
        pub.publish_telegram(telegram)
        assert pub.entity_count == len(telegram.readings)
