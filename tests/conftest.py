from __future__ import annotations

from pathlib import Path

import pytest

from ha_smartmeter import suppliers
from ha_smartmeter.simulator import MeterSimulator

#: Test key only. Never use a real meter key in this repository.
TEST_KEY = bytes.fromhex("36C66639E48A8CA4D6BC8B282A793BBB")
TEST_KEY_HEX = TEST_KEY.hex().upper()
WRONG_KEY = bytes.fromhex("000102030405060708090A0B0C0D0E0F")

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _clear_profile_cache():
    suppliers.load_all.cache_clear()
    yield
    suppliers.load_all.cache_clear()


@pytest.fixture
def tinetz():
    return suppliers.get("tinetz")


@pytest.fixture
def generic():
    return suppliers.get("generic-ksm-west")


@pytest.fixture
def sim():
    return MeterSimulator(key=TEST_KEY)


@pytest.fixture
def single_phase_sim():
    return MeterSimulator(key=TEST_KEY, three_phase=False, meter_number="1SAG0000000001")
