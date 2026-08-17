# Austrian Smart Meter

This add-on reads the M-Bus customer interface on the front of an Austrian smart
meter, decrypts the telegram with the key your grid operator gave you, and
publishes the values to Home Assistant over MQTT. The meter pushes a telegram
every five seconds; nothing polls it.

## What you need

- An M-Bus **slave** adapter with a USB connection. The meter supplies the bus
  voltage, so a master adapter will not work and a plain USB serial cable will
  not work either.
- An RJ12 (6P6C) cable to reach the socket on the meter.
- The encryption key from your grid operator. You have to ask for it; it is not
  printed on the meter.
- An MQTT broker. The Mosquitto broker add-on is the usual choice, and this
  add-on picks up its address and credentials on its own.

## Before you start

The customer interface is a separate, galvanically isolated, SELV output. It is
outside the utility seal and reading it does not require opening anything, does
not affect billing and is a service the operators offer deliberately. If a
procedure you find anywhere asks you to remove a seal or open the meter, it is
not this one.

## Wiring

The RJ12 socket has six positions. Only two are used.

```
   RJ12 socket, looking at the meter, latch downwards

    1   2   3   4   5   6
   ___ ___ ___ ___ ___ ___
  |   |   |   | * |   |   |
  |   |   | * |   |   |   |
   -----------------------
              |   |
              |   +--- pin 4  MBUS-
              +------- pin 3  MBUS+

  pins 1, 2, 5 and 6 are not connected
```

M-Bus is not polarity sensitive in practice, but wire it the right way round
anyway. Take the power for the adapter from the USB side: the meter supplies at
most 6 mA, which is four M-Bus loads, and an adapter that draws its power from
the bus will brown out.

## Installing

1. Add this repository to Home Assistant: **Settings** -> **Add-ons** ->
   **Add-on Store** -> the three dots at the top right -> **Repositories**, then
   paste the repository URL.
2. Install **Austrian Smart Meter**. The first install builds the container on
   your machine, which takes a few minutes on a Raspberry Pi.
3. Open the **Configuration** tab.
4. Pick your **grid operator**. This is the company that owns the meter
   (Netzbetreiber), not the company that sends you the bill.
5. Pick the **M-Bus adapter** from the device list. If the list is empty, the
   adapter is not plugged in or is not recognised.
6. Paste the **encryption key**. It is 32 characters of 0-9 and A-F. Paste it
   rather than typing it.
7. Start the add-on and open the **Log** tab.

Within about ten seconds the log says which meter it found and how many values
it read. The entities appear in Home Assistant immediately after that, grouped
under one device named after the meter number.

## Getting the key

Every operator issues the key on request and most send it by post. There is
usually a form or a portal setting called something like
"Kundenschnittstelle aktivieren".

| Operator | Where to ask |
|---|---|
| TINETZ | Customer portal, then the key arrives by post |
| Salzburg Netz | Customer portal |
| IKB | Customer service |
| Vorarlberger Energienetze | Customer service |
| EVN / Netz NOE | smartmeter.netz-noe.at |
| Kaernten Netz | Customer service |

Expect a wait of one to three weeks. The interface itself often has to be
enabled at the meter as well, which some operators do remotely and some do on a
site visit.

## Options

| Option | Default | What it does |
|---|---|---|
| `supplier` | `tinetz` | Which operator profile to use. See the table in the README for how much each one is worth trusting. |
| `port` | none | The serial device the adapter appears as. |
| `key` | none | The 32 character key from your operator. |
| `auth_key` | empty | A second key, only if your operator issued one. Almost none do. |
| `min_publish_interval` | `0` | Minimum seconds between MQTT updates. See below. |
| `stale_after` | `30` | Seconds without a telegram before entities go unavailable. |
| `capture_raw` | `false` | Write received frames to `/config/captures`. |
| `device_name` | empty | Overrides the device name in Home Assistant. |
| `replay_file` | empty | Development only. Read frames from a file instead of the serial port. |
| `log_level` | `info` | `debug` adds a hex dump of every frame. |

### About `min_publish_interval`

At the default of 0, every telegram is published, so each sensor gets a new
value every five seconds. That is about 17000 rows per sensor per day in the
recorder database. On a Raspberry Pi with an SD card, some people would rather
not.

Setting it to 30 or 60 drops the telegrams in between. The power graph gets
coarser and a short spike can be missed. Energy totals are unaffected, because
they are meter readings rather than samples: the value published is whatever the
meter counter said at that moment, so the energy dashboard stays correct either
way.

## Values

