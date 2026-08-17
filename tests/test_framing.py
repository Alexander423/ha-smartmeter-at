"""The three link layers Austria uses for the same DLMS payload."""

from __future__ import annotations

import pytest

from conftest import TEST_KEY
from ha_smartmeter.decoder import Decoder
from ha_smartmeter.errors import ProfileError
from ha_smartmeter.framing import DLMS_INTERFACES, FOREIGN_INTERFACES, build_framer
from ha_smartmeter.framing.hdlc_framer import HdlcFramer, crc16_x25
from ha_smartmeter.framing.mbus_framer import MBusFramer
from ha_smartmeter.framing.raw_framer import RawApduFramer
from ha_smartmeter.simulator import MeterSimulator, build_hdlc_frame
from ha_smartmeter.suppliers import get as get_profile

#: A separate authentication key, as the P1 operators issue. Test value only.
GAK = bytes.fromhex("0F0E0D0C0B0A09080706050403020100")


def sim_for(interface: str, **kwargs) -> MeterSimulator:
    security = 0x30 if interface == "p1" else 0x21
    return MeterSimulator(key=TEST_KEY, interface=interface, security_control=security, **kwargs)


class TestFramerSelection:
    @pytest.mark.parametrize(
        "profile_id, expected",
        [
            ("tinetz", MBusFramer),
            ("netz-burgenland", MBusFramer),
            ("energienetze-steiermark", RawApduFramer),
            ("kaernten-netz", RawApduFramer),
            ("wiener-netze", HdlcFramer),
        ],
    )
    def test_the_operator_decides_the_link_layer(self, profile_id, expected):
        assert isinstance(build_framer(get_profile(profile_id)), expected)

    @pytest.mark.parametrize("profile_id", ["netz-ooe", "linz-netz", "energie-klagenfurt"])
    def test_an_unreadable_interface_explains_itself(self, profile_id):
        with pytest.raises(ProfileError) as excinfo:
            build_framer(get_profile(profile_id))
        # Not "unsupported": a sentence saying what the meter speaks and what
        # would be needed instead.
        assert len(excinfo.value.hint) > 80

    def test_every_interface_is_either_handled_or_explained(self):
        for profile in [get_profile(p) for p in _all_profile_ids()]:
            assert profile.interface in DLMS_INTERFACES + FOREIGN_INTERFACES
            if profile.interface in FOREIGN_INTERFACES:
                assert profile.unsupported_hint()
                assert not profile.supported


class TestRawApduFramer:
    """DSMR P1: no link layer, the APDU delimits itself."""

    def test_one_apdu_in_one_read(self):
        framer = RawApduFramer()
        apdu = sim_for("p1").build_apdu()
        assert framer.feed(apdu) == [apdu]
        assert framer.stats.messages == 1

    def test_bytes_arriving_one_at_a_time(self):
        framer = RawApduFramer()
        apdu = sim_for("p1").build_apdu()
        out = []
        for index in range(len(apdu)):
            out += framer.feed(apdu[index : index + 1])
        assert out == [apdu]

    def test_two_apdus_back_to_back(self):
        sim = sim_for("p1")
        first, second = sim.build_apdu(), sim.build_apdu()
        assert RawApduFramer().feed(first + second) == [first, second]

    def test_noise_before_an_apdu_is_skipped(self):
        framer = RawApduFramer()
        apdu = sim_for("p1").build_apdu()
        assert framer.feed(b"\x00\x01\xdb\x02" + apdu) == [apdu]
        assert framer.stats.discarded == 4

    def test_a_short_length_field_is_rejected(self):
        framer = RawApduFramer()
        assert framer.feed(b"\xdb\x08" + bytes(8) + b"\x02" + bytes(2)) == []

    def test_a_wrong_system_title_length_is_skipped(self):
        framer = RawApduFramer()
        apdu = sim_for("p1").build_apdu()
        assert framer.feed(b"\xdb\x04" + apdu) == [apdu]

    def test_an_ascii_dsmr_telegram_is_reported(self, caplog):
        import logging

        framer = RawApduFramer()
        with caplog.at_level(logging.ERROR):
            framer.feed(b"/ISK5\\2M550T-1012\r\n\r\n1-0:1.8.0(000123.456*kWh)\r\n!\r\n")
        assert "readable DSMR text telegrams" in caplog.text

    def test_the_buffer_does_not_grow_without_bound(self):
        framer = RawApduFramer()
        framer.feed(b"\xdb" * 20000)
        assert framer.pending <= 2 * 4096


