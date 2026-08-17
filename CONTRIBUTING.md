# Contributing

## Adding a grid operator

This is the contribution the project needs most, and it is deliberately small:
**one data file and one test fixture**. If your change touches anything else,
something is wrong with the design and it is worth saying so in the issue.

1. Turn on `capture_raw` in the add-on, let it run for a minute, then turn it
   off. The file lands in `/config/captures/`.
2. Copy it to `tests/fixtures/`, named after the operator, and put a comment at
   the top saying which meter it came from and when. The frames are encrypted,
   so the file gives away nothing except the fact that you have a meter.
3. Copy `smartmeter/suppliers/tinetz.yaml` to
   `smartmeter/suppliers/your-operator.yaml` and edit the name, the notes and
   whatever else differs. Set `status:` honestly:
   - `verified` if you ran it against your own meter and checked the numbers
     against the display,
   - `documented` if you transcribed it from the operator's technical
     description,
   - `assumed` if you copied another profile and hoped.
4. Add the profile id to the `supplier` list in `smartmeter/config.yaml`. There
   is a test that fails if you forget.
5. Add your fixture to the parametrised list in `tests/test_decoder.py`.

A capture on its own is a useful contribution too. Open an issue with the file
and say which operator and meter model it is, and the profile can be written
from it.

## Working on the code

You do not need an M-Bus adapter. The simulator produces correctly encrypted
telegrams, including segmented ones, so the whole stack is testable on a laptop.

```
uv sync --python 3.12
uv run pytest
uv run ruff check .
uv run ruff format .
```

Two files are generated and committed. Regenerate them if you change the
generator, and CI will tell you if you forget:

```
uv run python tools/generate_fixtures.py
uv run python tools/generate_images.py
```

To decode a capture without Home Assistant, a broker or an adapter:

```
uv run python -m ha_smartmeter decode tests/fixtures/sim-three-phase.hex \
  --key 36C66639E48A8CA4D6BC8B282A793BBB
```

To run the add-on against a capture instead of a serial port, set `replay_file`
in the options.

## How the code is arranged

Each layer takes bytes or objects in and gives objects out, and nothing imports
the layer below it except through those types.

```
transport/       serial port, or a hex file for replay      bytes
mbus/frame.py    one M-Bus frame, checksum verified         MBusFrame
mbus/reader.py   resynchronising scanner over a byte stream
mbus/reassembly  CI segmentation, joins frames              bytes
dlms/apdu.py     general-glo-ciphering header               CipheredApdu
dlms/crypto.py   AES-128-GCM                                bytes
dlms/axdr.py     A-XDR decoder                              Node
dlms/telegram.py OBIS extraction                            Telegram
mqtt/            discovery and state                        MQTT messages
```

`pyserial` is imported in exactly one file. If you find yourself needing it
anywhere else, the layering has slipped.

## Rules of thumb

- Every error a user can cause needs a `hint` saying what to do about it, in
  the words a person who has never heard of DLMS would use. There is a test
  that checks the hints exist.
- The AES key must never reach a log line, a capture file or the status page.
  `tests/test_secrets.py` enforces this; add to it rather than trusting review.
- Anything that differs between operators belongs in a profile, not in an `if`.
- A telegram that cannot be decoded costs one telegram. Nothing in the read
  loop may raise its way out to the container.

## Reporting a bug

Include the add-on log at `debug`, the operator profile you selected, and a
capture if the problem is about decoding. Say which meter model you have; it is
printed on the front.
