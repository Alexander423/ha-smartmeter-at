# Austrian Smart Meter

A Home Assistant add-on that reads the customer interface on an Austrian smart
meter and publishes fifteen values to Home Assistant every five seconds,
including the two energy counters the energy dashboard needs. It is for people
in Austria whose grid operator has given them the encryption key for their own
meter.

Austria does not have one customer interface, it has five, and which one you get
follows from your grid operator rather than from your meter model. This add-on
reads the three that carry DLMS: wired M-Bus, DSMR P1, and DLMS over HDLC
through an infrared read head. That covers eight of the nine Bundesländer. It
cannot read the two Upper Austrian operators, and it says so rather than failing
quietly. See [supported operators](#supported-operators).

[![CI](https://github.com/Alexander423/ha-smartmeter-at/actions/workflows/ci.yaml/badge.svg)](https://github.com/Alexander423/ha-smartmeter-at/actions/workflows/ci.yaml)
[![Version](https://img.shields.io/badge/version-0.2.0-blue)](smartmeter/CHANGELOG.md)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Nothing here has been run against a physical meter yet.** The protocol is
implemented from the published technical descriptions and tested against
generated telegrams. If you have a meter and an adapter, you are ahead of the
author, and a capture file would be genuinely useful. See
[Supported operators](#supported-operators) for how far each profile can be
trusted.

## Find your interface first

Buying the wrong cable is the most expensive mistake here, and the meter model
will not tell you which one you need. Look up your grid operator, not your
meter:

| If your operator is | you need | roughly |
|---|---|---|
| TINETZ, Salzburg Netz, IKB, Vorarlberger Energienetze, Netz NÖ, Netz Burgenland, Stadtwerke Klagenfurt | M-Bus **slave** adapter, USB | 30 to 50 euro |
| Energienetze Steiermark, Energienetze Graz, Feistritzwerke, Kärnten Netz | USB **P1** cable (DSMR) | 15 to 30 euro |
| Wiener Netze | magnetic **infrared** read head, USB | 20 to 40 euro |
| Netz Oberösterreich, Linz Netz, Energie Klagenfurt | nothing that helps: see below | |

All three plug into the Pi over USB and appear as a serial device. None of them
substitutes for another.

## How it is wired

The M-Bus case, which is the one most of western Austria has:

```
   Smart meter                    M-Bus slave adapter          Raspberry Pi
  +--------------+               +------------------+        +------------+
  |              |   RJ12 6P6C   |                  |  USB   |            |
  |  [RJ12]      |===============|  MBUS+    USB-B  |========|  Home      |
  |   pin 3 +----|---- MBUS+     |  MBUS-           |        |  Assistant |
  |   pin 4 +----|---- MBUS-     |                  |        |  OS        |
  +--------------+               +------------------+        +------------+
     supplies ~30 V                 powered from USB
     one telegram every 5 s
```

The meter is the M-Bus master and supplies the bus voltage, which is why the
adapter has to be a **slave** module. A master adapter, or a plain USB serial
cable, will read nothing at all.

The same RJ12 socket is wired two different ways depending on the operator, and
this is the part that catches people out:

```
   RJ12 6P6C socket on the meter, latch downwards

    1   2   3   4   5   6
   ___ ___ ___ ___ ___ ___
  |   |   |   |   |   |   |
   -----------------------

   M-Bus operators          P1 operators (DSMR)
   1  not connected         1  +5V
   2  not connected         2  Data Request
   3  MBUS+                 3  Data Ground
   4  MBUS-                 4  not connected
   5  not connected         5  Data
   6  not connected         6  Power Ground
```

Wiener Netze has no socket to wire at all: its customer interface is the optical
port on the front of the meter, and the read head holds itself on with a magnet.

The customer interface is a separate, galvanically isolated SELV output on the
outside of the meter. Reading it does not require opening anything, does not
touch the utility seal and does not affect billing. Operators provide it on
purpose. If a procedure you find somewhere involves a seal, it is not this one.

## What you need

- The right adapter for your operator from the table above, 15 to 50 euro. For
  M-Bus it must say **slave**, and it must take its power from USB rather than
  from the bus, because the meter supplies only 6 mA.
- For M-Bus and P1, an RJ12 6P6C cable long enough to reach from the meter to
  the Pi, around 5 euro. Infrared needs no cable to the meter.
- The encryption key from your grid operator, free but slow. It is 32
  hexadecimal characters and usually arrives by post one to three weeks after
  you ask. The P1 operators issue two keys; Wiener Netze shows the key on screen
  straight away.

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
| Netz NÖ | smartmeter.netz-noe.at |
| Netz Burgenland | Request activation, they enable the RJ12 socket |
| Energienetze Steiermark | Portal at e-netze.at, two keys, well hidden |
| Energienetze Graz, Feistritzwerke | Customer service, expect two keys |
| Kärnten Netz | Customer service, +43 50 525 6000 |
| Stadtwerke Klagenfurt | Customer service |
| Wiener Netze | Smart Meter portal, Details, then Show Key |

The interface often has to be switched on at the meter as well, which some
operators do remotely and some do on a site visit. Wiener Netze is the only one
that hands the key over immediately; everyone else posts it.

Where two keys are issued they are labelled GUEK and GAK. The GUEK goes in
`key`, the GAK in `auth_key`. With both, every telegram is checked for
authenticity as well as decrypted.

## Supported operators

Grouped by Bundesland, because that is how you find yourself in it. The status
column is deliberately blunt: "supported" on its own would be a lie for most of
this table.

| Bundesland | Operator | Meter | Interface | Status |
|---|---|---|---|---|
| Burgenland | Netz Burgenland | Landis+Gyr E450 | M-Bus | Assumed |
| Kärnten | Kärnten Netz | Iskraemeco AM550, Siemens IM150/350 | P1 | Assumed |
| Kärnten | Stadtwerke Klagenfurt | Landis+Gyr E450 | M-Bus | Assumed |
| Kärnten | Energie Klagenfurt | NES MTR1000/3000 | MEP | **Not readable** |
| Niederösterreich | Netz NÖ (EVN) | Sagemcom T216-D, Kaifa MA309M | M-Bus | Assumed |
| Oberösterreich | Netz Oberösterreich | Siemens TD-3511 AMIS | OMS over infrared | **Not readable** |
| Oberösterreich | Linz Netz | NES MTR1000/3000 | MEP or wireless M-Bus | **Not readable** |
| Salzburg | Salzburg Netz | Kaifa MA309M / MA110M | M-Bus | Documented |
| Steiermark | Energienetze Steiermark | Sagemcom T216-D / S210 | P1 | Documented |
| Steiermark | Energienetze Graz | Sagemcom T216-D / S210 | P1 | Assumed |
| Steiermark | Feistritzwerke | Sagemcom T216-D / S210 | P1 | Assumed |
| Tirol | TINETZ | Kaifa MA309M / MA110M | M-Bus | Documented |
| Tirol | IKB | Kaifa MA309M / MA110M | M-Bus | Assumed |
| Vorarlberg | Vorarlberger Energienetze | Kaifa MA309M / MA110M | M-Bus | Assumed |
| Wien | Wiener Netze | Siemens IM150/350, Iskraemeco AM550, Landis+Gyr E450s4 | Infrared | Assumed |

Wiener Netze also supplies parts of Lower Austria and Burgenland, so check the
name on your bill rather than the Bundesland.

What the status column means:

- **Documented**: transcribed from a published technical description of that
  operator's interface. Not run against a meter.
- **Assumed**: the meter and the interface are taken from the Oesterreichs
  Energie overview of Austrian meter types, but the frame details are inherited
  from a related operator. Nobody has read that operator's own document.
- **Verified**: somebody ran it against their own meter and checked the numbers
  against the meter's display. Nothing is verified yet.

There are also three generic profiles, one per interface, which work the frame
layout out from the first telegram. Use one if your operator is missing, then
send a capture so a named profile can be added.

### Upper Austria does not work

Both Upper Austrian operators use protocols this add-on does not decode, and no
adapter changes that.

Netz Oberösterreich runs AMIS with Siemens TD-3511 meters. The interface is
optical, like Vienna's, but what comes out of it is OMS over M-Bus with AES-128
in CBC mode rather than DLMS. Different application layer, different cipher
mode. [amis_smartmeter_reader](https://github.com/mgerhard74/amis_smartmeter_reader)
and the [AMIS-Leser](https://www.mitterbaur.at/amis-leser.html) do read these.

Linz Netz and Energie Klagenfurt run NES MTR1000/3000 meters. Generation 3 uses
the MEP expansion port speaking ANSI C12.19; generation 4 moved to wireless
M-Bus, which needs an 868 MHz receiver rather than a cable.

Both are in the operator dropdown anyway, so that selecting one produces an
explanation rather than a device list and a long silence.

### Do not pick by meter model

The same Sagemcom family is wired as M-Bus at Netz NÖ and as P1 in Styria, and
the same Siemens IM350 is P1 in Carinthia and infrared in Vienna. The interface
follows the operator, which is why the operator is what you select.

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
- Upper Austria cannot work at all, for the reasons above.
- The M-Bus and infrared meters set the security control byte to encryption
  without authentication, so there is no GCM tag on the wire to verify. A wrong
  key is caught by checking that the decrypted bytes are a valid telegram, which
  works, but it is a structural check and not a cryptographic one. The P1
  operators do authenticate, and with both keys set the tag is verified
  properly.
- The P1 and infrared profiles are transcribed from working community
  implementations rather than from an operator document, and neither has been
  run against a meter by anyone here.
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
Most often the adapter is the wrong kind for your operator. Check the table at
the top: an M-Bus slave adapter, a P1 cable and an infrared read head are not
interchangeable, and plugging the wrong one in produces exactly this silence.
Otherwise the interface is not switched on at the meter, or the wiring is wrong,
or an M-Bus adapter is a master rather than a slave. On many meters an LED near
the socket blinks when data is being pushed.

**Nothing arrives on a P1 cable specifically.**
P1 meters only send while the Data Request line on pin 2 is held high. Most
cables tie it to the +5V on pin 1; a few expect the host to raise DTR or RTS,
which the P1 profiles do on opening the port. If your cable does neither, it
will sit silent with no error at all.

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

**The add-on refuses to start and says the interface carries no DLMS.**
Your operator is one of the three that cannot be read. See above.

More detail is in [the documentation tab](smartmeter/DOCS.md).

## Contributing

Adding an operator is one data file and one test fixture, and nothing else. A
capture from a meter that does not work yet is just as useful. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## Sources and prior art

The per-operator mapping of meter model to customer interface comes from
[Übersicht über in Österreich eingesetzte Smart-Meter-Gerätetypen](https://oesterreichsenergie.at/fileadmin/user_upload/Smart_Meter-Plattform/20200201_Konzept_Kundenschnittstelle_SM.pdf)
published by Oesterreichs Energie, cross-checked against the per-operator
connection guides at [igecos.com](https://igecos.com/verbindungsanleitungen/).

The protocol work is other people's:
[tirolerstefan/kaifa](https://github.com/tirolerstefan/kaifa) did the original
decryption of the Austrian M-Bus meters,
[DomiStyle/esphome-dlms-meter](https://github.com/DomiStyle/esphome-dlms-meter)
is the ESP32 implementation of the same,
[NECH2004/smartmeter_austria](https://github.com/NECH2004/smartmeter_austria) is
a HACS integration worth reading for its DLMS parsing,
[debug-richard/sagemcom-dsmr](https://github.com/debug-richard/sagemcom-dsmr)
documents the P1 frame layout used in Styria, and
[bernikr/esphome-wienernetze-smartmeter](https://github.com/bernikr/esphome-wienernetze-smartmeter)
documents the HDLC framing Wiener Netze uses. The P1 and infrared support here
was written from those last two.

## License

MIT. See [LICENSE](LICENSE).