class TestHdlcFramer:
    def test_crc_matches_the_x25_check_value(self):
        # The standard check value for CRC-16/X.25 over "123456789".
        assert crc16_x25(b"123456789") == 0x906E

    def test_one_frame_one_message(self):
        framer = HdlcFramer()
        info = b"\xe6\xe7\x00" + b"payload"
        assert framer.feed(build_hdlc_frame(info)) == [b"payload"]
        assert framer.stats.frames == 1

    def test_segments_are_joined_and_the_llc_header_stripped(self):
        framer = HdlcFramer()
        data = build_hdlc_frame(b"\xe6\xe7\x00" + b"first", segmented=True) + build_hdlc_frame(
            b"second"
        )
        assert framer.feed(data) == [b"firstsecond"]

    def test_adjacent_frames_may_share_a_flag_byte(self):
        framer = HdlcFramer()
        one = build_hdlc_frame(b"\xe6\xe7\x00a")
        two = build_hdlc_frame(b"\xe6\xe7\x00b")
        assert framer.feed(one + two[1:]) == [b"a", b"b"]

    def test_a_corrupted_frame_is_dropped_and_the_next_still_reads(self):
        framer = HdlcFramer()
        good = build_hdlc_frame(b"\xe6\xe7\x00ok")
        broken = bytearray(build_hdlc_frame(b"\xe6\xe7\x00bad"))
        broken[8] ^= 0xFF
        assert framer.feed(bytes(broken) + good) == [b"ok"]
        assert framer.stats.checksum_errors >= 1

    def test_bytes_arriving_one_at_a_time(self):
        framer = HdlcFramer()
        data = build_hdlc_frame(b"\xe6\xe7\x00hello")
        out = []
        for index in range(len(data)):
            out += framer.feed(data[index : index + 1])
        assert out == [b"hello"]

    def test_idle_flags_between_frames_are_ignored(self):
        framer = HdlcFramer()
        frame = build_hdlc_frame(b"\xe6\xe7\x00x")
        assert framer.feed(b"\x7e\x7e\x7e" + frame) == [b"x"]

    def test_a_stalled_segment_is_dropped_after_the_timeout(self):
        clock = _FakeClock()
        framer = HdlcFramer(timeout=15.0, clock=clock)
        framer.feed(build_hdlc_frame(b"\xe6\xe7\x00half", segmented=True))
        clock.now = 20.0
        framer.check_timeout()
        assert framer.stats.timeouts == 1
        assert framer.feed(build_hdlc_frame(b"\xe6\xe7\x00whole")) == [b"whole"]


