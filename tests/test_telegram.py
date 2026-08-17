from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from conftest import TEST_KEY, WRONG_KEY
from ha_smartmeter.dlms import axdr
from ha_smartmeter.dlms.apdu import parse_ciphered_apdu
from ha_smartmeter.dlms.axdr import Node, Tag
from ha_smartmeter.dlms.crypto import decrypt
from ha_smartmeter.dlms.telegram import parse_telegram
from ha_smartmeter.errors import KeyMismatchError
from ha_smartmeter.obis import parse_obis
from ha_smartmeter.simulator import MeterSimulator

VIENNA_SUMMER = timezone(timedelta(hours=2))
MOMENT = datetime(2026, 8, 17, 14, 30, 15, tzinfo=VIENNA_SUMMER)


def decode(sim: MeterSimulator, key: bytes = TEST_KEY, **kwargs):
    apdu = parse_ciphered_apdu(sim.build_apdu(sim.build_plaintext(MOMENT)))
    plaintext, _ = decrypt(apdu, key)
    return parse_telegram(plaintext, **kwargs)


class TestObisTaggedLayout:
    def test_all_documented_values_are_read(self, sim):
        telegram = decode(sim)
        assert set(telegram.readings) == {
            "clock",
            "meter_number",
            "logical_device_name",
            "voltage_l1",
            "voltage_l2",
            "voltage_l3",
            "current_l1",
            "current_l2",
            "current_l3",
            "active_power_plus",
            "active_power_minus",
            "active_energy_plus",
            "active_energy_minus",
            "reactive_energy_plus",
            "reactive_energy_minus",
        }

    def test_values_survive_the_round_trip(self, sim):
        telegram = decode(sim)
        assert telegram.readings["voltage_l1"].value == pytest.approx(231.4)
        assert telegram.readings["current_l1"].value == pytest.approx(1.23)
        assert telegram.readings["active_power_plus"].value == 412
        assert telegram.readings["active_energy_plus"].value == 1234567
        assert telegram.readings["meter_number"].value == "1SAG1234567890"

    def test_units_come_from_the_registry(self, sim):
        telegram = decode(sim)
        assert telegram.readings["voltage_l1"].unit == "V"
        assert telegram.readings["active_energy_plus"].unit == "Wh"
        assert telegram.readings["reactive_energy_plus"].unit == "varh"

    def test_the_clock_is_read_from_the_apdu_header(self, sim):
        telegram = decode(sim)
        assert telegram.timestamp == MOMENT

    def test_a_single_phase_meter_simply_reports_fewer_values(self, single_phase_sim):
        telegram = decode(single_phase_sim)
        assert "voltage_l1" in telegram.readings
        assert "voltage_l2" not in telegram.readings
        assert "current_l3" not in telegram.readings
        # The values every meter sends are all still there.
        assert telegram.readings["active_power_plus"].value == 412

    def test_unknown_obis_codes_are_ignored(self):
        # 1-0:13.7.0.255 is the power factor, which this add-on does not publish.
        body = Node(
            Tag.STRUCTURE,
            [
                Node(Tag.OCTET_STRING, parse_obis("1-0:13.7.0.255")),
                Node(Tag.UINT16, 950),
                Node(Tag.OCTET_STRING, parse_obis("1-0:1.7.0.255")),
                Node(Tag.UINT32, 1500),
            ],
        )
        telegram = parse_telegram(_notification(body))
        assert set(telegram.readings) == {"active_power_plus"}


class TestScaling:
    def test_a_register_scaler_is_applied(self):
        body = Node(
            Tag.STRUCTURE,
            [
                Node(Tag.OCTET_STRING, parse_obis("1-0:32.7.0.255")),
                Node(
                    Tag.STRUCTURE,
                    [
                        Node(Tag.UINT32, 2314),
                        Node(Tag.STRUCTURE, [Node(Tag.INT8, -1), Node(Tag.ENUM, 35)]),
                    ],
                ),
            ],
        )
        telegram = parse_telegram(_notification(body))
        assert telegram.readings["voltage_l1"].value == pytest.approx(231.4)

    def test_a_profile_scale_overrides_the_registry(self):
        body = Node(
            Tag.STRUCTURE,
            [
                Node(Tag.OCTET_STRING, parse_obis("1-0:1.8.0.255")),
                Node(Tag.UINT32, 1234),
            ],
        )
        telegram = parse_telegram(_notification(body), scales={"active_energy_plus": 1000.0})
        assert telegram.readings["active_energy_plus"].value == pytest.approx(1_234_000)


class TestPositionalLayout:
    ORDER = ("1-0:1.7.0.255", "1-0:2.7.0.255", "1-0:1.8.0.255")

    def test_values_are_named_from_the_profile_order(self):
        body = Node(
            Tag.STRUCTURE,
            [Node(Tag.UINT32, 100), Node(Tag.UINT32, 0), Node(Tag.UINT32, 55555)],
        )
        telegram = parse_telegram(_notification(body), layout="positional", obis_order=self.ORDER)
        assert telegram.readings["active_power_plus"].value == 100
        assert telegram.readings["active_energy_plus"].value == 55555

    def test_a_short_telegram_fills_what_it_can(self):
        body = Node(Tag.STRUCTURE, [Node(Tag.UINT32, 100)])
        telegram = parse_telegram(_notification(body), layout="positional", obis_order=self.ORDER)
        assert set(telegram.readings) == {"active_power_plus"}


class TestWrongKey:
    def test_garbage_plaintext_is_reported_as_a_key_problem(self, sim):
        with pytest.raises(KeyMismatchError) as excinfo:
            decode(sim, key=WRONG_KEY)
        assert "key" in excinfo.value.hint.lower()

    def test_a_plaintext_that_is_not_a_data_notification_is_rejected(self):
        with pytest.raises(KeyMismatchError, match="data-notification"):
            parse_telegram(b"\xc2\x00\x00\x00\x01\x00\x00")

    def test_an_empty_plaintext_is_rejected(self):
        with pytest.raises(KeyMismatchError):
            parse_telegram(b"")

    def test_a_valid_apdu_carrying_nothing_useful_is_rejected(self):
        body = Node(Tag.STRUCTURE, [Node(Tag.UINT16, 1), Node(Tag.UINT16, 2)])
        with pytest.raises(KeyMismatchError, match="no recognised OBIS codes"):
            parse_telegram(_notification(body))


class TestTrailingBytes:
    def test_bytes_after_the_notification_body_are_ignored(self, sim):
        # A meter that appends a GCM tag even though the security control byte
        # says it does not authenticate leaves twelve stray bytes here.
        plaintext = sim.build_plaintext(MOMENT) + b"\xaa" * 12
        telegram = parse_telegram(plaintext)
        assert telegram.readings["active_power_plus"].value == 412


def _notification(body: Node, moment: datetime = MOMENT) -> bytes:
    clock = axdr.encode_datetime(moment)
    return b"\x0f\x00\x00\x00\x01" + bytes([len(clock)]) + clock + axdr.encode(body)
