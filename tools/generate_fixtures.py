"""Regenerates the synthetic test fixtures in tests/fixtures.

Run it after changing the simulator or the frame layout:

    python tools/generate_fixtures.py

The output is deterministic: fixed key, fixed system title, fixed timestamp and
fixed frame counter. Captures from real meters are added by hand and are never
overwritten by this script, which only touches files whose names start with
"sim-".
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "smartmeter" / "app"))

from ha_smartmeter.simulator import MeterSimulator, hex_lines  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"

TEST_KEY = bytes.fromhex("36C66639E48A8CA4D6BC8B282A793BBB")
MOMENT = datetime(2026, 8, 17, 14, 30, 15, tzinfo=timezone(timedelta(hours=2)))


def write(name: str, header: str, sim: MeterSimulator) -> None:
    frames = sim.build_frames(sim.build_plaintext(MOMENT))
    path = FIXTURES / name
    path.write_text(header.strip() + "\n#\n" + hex_lines(frames), encoding="utf-8")
    print(f"{name}: {len(frames)} frame(s), {sum(len(f) for f in frames)} bytes")


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)

    write(
        "sim-three-phase.hex",
        """
# Synthetic. Produced by tools/generate_fixtures.py, not captured from a meter.
#
# A three-phase telegram carrying all fifteen documented values, encrypted with
# the test key 36C66639E48A8CA4D6BC8B282A793BBB. It exceeds the 250 byte limit
# of a single M-Bus frame, so it is segmented: CI 0x00 then CI 0x11. This is the
# normal case on a three-phase meter, which is why it is the main fixture.
""",
        MeterSimulator(key=TEST_KEY, frame_counter=4711),
    )

    write(
        "sim-single-phase.hex",
        """
# Synthetic. Produced by tools/generate_fixtures.py, not captured from a meter.
#
# A single-phase telegram: no L2 or L3 voltage or current. Fits in one frame,
# so CI is 0x10. Used to check that missing phases produce fewer entities
# instead of an error.
""",
        MeterSimulator(
            key=TEST_KEY,
            frame_counter=4712,
            three_phase=False,
            meter_number="1SAG0000000001",
        ),
    )

    write(
        "sim-many-segments.hex",
        """
# Synthetic. Produced by tools/generate_fixtures.py, not captured from a meter.
#
# The same three-phase telegram cut into 100 byte pieces instead of 250, to
# exercise reassembly across four segments. A real meter uses 250, but the
# segment numbering and the FIN bit behave identically.
""",
        MeterSimulator(key=TEST_KEY, frame_counter=4713, max_dlms_per_frame=100),
    )

    write(
        "sim-no-tsap.hex",
        """
# Synthetic. Produced by tools/generate_fixtures.py, not captured from a meter.
#
# A meter that puts the DLMS data straight after the CI field with no STSAP and
# DTSAP bytes. No Austrian operator is known to do this, but the generic profile
# detects it, and this fixture is what proves the detection works.
""",
        MeterSimulator(key=TEST_KEY, frame_counter=4714, three_phase=False, tsap=None),
    )

    write(
        "sim-p1.hex",
        """
# Synthetic. Produced by tools/generate_fixtures.py, not captured from a meter.
#
# The DSMR P1 interface used by Energienetze Steiermark and Kaernten Netz: no
# link layer at all, just the DLMS ciphering APDU on the line at 115200 8N1.
# Security control 0x30, so the telegram is authenticated as well as encrypted
# and the 12 byte GCM tag is on the wire. Both keys are the test key here; a
# real meter has a separate GUEK and GAK.
#
# One line, because one telegram is one APDU on this interface however long it
# gets.
""",
        MeterSimulator(
            key=TEST_KEY,
            frame_counter=4715,
            interface="p1",
            security_control=0x30,
        ),
    )

    write(
        "sim-hdlc.hex",
        """
# Synthetic. Produced by tools/generate_fixtures.py, not captured from a meter.
#
# The infrared interface used by Wiener Netze: DLMS over HDLC at 9600 8N1.
# Frames run between 0x7E flags with a CRC-16/X.25 check sequence, the LLC
# header E6 E7 00 sits at the front of the first segment, and the telegram is
# split across several frames using the segmentation bit in the frame format
# field.
""",
        MeterSimulator(
            key=TEST_KEY,
            frame_counter=4716,
            interface="hdlc",
            max_hdlc_info=128,
        ),
    )


if __name__ == "__main__":
    main()
