from __future__ import annotations

import pytest

from conftest import FIXTURES, TEST_KEY
from ha_smartmeter.decoder import Decoder
from ha_smartmeter.errors import ConfigError
from ha_smartmeter.transport.replay_source import ReplaySource, group_telegrams, parse_hex_file


class TestHexFile:
    def test_comments_and_blank_lines_are_ignored(self):
        text = "# where this came from\n\n68 03 03 68 53 ff 10 62 16\n"
        assert parse_hex_file(text) == [bytes.fromhex("6803036853ff106216")]

    def test_a_trailing_comment_is_ignored(self):
        assert parse_hex_file("aabb # note\n") == [b"\xaa\xbb"]

    def test_a_bad_line_names_its_line_number(self):
        with pytest.raises(ConfigError, match="line 2"):
            parse_hex_file("aabb\nnot hex at all\n")


class TestGrouping:
    def test_frames_are_grouped_by_the_fin_bit(self):
        first = bytes.fromhex("68030368") + bytes([0x53, 0xFF, 0x00]) + b"\x00\x16"
        last = bytes.fromhex("68030368") + bytes([0x53, 0xFF, 0x11]) + b"\x00\x16"
        assert group_telegrams([first, last, last]) == [[first, last], [last]]


class TestReplaySource:
    async def test_replays_a_fixture_and_loops(self, generic):
        source = ReplaySource(FIXTURES / "sim-three-phase.hex", interval=0)
        await source.open()
        decoder = Decoder(profile=generic, key=TEST_KEY)
        for _ in range(3):
            assert len(decoder.feed(await source.read())) == 1
        assert decoder.stats.telegrams == 3
        await source.close()

    async def test_each_read_returns_one_whole_telegram(self, generic):
        source = ReplaySource(FIXTURES / "sim-many-segments.hex", interval=0)
        await source.open()
        data = await source.read()
        assert len(parse_hex_file(data.hex())) == 1  # one contiguous chunk
        assert len(Decoder(profile=generic, key=TEST_KEY).feed(data)) == 1

    async def test_a_missing_file_says_what_to_do(self):
        source = ReplaySource(FIXTURES / "does-not-exist.hex")
        with pytest.raises(ConfigError) as excinfo:
            await source.open()
        assert "replay" in excinfo.value.hint.lower()

    async def test_an_empty_file_is_rejected(self, tmp_path):
        path = tmp_path / "empty.hex"
        path.write_text("# nothing but a comment\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="no frames"):
            await ReplaySource(path).open()

    def test_the_description_is_readable(self):
        assert "replay of" in ReplaySource("x.hex", interval=5).description
