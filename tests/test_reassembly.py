from __future__ import annotations

from ha_smartmeter.mbus.reassembly import Reassembler
from ha_smartmeter.models import MBusFrame

TSAP = bytes([0x01, 0x67])


def frame(ci: int, payload: bytes, tsap: bytes = TSAP) -> MBusFrame:
    return MBusFrame(c_field=0x53, a_field=0xFF, ci_field=ci, payload=tsap + payload)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_single_segment_message_comes_straight_out():
    r = Reassembler(tsap=(0x01, 0x67))
    assert r.push(frame(0x10, b"\xdb\x08hello")) == b"\xdb\x08hello"
    assert r.stats.messages == 1
    assert r.pending_bytes == 0


def test_two_segments_are_joined():
    r = Reassembler(tsap=(0x01, 0x67))
    assert r.push(frame(0x00, b"\xdb\x08first")) is None
    assert r.push(frame(0x11, b"second")) == b"\xdb\x08firstsecond"
    assert r.stats.messages == 1
    assert r.stats.segments == 2


def test_three_segments_are_joined():
    r = Reassembler(tsap=(0x01, 0x67))
    assert r.push(frame(0x00, b"\xdb\x08a")) is None
    assert r.push(frame(0x01, b"b")) is None
    assert r.push(frame(0x12, b"c")) == b"\xdb\x08abc"


def test_out_of_order_segment_discards_the_message():
    r = Reassembler(tsap=(0x01, 0x67))
    r.push(frame(0x00, b"\xdb\x08a"))
    assert r.push(frame(0x12, b"c")) is None  # segment 2 where 1 was expected
    assert r.stats.out_of_order == 1
    assert r.pending_bytes == 0
    # And the next complete message still works.
    assert r.push(frame(0x10, b"\xdb\x08fresh")) == b"\xdb\x08fresh"


def test_a_restarted_message_discards_the_partial_one():
    r = Reassembler(tsap=(0x01, 0x67))
    r.push(frame(0x00, b"\xdb\x08partial"))
    assert r.push(frame(0x10, b"\xdb\x08whole")) == b"\xdb\x08whole"
    assert r.stats.out_of_order == 1


def test_joining_mid_message_waits_for_the_next_start():
    r = Reassembler(tsap=(0x01, 0x67))
    assert r.push(frame(0x11, b"tail")) is None
    assert r.push(frame(0x10, b"\xdb\x08whole")) == b"\xdb\x08whole"


def test_a_stalled_message_is_dropped_after_the_timeout():
    clock = FakeClock()
    r = Reassembler(tsap=(0x01, 0x67), timeout=15.0, clock=clock)
    r.push(frame(0x00, b"\xdb\x08a"))
    assert r.pending_bytes == 3

    clock.now = 20.0
    r.check_timeout()
    assert r.stats.timeouts == 1
    assert r.pending_bytes == 0

    # A stuck buffer would swallow this one; it must not.
    assert r.push(frame(0x10, b"\xdb\x08fresh")) == b"\xdb\x08fresh"


def test_timeout_is_also_checked_when_a_frame_arrives():
    clock = FakeClock()
    r = Reassembler(tsap=(0x01, 0x67), timeout=15.0, clock=clock)
    r.push(frame(0x00, b"\xdb\x08a"))
    clock.now = 20.0
    assert r.push(frame(0x11, b"b")) is None
    assert r.stats.timeouts == 1


def test_frames_with_an_mbus_data_header_are_ignored():
    r = Reassembler(tsap=(0x01, 0x67))
    assert r.push(frame(0x90, b"\xdb\x08x")) is None
    assert r.stats.foreign_ci == 1


def test_the_maximum_of_sixteen_segments_is_joined():
    # Segment numbers are four bits, so sixteen is as far as a message can go.
    # At 250 bytes each that is 4000 bytes, far more than a telegram needs.
    r = Reassembler(tsap=(0x01, 0x67))
    r.push(frame(0x00, b"\xdb\x08"))
    for segment in range(1, 15):
        assert r.push(frame(segment, b"x")) is None
    assert r.push(frame(0x1F, b"last")) == b"\xdb\x08" + b"x" * 14 + b"last"


class TestTsapDetection:
    def test_detects_tsap_bytes_in_front_of_the_apdu(self):
        r = Reassembler(tsap="auto")
        assert r.push(frame(0x10, b"\xdb\x08body")) == b"\xdb\x08body"
        assert r.resolved_tsap == (0x01, 0x67)

    def test_detects_that_there_are_no_tsap_bytes(self):
        r = Reassembler(tsap="auto")
        assert r.push(frame(0x10, b"\xdb\x08body", tsap=b"")) == b"\xdb\x08body"
        assert r.resolved_tsap is None

    def test_configured_tsap_is_used_without_detection(self):
        r = Reassembler(tsap=None)
        assert r.push(frame(0x10, b"\xdb\x08body")) == TSAP + b"\xdb\x08body"

    def test_detection_happens_once_and_sticks(self):
        r = Reassembler(tsap="auto")
        r.push(frame(0x10, b"\xdb\x08one"))
        assert r.push(frame(0x10, b"\xdb\x08two")) == b"\xdb\x08two"
        assert r.resolved_tsap == (0x01, 0x67)
