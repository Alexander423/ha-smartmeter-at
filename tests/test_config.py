from __future__ import annotations

import json

import pytest

from conftest import TEST_KEY_HEX
from ha_smartmeter.config import MqttSettings, Options
from ha_smartmeter.errors import ConfigError

VALID = {"supplier": "tinetz", "port": "/dev/ttyUSB0", "key": TEST_KEY_HEX}


def options(**overrides) -> Options:
    return Options.from_mapping({**VALID, **overrides})


class TestValidConfiguration:
    def test_defaults_are_sensible(self):
        parsed = options()
        assert parsed.min_publish_interval == 0  # publish every telegram
        assert parsed.capture_raw is False
        assert parsed.log_level == "info"
        assert parsed.stale_after == 30
        assert parsed.encryption_key.hex().upper() == TEST_KEY_HEX
        assert parsed.authentication_key is None

    def test_the_profile_is_resolved(self):
        assert options().profile.id == "tinetz"

    def test_an_auth_key_is_parsed_when_given(self):
        parsed = options(auth_key="000102030405060708090A0B0C0D0E0F")
        assert parsed.authentication_key == bytes(range(16))

    def test_replay_mode_needs_no_serial_port(self):
        parsed = options(port="", replay_file="/config/capture.hex")
        assert parsed.replay_mode is True

    def test_describe_names_the_profile_and_the_source(self):
        assert "TINETZ" in options().describe()
        assert "/dev/ttyUSB0" in options().describe()

    def test_secrets_lists_both_keys(self):
        parsed = options(auth_key="000102030405060708090A0B0C0D0E0F")
        assert parsed.secrets == (TEST_KEY_HEX, "000102030405060708090A0B0C0D0E0F")

    def test_secrets_holds_only_the_keys_that_are_set(self):
        assert options().secrets == (TEST_KEY_HEX,)


class TestRejectedConfiguration:
    @pytest.mark.parametrize(
        "overrides, hint_fragment",
        [
            ({"key": "nope"}, "0-9 and A-F"),
            ({"key": ""}, "Enter the key"),
            ({"supplier": "wiener-netze"}, "Choose one of"),
            ({"port": ""}, "pick your M-Bus adapter"),
            ({"log_level": "loud"}, "Choose one of"),
            ({"min_publish_interval": -5}, "publish every telegram"),
            ({"stale_after": 0}, "six missed telegrams"),
            ({"min_publish_interval": "often"}, "Set min_publish_interval to a number"),
        ],
    )
    def test_every_rejection_says_what_to_do(self, overrides, hint_fragment):
        with pytest.raises(ConfigError) as excinfo:
            options(**overrides)
        assert hint_fragment in excinfo.value.hint


class TestLoading:
    def test_reads_the_supervisor_options_file(self, tmp_path):
        path = tmp_path / "options.json"
        path.write_text(json.dumps(VALID), encoding="utf-8")
        assert Options.load(path).port == "/dev/ttyUSB0"

    def test_a_missing_file_is_explained(self, tmp_path):
        with pytest.raises(ConfigError, match="missing"):
            Options.load(tmp_path / "nothing.json")

    def test_broken_json_is_explained(self, tmp_path):
        path = tmp_path / "options.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ConfigError) as excinfo:
            Options.load(path)
        assert "defaults" in excinfo.value.hint


#: What run.sh exports after asking bashio for the broker.
BASHIO_ENV = {
    "MQTT_HOST": "core-mosquitto",
    "MQTT_PORT": "1883",
    "MQTT_USERNAME": "addons",
    "MQTT_PASSWORD": "secret",
}


class TestMqttSettings:
    def test_reads_what_bashio_put_in_the_environment(self):
        settings = MqttSettings.from_env(BASHIO_ENV)
        assert settings.host == "core-mosquitto"
        assert settings.port == 1883
        assert settings.username == "addons"
        assert settings.discovery_prefix == "homeassistant"
        assert settings.base_topic == "smartmeter"

    def test_a_missing_broker_tells_the_user_to_install_one(self):
        with pytest.raises(ConfigError) as excinfo:
            MqttSettings.from_env({})
        assert "Mosquitto" in excinfo.value.hint

    def test_prefixes_are_stripped_of_slashes(self):
        settings = MqttSettings.from_env(
            {**BASHIO_ENV, "MQTT_DISCOVERY_PREFIX": "/ha/", "MQTT_BASE_TOPIC": "/meter/"}
        )
        assert settings.discovery_prefix == "ha"
        assert settings.base_topic == "meter"
