from __future__ import annotations

import asyncio
import logging
import time

import pytest

from conftest import TEST_KEY, TEST_KEY_HEX
from ha_smartmeter import runner as runner_module
from ha_smartmeter.config import MqttSettings, Options
from ha_smartmeter.errors import SerialUnavailableError
from ha_smartmeter.runner import Runner
from ha_smartmeter.simulator import MeterSimulator
from ha_smartmeter.status import Status
from ha_smartmeter.transport import Source

SETTINGS = MqttSettings(host="core-mosquitto")


class FakePublisher:
    def __init__(self, *args, **kwargs):
        self.telegrams = []
        self.availability = []
        self.started = False
        self.stopped = False
        self.connected = True
        self.published = 0
        self.skipped = 0

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def publish_telegram(self, telegram):
        self.telegrams.append(telegram)
        self.published += 1
        return True

    def set_available(self, available):
        if not self.availability or self.availability[-1] != available:
            self.availability.append(available)

    @property
    def entity_count(self):
        return 0


class ScriptedSource(Source):
    """Hands out prepared chunks, then does whatever the test asked for."""

    def __init__(self, chunks: list[bytes], after=None, fail_at_open: int = 0) -> None:
        self._chunks = list(chunks)
        self._after = after
        self._fail_at_open = fail_at_open
        self.opens = 0
        self.closed = 0

    @property
    def description(self) -> str:
        return "scripted source"

    async def open(self) -> None:
        self.opens += 1
        if self.opens <= self._fail_at_open:
            raise SerialUnavailableError("adapter not plugged in")

    async def read(self) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        if self._after is not None:
            self._after()
        await asyncio.sleep(0.005)
        return b""

    async def close(self) -> None:
        self.closed += 1


@pytest.fixture(autouse=True)
def _fake_publisher(monkeypatch):
    holder = {}

    def factory(*args, **kwargs):
        holder["publisher"] = FakePublisher()
        return holder["publisher"]

    monkeypatch.setattr(runner_module, "MqttPublisher", factory)
    return holder


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    monkeypatch.setattr(runner_module, "RETRY_MIN", 0.01)
    monkeypatch.setattr(runner_module, "RETRY_MAX", 0.02)
    monkeypatch.setattr(runner_module, "HOUSEKEEPING_INTERVAL", 0.01)


def make_options(**overrides) -> Options:
    return Options.from_mapping(
        {"supplier": "tinetz", "port": "/dev/ttyUSB0", "key": TEST_KEY_HEX, **overrides}
    )


def run(options: Options, source: ScriptedSource, status: Status | None = None) -> Runner:
    status = status or Status()
    runner = Runner(options, SETTINGS, status=status, source_factory=lambda _: source)
    source._after = source._after or runner.request_stop
    asyncio.run(asyncio.wait_for(runner.run(), timeout=5))
    return runner


class TestReadLoop:
    def test_telegrams_reach_the_publisher(self, _fake_publisher):
        sim = MeterSimulator(key=TEST_KEY)
        source = ScriptedSource([sim.next_telegram(), sim.next_telegram()])
        runner = run(make_options(), source)
        assert len(_fake_publisher["publisher"].telegrams) == 2
        assert runner.status.telegrams == 2
        assert runner.status.state == "stopped"

    def test_the_status_reflects_the_last_telegram(self):
        sim = MeterSimulator(key=TEST_KEY)
        status = Status()
        run(make_options(), ScriptedSource([sim.next_telegram()]), status)
        assert status.meter_number == "1SAG1234567890"
        assert status.frame_counter == 1
        assert status.values["active_power_plus"] == 412
        assert status.last_telegram_at is not None
        assert status.frames == 2  # a three-phase telegram is two frames

    def test_the_source_is_closed_on_the_way_out(self):
        source = ScriptedSource([])
        run(make_options(), source)
        assert source.closed == 1

    def test_a_wrong_key_does_not_stop_the_loop(self, _fake_publisher, caplog):
        sim = MeterSimulator(key=bytes(16))
        source = ScriptedSource([sim.next_telegram(), sim.next_telegram()])
        with caplog.at_level(logging.ERROR):
            runner = run(make_options(), source)
        assert runner.status.decode_failures == 2
        assert _fake_publisher["publisher"].telegrams == []
        assert "key" in caplog.text.lower()