Fifteen values are read, of which four only exist on three-phase meters.

| Sensor | OBIS | Unit | Energy dashboard |
|---|---|---|---|
| Active energy import | `1-0:1.8.0.255` | Wh | Grid consumption |
| Active energy export | `1-0:2.8.0.255` | Wh | Return to grid |
| Active power import | `1-0:1.7.0.255` | W | |
| Active power export | `1-0:2.7.0.255` | W | |
| Voltage L1, L2, L3 | `1-0:32/52/72.7.0.255` | V | |
| Current L1, L2, L3 | `1-0:31/51/71.7.0.255` | A | |
| Reactive energy import | `1-0:3.8.0.255` | varh | |
| Reactive energy export | `1-0:4.8.0.255` | varh | |
| Meter clock | `0-0:1.0.0.255` | | |
| Meter number | `0-0:96.1.0.255` | | |
| Logical device name | `0-0:42.0.0.255` | | |

A single-phase meter sends no L2 or L3 values. The add-on creates entities only
for what arrives, so you will not find three voltage sensors sitting at
"unknown" for ever.

Reactive energy has no `device_class`. Home Assistant only recently learned
about reactive energy and an unrecognised device class breaks the entity
outright, whereas leaving it out only means the sensor does not appear in the
energy dashboard, where it does not belong anyway.

To set up the energy dashboard: **Settings** -> **Dashboards** -> **Energy**,
then add "Active energy import" as grid consumption and "Active energy export"
as return to grid.

## The status page

The **Web UI** tab shows what the add-on is doing right now: whether frames are
arriving, whether they decode, the current values, error counters and the last
few frames as hex. It is worth having open the first time you plug the adapter
in, because it distinguishes "nothing is arriving" from "things are arriving and
will not decrypt", which is the difference between a wiring problem and a key
problem.

## Troubleshooting

**The device list is empty.**
The adapter is not plugged in, or the host has not recognised it. Check
**Settings** -> **System** -> **Hardware** for a `ttyUSB` or `ttyACM` device.
Some adapters need a reboot after first being plugged in. If it is there but not
in the dropdown, restart the add-on so Supervisor re-reads the device list.

**"The serial device is not there" after a reboot.**
USB devices can be renumbered. Reopen the configuration and pick the device
again. Choosing the entry under `/dev/serial/by-id/` instead of `/dev/ttyUSB0`
avoids this permanently, if your host offers one.

**Nothing at all arrives: the frame counter on the status page stays at zero.**
Either the interface is not enabled at the meter, or the wiring is wrong, or the
adapter is a master rather than a slave. The customer interface has to be
switched on by the operator; on many meters an LED near the RJ12 socket blinks
when it is pushing data. If the log shows bytes being discarded rather than
nothing at all, the wiring is fine and the serial settings are wrong, which
should not happen at 2400 8E1 but would explain it.

**Frames arrive but nothing decodes, and the log says the key is wrong.**
That is almost always exactly what it is. Check for a transposed character, and
check that the key belongs to this meter: operators issue one per meter and a
household with two meters gets two keys. The add-on says this after three
consecutive failures rather than on the first, so a single damaged telegram does
not raise a false alarm.

**Values decode but look wrong, for example a voltage of 2314 V.**
The scaling in your operator's profile is wrong. The add-on warns about this
once when it sees a value outside a plausible range. Turn on `capture_raw`, let
it run for a minute, and open an issue with the file: fixing it is a one-line
change to a profile.

**Some sensors are missing.**
If your meter is single-phase, the L2 and L3 values do not exist and this is
expected. Otherwise the log lists, on the first successful telegram, which of
the values your operator's profile expects were not present.

**Entities show as unavailable.**
The add-on has not seen a telegram for `stale_after` seconds. Check the Log tab.
This is deliberate: showing you a power reading from an hour ago as if it were
current is worse than showing nothing.

**The add-on will not start and the log mentions MQTT.**
There is no broker. Install the Mosquitto broker add-on, start it, then start
this one. No broker settings are needed here: Supervisor passes them over.

## Sending a capture

If your meter does not work, this is the useful thing to send.

1. Turn on `capture_raw` and restart the add-on.
2. Wait a minute, then turn it off again. It also stops on its own after 2000
   frames.
3. The file is in `/config/captures/`, reachable through the File Editor,
   Studio Code Server or Samba add-ons.
4. Open an issue and attach it, and say which operator and which meter model.

The capture holds encrypted frames. Your key is not in it and cannot be worked
out from it. The meter number can be, once the frames are decrypted, so treat it
the way you would treat a photograph of your meter.
