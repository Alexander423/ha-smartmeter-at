from __future__ import annotations

import logging

import pytest

from conftest import FIXTURES, TEST_KEY, WRONG_KEY
from ha_smartmeter.decoder import FAILURES_BEFORE_ALARM, Decoder
from ha_smartmeter.simulator import MeterSimulator
from ha_smartmeter.transport.replay_source import parse_hex_file


class TestEndToEnd:
    def test_a_telegram_goes_from_bytes_to_values(self, tinetz, sim):
        decoder = Decoder(profile=tinetz, key=TEST_KEY)
        telegrams = decoder.feed(sim.next_telegram())
        assert len(telegrams) == 1
        telegram = telegrams[0]
        assert telegram.readings["active_power_plus"].value == 412
        assert telegram.meter_number == "1SAG1234567890"
        assert telegram.frame_counter == 1
        assert telegram.system_title == sim.system_title
        assert decoder.stats.telegrams == 1
        assert decoder.stats.decode_failures == 0

    def test_a_three_phase_telegram_really_is_segmented(self, sim):
        # If this stops being true the multi-segment path is no longer covered
        # by the main fixture and the test below is worth less.
        assert len(sim.build_frames()) > 1

    def test_bytes_arriving_one_at_a_time_still_decode(self, tinetz, sim):
        decoder = Decoder(profile=tinetz, key=TEST_KEY)
        data = sim.next_telegram()
        telegrams = []
        for index in range(len(data)):
            telegrams += decoder.feed(data[index : index + 1])
        assert len(telegrams) == 1

    def test_consecutive_telegrams_each_decode(self, tinetz, sim):
        decoder = Decoder(profile=tinetz, key=TEST_KEY)
        for expected_counter in (1, 2, 3):
            telegrams = decoder.feed(sim.next_telegram())
            assert len(telegrams) == 1
            assert telegrams[0].frame_counter == expected_counter
        assert decoder.stats.telegrams == 3

    def test_line_noise_between_telegrams_is_survived(self, tinetz, sim):
        decoder = Decoder(profile=tinetz, key=TEST_KEY)
        decoder.feed(b"\x00\xff\x68\x55" + sim.next_telegram())
        decoder.feed(b"\xaa" * 40 + sim.next_telegram())
        assert decoder.stats.telegrams == 2

    def test_a_lost_frame_costs_one_telegram_and_no_more(self, tinetz, sim):
        decoder = Decoder(profile=tinetz, key=TEST_KEY)
        frames = sim.build_frames()
        assert len(frames) >= 2
        decoder.feed(frames[0])  # the rest of this telegram never arrives
        sim.frame_counter += 1
        assert len(decoder.feed(sim.next_telegram())) == 1

    def test_a_single_phase_meter_produces_fewer_readings(self, tinetz, single_phase_sim):
        decoder = Decoder(profile=tinetz, key=TEST_KEY)
        telegram = decoder.feed(single_phase_sim.next_telegram())[0]
        assert "voltage_l2" not in telegram.readings
        assert len(telegram.readings) == 11

    def test_captured_frames_are_offered_to_the_capture_hook(self, tinetz, sim):
        seen = []
        decoder = Decoder(profile=tinetz, key=TEST_KEY, on_frame=seen.append)
        decoder.feed(sim.next_telegram())
        assert len(seen) == len(sim.build_frames())
        # What the hook gets has to be the bytes as they arrived, because that
        # is what a capture file is for.
        assert all(raw.startswith(b"\x68") and raw.endswith(b"\x16") for raw in seen)


class TestWrongKey:
    def test_the_user_is_told_the_key_is_wrong(self, tinetz, sim, caplog):
        decoder = Decoder(profile=tinetz, key=WRONG_KEY)
        with caplog.at_level(logging.ERROR):
            assert decoder.feed(sim.next_telegram()) == []
        assert decoder.stats.decode_failures == 1
        assert "key" in caplog.text.lower()
        assert "key" in decoder.stats.last_error_hint.lower()

    def test_the_key_is_never_written_to_the_log(self, tinetz, sim, caplog):
        decoder = Decoder(profile=tinetz, key=WRONG_KEY)
        with caplog.at_level(logging.DEBUG):
            decoder.feed(sim.next_telegram())
        assert WRONG_KEY.hex() not in caplog.text.lower()

    def test_repeated_failures_do_not_multiply_the_error_log(self, tinetz, sim, caplog):
        decoder = Decoder(profile=tinetz, key=WRONG_KEY)
        with caplog.at_level(logging.ERROR):
            for _ in range(10):
                decoder.feed(sim.next_telegram())
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert 1 <= len(errors) <= FAILURES_BEFORE_ALARM


class TestProfileDetection:
    def test_the_generic_profile_reads_a_tinetz_telegram(self, generic, sim):
        decoder = Decoder(profile=generic, key=TEST_KEY)
        assert len(decoder.feed(sim.next_telegram())) == 1
        assert decoder.framer.resolved_tsap == (0x01, 0x67)

    def test_the_generic_profile_reads_a_meter_without_tsap_bytes(self, generic):
        sim = MeterSimulator(key=TEST_KEY, tsap=None, three_phase=False)
        decoder = Decoder(profile=generic, key=TEST_KEY)
        assert len(decoder.feed(sim.next_telegram())) == 1
        assert decoder.framer.resolved_tsap is None


class TestImplausibleValues:
    def test_a_voltage_that_is_ten_times_too_large_is_flagged(self, tinetz, caplog):
        sim = MeterSimulator(key=TEST_KEY, values={"voltage_l1": 2314.0})
        decoder = Decoder(profile=tinetz, key=TEST_KEY)
        with caplog.at_level(logging.WARNING):
            decoder.feed(sim.next_telegram())
        assert "plausible range" in caplog.text

    def test_the_warning_is_not_repeated_every_five_seconds(self, tinetz, caplog):
        sim = MeterSimulator(key=TEST_KEY, values={"voltage_l1": 2314.0})
        decoder = Decoder(profile=tinetz, key=TEST_KEY)
        with caplog.at_level(logging.WARNING):
            for _ in range(5):
                decoder.feed(sim.next_telegram())
        assert caplog.text.count("plausible range") == 1


class TestFixtures:
    """The committed fixtures are the regression net for the frame layout."""

    @pytest.mark.parametrize(
        "name, frames, readings",
        [
            ("sim-three-phase.hex", 2, 15),
            ("sim-single-phase.hex", 1, 11),
            ("sim-many-segments.hex", 4, 15),
            ("sim-no-tsap.hex", 1, 11),
        ],
    )
    def test_fixture_decodes(self, generic, name, frames, readings):
        data = parse_hex_file((FIXTURES / name).read_text(encoding="utf-8"))
        assert len(data) == frames
        decoder = Decoder(profile=generic, key=TEST_KEY)
        telegrams = decoder.feed(b"".join(data))
        assert len(telegrams) == 1
        assert len(telegrams[0].readings) == readings

    def test_the_three_phase_fixture_has_the_expected_values(self, tinetz):
        data = parse_hex_file((FIXTURES / "sim-three-phase.hex").read_text(encoding="utf-8"))
        decoder = Decoder(profile=tinetz, key=TEST_KEY)
        telegram = decoder.feed(b"".join(data))[0]
        assert telegram.frame_counter == 4711
        assert telegram.readings["voltage_l1"].value == pytest.approx(231.4)
        assert telegram.readings["active_energy_plus"].value == 1234567
        assert telegram.timestamp is not None
        assert telegram.timestamp.year == 2026
