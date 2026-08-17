from __future__ import annotations

import pytest

from ha_smartmeter.errors import FrameError
from ha_smartmeter.mbus.frame import build_frame, checksum, parse_frame
from ha_smartmeter.mbus.reader import FrameReader

# A minimal but complete frame: SND_UD, broadcast address, CI 0x10 (only
# segment), TSAP 01 67 and four bytes of payload.
SAMPLE = build_frame(0x53, 0xFF, 0x10, bytes([0x01, 0x67, 0xDB, 0x08, 0xAA, 0xBB]))


def test_frame_layout():
    assert SAMPLE[0] == 0x68
    assert SAMPLE[1] == SAMPLE[2] == 9  # C, A, CI plus six payload bytes
    assert SAMPLE[3] == 0x68
    assert SAMPLE[-1] == 0x16
    assert SAMPLE[-2] == checksum(SAMPLE[4:-2])


def test_parse_round_trip():
    frame = parse_frame(SAMPLE)
    assert frame.c_field == 0x53
    assert frame.a_field == 0xFF
    assert frame.ci_field == 0x10
    assert frame.payload == bytes([0x01, 0x67, 0xDB, 0x08, 0xAA, 0xBB])
    assert frame.is_final
    assert frame.segment_number == 0
    assert not frame.has_mbus_data_header


def test_checksum_ignores_carries():
    assert checksum(b"\xff\xff\x02") == 0x00


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda f: f[:-2] + bytes([(f[-2] + 1) & 0xFF]) + f[-1:], "checksum"),
        (lambda f: f[:1] + bytes([f[1] + 1]) + f[2:], "L field bytes differ"),
        (lambda f: f[:-1] + b"\x00", "stop byte"),
        (lambda f: b"\x69" + f[1:], "start byte"),
        (lambda f: f[:-1], "bytes but"),
        (lambda f: f[:3], "too short"),
    ],
)
def test_damaged_frames_are_rejected(mutate, message):
    with pytest.raises(FrameError, match=message):
        parse_frame(mutate(SAMPLE))


def test_oversized_payload_is_refused():
    with pytest.raises(ValueError, match="exceeds"):
        build_frame(0x53, 0xFF, 0x10, b"\x00" * 253)


class TestFrameReader:
    def test_reads_a_whole_frame(self):
        reader = FrameReader()
        assert [f.payload for f in reader.feed(SAMPLE)] == [parse_frame(SAMPLE).payload]
        assert reader.stats.frames == 1
        assert reader.stats.discarded == 0

    def test_reads_frames_split_across_arbitrary_chunks(self):
        # A serial port hands over bytes whenever it feels like it, so the
        # reader has to cope with a frame arriving one byte at a time.
        reader = FrameReader()
        frames = []
        for index in range(len(SAMPLE)):
            frames += reader.feed(SAMPLE[index : index + 1])
        assert len(frames) == 1
        assert frames[0].ci_field == 0x10

    def test_reads_two_frames_from_one_chunk(self):
        reader = FrameReader()
        assert len(reader.feed(SAMPLE + SAMPLE)) == 2

    def test_skips_noise_before_a_frame(self):
        reader = FrameReader()
        noise = b"\x00\xff\x68\x01"  # includes a false start byte
        frames = reader.feed(noise + SAMPLE)
        assert len(frames) == 1
        assert reader.stats.discarded == len(noise)

    def test_recovers_from_a_corrupted_frame(self):
        reader = FrameReader()
        broken = SAMPLE[:-2] + b"\x00" + SAMPLE[-1:]
        frames = reader.feed(broken + SAMPLE)
        assert len(frames) == 1
        assert reader.stats.checksum_errors == 1

    def test_a_false_start_byte_inside_noise_does_not_eat_the_next_frame(self):
        reader = FrameReader()
        # 68 09 09 68 looks like a header but the checksum will not match, and
        # the real frame starts three bytes later.
        assert len(reader.feed(b"\x68\x09\x09\x68" + SAMPLE)) == 1

    def test_buffer_does_not_grow_without_bound(self):
        reader = FrameReader()
        reader.feed(b"\x68" * 4000)
        assert reader.pending <= 2 * (6 + 0xFF)

    def test_reset_drops_a_partial_frame(self):
        reader = FrameReader()
        reader.feed(SAMPLE[:5])
        reader.reset()
        assert reader.feed(SAMPLE) != []
