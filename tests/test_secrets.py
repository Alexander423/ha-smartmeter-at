"""The key is secret. These tests are the enforcement, not the intention."""

from __future__ import annotations

import logging

from conftest import TEST_KEY_HEX
from ha_smartmeter.capture import FrameCapture
from ha_smartmeter.logging_setup import REDACTED, configure, redact
from ha_smartmeter.mbus.frame import build_frame, parse_frame
from ha_smartmeter.status import Status

SECRETS = (TEST_KEY_HEX,)


class TestLogRedaction:
    def test_the_key_never_reaches_a_log_line(self, caplog):
        configure("debug", secrets=SECRETS)
        handler = logging.getLogger().handlers[0]
        record = logging.LogRecord(
            "t", logging.INFO, __file__, 1, "using key %s", (TEST_KEY_HEX,), None
        )
        assert TEST_KEY_HEX not in handler.format(record)
        assert REDACTED in handler.format(record)

    def test_lower_case_is_redacted_too(self):
        assert TEST_KEY_HEX.lower() not in redact(f"key={TEST_KEY_HEX.lower()}", SECRETS)

    def test_the_key_is_redacted_from_an_exception_message(self):
        configure("debug", secrets=SECRETS)
        handler = logging.getLogger().handlers[0]
        try:
            raise ValueError(f"bad key {TEST_KEY_HEX}")
        except ValueError:
            record = logging.LogRecord("t", logging.ERROR, __file__, 1, "failed", (), None)
            record.exc_info = __import__("sys").exc_info()
            assert TEST_KEY_HEX not in handler.format(record)

    def test_short_strings_are_left_alone(self):
        # Redacting a two character "key" would mangle ordinary sentences.
        assert redact("the meter is on", ("me",)) == "the meter is on"


class TestCaptureRedaction:
    def test_a_capture_file_contains_no_key(self, tmp_path):
        capture = FrameCapture(tmp_path, secrets=SECRETS, header=f"key: {TEST_KEY_HEX}")
        capture.write(parse_frame(build_frame(0x53, 0xFF, 0x10, b"\x01\x67\xdb\x08")))
        capture.close()
        text = capture.path.read_text(encoding="utf-8")
        assert TEST_KEY_HEX not in text
        assert REDACTED in text

    def test_the_capture_is_a_replayable_hex_file(self, tmp_path):
        from ha_smartmeter.transport.replay_source import parse_hex_file

        frame = build_frame(0x53, 0xFF, 0x10, b"\x01\x67\xdb\x08")
        capture = FrameCapture(tmp_path, secrets=SECRETS)
        capture.write(parse_frame(frame))
        capture.close()
        assert parse_hex_file(capture.path.read_text(encoding="utf-8")) == [frame]

    def test_capture_stops_on_its_own(self, tmp_path, caplog):
        frame = parse_frame(build_frame(0x53, 0xFF, 0x10, b"\x01\x67\xdb\x08"))
        capture = FrameCapture(tmp_path, max_frames=3)
        with caplog.at_level(logging.INFO):
            for _ in range(10):
                capture.write(frame)
        assert capture.frames_written == 3
        assert capture.finished
        assert "stopped after 3 frames" in capture.path.read_text(encoding="utf-8")

    def test_writing_after_the_limit_does_not_raise(self, tmp_path):
        capture = FrameCapture(tmp_path, max_frames=1)
        frame = parse_frame(build_frame(0x53, 0xFF, 0x10, b"\x01\x67\xdb\x08"))
        capture.write(frame)
        capture.write(frame)
        capture.write(frame)  # would be a closed-file write if the guard were missing

    def test_close_is_idempotent(self, tmp_path):
        capture = FrameCapture(tmp_path)
        capture.close()
        capture.close()


class TestStatusSnapshot:
    def test_the_snapshot_carries_no_configuration_secrets(self):
        status = Status(profile_name="TINETZ", source="/dev/ttyUSB0")
        status.note_frame(bytes.fromhex("6803036853ff106216"))
        snapshot = status.snapshot()
        assert TEST_KEY_HEX not in str(snapshot)
        assert snapshot["recent_frames"] == ["6803036853ff106216"]

    def test_the_snapshot_is_json_serialisable(self):
        import json

        status = Status(values={"active_power_plus": 412})
        assert json.loads(json.dumps(status.snapshot()))["values"]["active_power_plus"] == 412

    def test_only_the_last_few_frames_are_kept(self):
        status = Status()
        for index in range(20):
            status.note_frame(bytes([index]))
        assert len(status.snapshot()["recent_frames"]) == 6