class TestEndToEndPerInterface:
    """Every interface has to reach the same fifteen values."""

    @pytest.mark.parametrize(
        "profile_id, interface",
        [
            ("tinetz", "mbus"),
            ("energienetze-steiermark", "p1"),
            ("wiener-netze", "hdlc"),
        ],
    )
    def test_a_telegram_decodes_whatever_it_is_wrapped_in(self, profile_id, interface):
        profile = get_profile(profile_id)
        sim = sim_for(interface)
        decoder = Decoder(profile=profile, key=TEST_KEY, auth_key=TEST_KEY)
        telegrams = decoder.feed(sim.next_telegram())
        assert len(telegrams) == 1
        assert len(telegrams[0].readings) == 15
        assert telegrams[0].readings["active_power_plus"].value == 412

    def test_the_p1_tag_is_actually_verified(self):
        # Security control 0x30 puts a GCM tag on the wire, and with the second
        # key configured it is checked rather than assumed.
        sim = sim_for("p1")
        decoder = Decoder(
            profile=get_profile("energienetze-steiermark"), key=TEST_KEY, auth_key=TEST_KEY
        )
        assert decoder.feed(sim.next_telegram())[0].authenticated is True

    def test_a_missing_second_key_still_decodes_but_says_so(self, caplog):
        import logging

        # A real P1 meter has a GAK that differs from the GUEK, so leaving the
        # second key out means the tag cannot be checked at all.
        sim = sim_for("p1", auth_key=GAK)
        decoder = Decoder(profile=get_profile("energienetze-steiermark"), key=TEST_KEY)
        with caplog.at_level(logging.WARNING):
            telegram = decoder.feed(sim.next_telegram())[0]
        assert telegram.authenticated is False
        assert "GAK" in caplog.text

    def test_the_values_are_the_same_with_or_without_the_second_key(self):
        sim = sim_for("p1", auth_key=GAK)
        telegram = sim.build_plaintext()
        profile = get_profile("energienetze-steiermark")
        without = Decoder(profile=profile, key=TEST_KEY).decode_message(sim.build_apdu(telegram))
        with_gak = Decoder(profile=profile, key=TEST_KEY, auth_key=GAK).decode_message(
            sim.build_apdu(telegram)
        )
        assert without.values() == with_gak.values()
        assert (without.authenticated, with_gak.authenticated) == (False, True)

    def test_a_wrong_second_key_is_reported_rather_than_ignored(self, caplog):
        import logging

        sim = sim_for("p1", auth_key=GAK)
        decoder = Decoder(
            profile=get_profile("energienetze-steiermark"), key=TEST_KEY, auth_key=bytes(16)
        )
        with caplog.at_level(logging.ERROR):
            assert decoder.feed(sim.next_telegram()) == []
        assert "authentication key" in caplog.text

    def test_a_wrong_p1_key_is_reported_as_a_key_problem(self, caplog):
        import logging

        sim = sim_for("p1")
        decoder = Decoder(profile=get_profile("energienetze-steiermark"), key=bytes(16))
        with caplog.at_level(logging.ERROR):
            assert decoder.feed(sim.next_telegram()) == []
        assert "key" in caplog.text.lower()


class TestFixtures:
    """One committed fixture per interface, so the wire formats stay honest."""

    @pytest.mark.parametrize(
        "name, profile_id, frames",
        [
            ("sim-p1.hex", "generic-p1", 1),
            ("sim-hdlc.hex", "generic-ir", 3),
        ],
    )
    def test_fixture_decodes(self, name, profile_id, frames):
        from conftest import FIXTURES
        from ha_smartmeter.transport.replay_source import parse_hex_file

        data = parse_hex_file((FIXTURES / name).read_text(encoding="utf-8"))
        assert len(data) == frames
        decoder = Decoder(profile=get_profile(profile_id), key=TEST_KEY, auth_key=TEST_KEY)
        telegrams = decoder.feed(b"".join(data))
        assert len(telegrams) == 1
        assert len(telegrams[0].readings) == 15
        assert telegrams[0].readings["voltage_l1"].value == pytest.approx(231.4)

    @pytest.mark.parametrize(
        "name, expected",
        [
            ("sim-three-phase.hex", 2),  # two M-Bus segments
            ("sim-p1.hex", 1),  # one self-contained APDU
            ("sim-hdlc.hex", 3),  # three HDLC segments
        ],
    )
    def test_replay_groups_each_interface_into_telegrams(self, name, expected):
        from conftest import FIXTURES
        from ha_smartmeter.transport.replay_source import group_telegrams, parse_hex_file

        # A capture file does not say which interface it came from, so the
        # grouping has to work it out from the frames themselves.
        frames = parse_hex_file((FIXTURES / name).read_text(encoding="utf-8"))
        groups = group_telegrams(frames)
        assert len(groups) == 1
        assert len(groups[0]) == expected


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _all_profile_ids() -> list[str]:
    from ha_smartmeter import suppliers

    return sorted(suppliers.load_all())