class TestRecovery:
    def test_a_missing_adapter_is_retried_until_it_appears(self, caplog):
        sim = MeterSimulator(key=TEST_KEY)
        source = ScriptedSource([sim.next_telegram()], fail_at_open=3)
        with caplog.at_level(logging.ERROR):
            runner = run(make_options(), source)
        assert source.opens == 4
        assert runner.status.telegrams == 1
        assert "M-Bus adapter" in caplog.text

    def test_the_add_on_does_not_exit_when_the_adapter_is_unplugged(self):
        sim = MeterSimulator(key=TEST_KEY)

        class Unplugged(ScriptedSource):
            def __init__(self):
                super().__init__([sim.next_telegram()])
                self._failed = False

            async def read(self):
                if self._chunks:
                    return self._chunks.pop(0)
                if not self._failed:
                    self._failed = True
                    raise SerialUnavailableError("adapter vanished")
                self._after()
                await asyncio.sleep(0.005)
                return b""

        source = Unplugged()
        runner = run(make_options(), source)
        assert source.opens == 2  # reopened after the failure
        assert runner.status.telegrams == 1


class TestStaleness:
    def test_entities_go_unavailable_when_the_meter_goes_quiet(self, _fake_publisher):
        options = make_options(stale_after=1)
        runner = Runner(options, SETTINGS, source_factory=lambda _: ScriptedSource([]))
        runner.status.last_telegram_at = time.time() - 10
        runner.status.state = "reading"
        runner._housekeeping()
        assert _fake_publisher["publisher"].availability[-1] is False
        assert runner.status.state == "no telegrams"

    def test_a_fresh_telegram_keeps_entities_available(self, _fake_publisher):
        runner = Runner(make_options(), SETTINGS, source_factory=lambda _: ScriptedSource([]))
        runner.status.last_telegram_at = time.time()
        runner._housekeeping()
        assert _fake_publisher["publisher"].availability[-1] is True


class TestCapture:
    def test_capture_mode_writes_the_frames_it_saw(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runner_module.Options, "output_dir", lambda self: tmp_path)
        sim = MeterSimulator(key=TEST_KEY)
        runner = run(make_options(capture_raw=True), ScriptedSource([sim.next_telegram()]))
        written = list((tmp_path / "captures").glob("*.hex"))
        assert len(written) == 1
        assert runner.status.capture_frames == 2
        assert TEST_KEY_HEX not in written[0].read_text(encoding="utf-8")

    def test_capture_is_off_by_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runner_module.Options, "output_dir", lambda self: tmp_path)
        run(make_options(), ScriptedSource([]))
        assert not (tmp_path / "captures").exists()


class TestStartupLogging:
    def test_an_unverified_profile_says_so(self, caplog):
        with caplog.at_level(logging.WARNING):
            run(make_options(supplier="ikb"), ScriptedSource([]))
        assert "not been confirmed against a physical meter" in caplog.text

    def test_replay_mode_says_the_values_are_not_from_your_meter(self, caplog, tmp_path):
        path = tmp_path / "replay.hex"
        path.write_text("6803036853ff106216\n", encoding="utf-8")
        options = make_options(replay_file=str(path))
        with caplog.at_level(logging.WARNING):
            run(options, ScriptedSource([]))
        assert "not from your" in caplog.text

    def test_the_throttle_setting_is_explained(self, caplog):
        with caplog.at_level(logging.INFO):
            run(make_options(min_publish_interval=60), ScriptedSource([]))
        assert "at most one update every 60 s" in caplog.text
