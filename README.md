# Austrian Smart Meter

A Home Assistant add-on that reads the M-Bus customer interface on an Austrian
smart meter and publishes fifteen values to Home Assistant every five seconds,
including the two energy counters the energy dashboard needs. It is for people
in Austria whose grid operator has given them the encryption key for their own
meter.

[![CI](https://github.com/Alexander423/ha-smartmeter-at/actions/workflows/ci.yaml/badge.svg)](https://github.com/Alexander423/ha-smartmeter-at/actions/workflows/ci.yaml)
[![Version](https://img.shields.io/badge/version-0.1.0-blue)](smartmeter/CHANGELOG.md)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Nothing here has been run against a physical meter yet.** The protocol is
implemented from the published technical descriptions and tested against
generated telegrams. If you have a meter and an adapter, you are ahead of the
author, and a capture file would be genuinely useful. See
[Supported operators](#supported-operators) for how far each profile can be
trusted.

## How it is wired

```
   Smart meter                    M-Bus slave adapter          Raspberry Pi
  +--------------+               +------------------+        +------------+
  |              |   RJ12 6P6C   |                  |  USB   |            |
  |  [RJ12]      |===============|  MBUS+    USB-B  |========|  Home       |
  |   pin 3 +----|---- MBUS+     |  MBUS-           |        |  Assistant |
  |   pin 4 +----|---- MBUS-     |                  |        |  OS        |
  +--------------+               +------------------+        +------------+
     supplies ~30 V                 powered from USB
     one telegram every 5 s
```

The meter is the M-Bus master and supplies the bus voltage, which is why the
adapter has to be a **slave** module. A master adapter, or a plain USB serial
cable, will read nothing at all.

```
   RJ12 socket on the meter, latch downwards

    1   2   3   4   5   6
   ___ ___ ___ ___ ___ ___
  |   |   | + | - |   |   |
   -----------------------
            |   |
            |   +--- pin 4  MBUS-
            +------- pin 3  MBUS+

  pins 1, 2, 5 and 6 are not connected
```

The customer interface is a separate, galvanically isolated SELV output on the
outside of the meter. Reading it does not require opening anything, does not
touch the utility seal and does not affect billing. Operators provide it on
purpose. If a procedure you find somewhere involves a seal, it is not this one.

## What you need

- An M-Bus **slave** adapter with USB, around 30 to 50 euro. It must say slave,
  and it must take its power from USB rather than from the bus, because the
  meter supplies only 6 mA.
- An RJ12 6P6C cable long enough to reach from the meter to the Pi, around 5
  euro. Only two of the six conductors are used.
- The encryption key from your grid operator, free but slow. It is 32
  hexadecimal characters and usually arrives by post one to three weeks after
  you ask.

You also need an MQTT broker. The Mosquitto broker add-on is the usual one, and
this add-on picks up its address and credentials from Supervisor without being
told.

## Installing

1. In Home Assistant, go to **Settings** -> **Add-ons** -> **Add-on Store**,
   open the menu at the top right, choose **Repositories**, and paste
   `https://github.com/Alexander423/ha-smartmeter-at`.

   [![Add repository to my Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FAlexander423%2Fha-smartmeter-at)

2. Install **Austrian Smart Meter**. The container is built on your own machine,
   which takes a few minutes on a Raspberry Pi.
3. On the **Configuration** tab, choose your grid operator. That is the company
   that owns the meter, not the one that bills you.
4. Choose the M-Bus adapter from the device dropdown. An empty dropdown means
   the adapter is not plugged in or was not recognised.
5. Paste the key. Paste it rather than typing it; a single wrong character is
   the most common reason this does not work.
6. Start the add-on and watch the **Log** tab. Within ten seconds it names your
   meter and lists the values it read.
7. For the energy dashboard: **Settings** -> **Dashboards** -> **Energy**, then
   add "Active energy import" as grid consumption and "Active energy export" as
   return to grid.

### Getting the key

| Operator | Where to ask |
|---|---|
| TINETZ | Customer portal, key by post |
| Salzburg Netz | Customer portal |
| IKB | Customer service |
| Vorarlberger Energienetze | Customer service |
| EVN / Netz NOE | smartmeter.netz-noe.at |
| Kaernten Netz | Customer service |

The interface often has to be switched on at the meter as well, which some
operators do remotely and some do on a site visit.

## Supported operators

Three levels of confidence, because "supported" on its own would be a lie for
most of this table.

| Operator | Meter | Status | What that means |
|---|---|---|---|
| TINETZ | Sagemcom T210-D | Documented | Every field transcribed from the TINETZ "Technische Beschreibung Kundenschnittstelle". Not run against a meter. |
| Salzburg Netz | Sagemcom T210-D | Documented | Same KSM West specification, same fields. Not run against a meter. |
| IKB | Sagemcom T210-D | Assumed | Copy of the TINETZ profile because IKB is in the same cooperation. No IKB document was read. |
| Vorarlberger Energienetze | Sagemcom T210-D | Assumed | As above. |
| EVN / Netz NOE | Kaifa MA309 | Assumed | Different meter. The link layer matches every published community capture, and the profile detects the parts most likely to differ. |
| Kaernten Netz | unknown | Assumed | The generic profile with a name on it. |
| Generic | any | Assumed | Works the frame layout out from the first telegram. Use this if your operator is missing, then send a capture. |

A profile only becomes "verified" when somebody has run it against their own
meter and checked the numbers against the meter's own display. None have.

Single-phase meters send no L2 or L3 values. That is normal, and the add-on
creates entities only for what actually arrives, so you will not end up with
sensors stuck at "unknown".

## What it reads

Fifteen values from one telegram every five seconds:

| Value | OBIS | Unit |
|---|---|---|
| Active energy import, export | `1-0:1.8.0.255`, `1-0:2.8.0.255` | Wh |
| Active power import, export | `1-0:1.7.0.255`, `1-0:2.7.0.255` | W |
| Voltage L1, L2, L3 | `1-0:32/52/72.7.0.255` | V |
| Current L1, L2, L3 | `1-0:31/51/71.7.0.255` | A |
| Reactive energy import, export | `1-0:3.8.0.255`, `1-0:4.8.0.255` | varh |
| Meter clock, meter number, device name | `0-0:1.0.0.255` and others | |

The **Web UI** tab shows a live status page: whether frames are arriving,
whether they decrypt, the current values, error counters and the last frames as
hex. It is worth having open the first time you plug the adapter in.

## Limitations

- No profile has been confirmed against real hardware.
- The Austrian meters set the security control byte to encryption without
  authentication, so there is no GCM tag on the wire to verify. A wrong key is
  caught by checking that the decrypted bytes are a valid telegram, which works,
  but it is a structural check and not a cryptographic one. If your operator
  issues a second key, set `auth_key` and the tag is verified properly.
- Publishing every telegram writes about 17000 rows per sensor per day into the
  recorder. `min_publish_interval` trades resolution for that; energy totals are
  unaffected either way.
- Reactive energy carries no `device_class`, because an unrecognised one breaks
  the entity outright on older Home Assistant versions and reactive energy does
  not belong in the energy dashboard anyway.
- Nothing is prebuilt: the container is compiled on your machine at install
  time.

## Troubleshooting

**The device dropdown is empty.**
The adapter is not plugged in or the host did not recognise it. Check
**Settings** -> **System** -> **Hardware** for a `ttyUSB` or `ttyACM` entry, then
restart the add-on so Supervisor re-reads the list.

**"The serial device is not there", usually after a reboot.**
USB devices get renumbered. Reopen the configuration and pick the device again.
If your host offers a `/dev/serial/by-id/` entry, choose that instead and it
will not happen again.

**No frames arrive at all: the counter on the status page stays at zero.**
Either the interface is not switched on at the meter, or the wiring is wrong, or
the adapter is a master. Ask the operator to enable the interface. On many
meters an LED near the RJ12 socket blinks when data is being pushed.

**Frames arrive but the log says the key is wrong.**
It is the key. Check for a transposed character, and check that the key belongs
to this meter: a household with two meters gets two keys. The message appears
after three consecutive failures, so one damaged telegram will not trigger it.

**Values decode but are ten times too large.**
The scaling in your operator's profile is wrong. The add-on says so once when a
value falls outside a plausible range. Turn on `capture_raw`, run it for a
minute and open an issue with the file: the fix is one line in a profile.

**Entities go unavailable.**
Nothing has arrived for `stale_after` seconds, 30 by default. This is
deliberate. A power reading from an hour ago displayed as if it were current is
worse than nothing.

**The add-on will not start and the log mentions MQTT.**
There is no broker. Install the Mosquitto broker add-on and start it first.

More detail is in [the documentation tab](smartmeter/DOCS.md).

## Contributing

Adding an operator is one data file and one test fixture, and nothing else. A
capture from a meter that does not work yet is just as useful. See
[CONTRIBUTING.md](CONTRIBUTING.md).

The decryption work for Austrian meters was done first by
[tirolerstefan/kaifa](https://github.com/tirolerstefan/kaifa). The DLMS parsing
in [NECH2004/smartmeter_austria](https://github.com/NECH2004/smartmeter_austria)
and the ESP32 implementation in
[DomiStyle/esphome-dlms-meter](https://github.com/DomiStyle/esphome-dlms-meter)
were both useful references for what these meters actually send.

## License

MIT. See [LICENSE](LICENSE).
