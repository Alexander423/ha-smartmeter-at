# Austrian Smart Meter

This add-on reads the customer interface of an Austrian smart meter, decrypts
the telegram with the key your grid operator gave you, and publishes the values
to Home Assistant over MQTT. The meter pushes a telegram every five seconds;
nothing polls it.

## Which interface do you have

Austria has five customer interfaces and your grid operator decides which one
you get. The meter model does not tell you: the same Sagemcom family is M-Bus at
Netz NÖ and P1 in Styria, and the same Siemens IM350 is P1 in Carinthia and
infrared in Vienna. Find your operator, then buy the adapter.

| Operator | Interface | You need |
|---|---|---|
| TINETZ, Salzburg Netz, IKB, Vorarlberger Energienetze | M-Bus | M-Bus **slave** adapter, USB |
| Netz NÖ, Netz Burgenland, Stadtwerke Klagenfurt | M-Bus | M-Bus **slave** adapter, USB |
| Energienetze Steiermark, Energienetze Graz, Feistritzwerke | P1 | USB P1 cable (DSMR) |
| Kärnten Netz | P1 | USB P1 cable (DSMR) |
| Wiener Netze | Infrared | Magnetic IR read head, USB |
| Netz Oberösterreich | OMS over infrared | Nothing: see below |
| Linz Netz, Energie Klagenfurt | MEP or wireless M-Bus | Nothing: see below |

### The three operators that cannot work

Netz Oberösterreich runs AMIS with Siemens TD-3511 meters. The read head is the
same kind Vienna uses, but the data is OMS over M-Bus with AES-128 in CBC mode,
not DLMS. This add-on has no OMS decoder. The `amis_smartmeter_reader` and
`AMIS-Leser` projects do read these.

Linz Netz and Energie Klagenfurt run NES MTR1000/3000 meters. Generation 3 uses
the MEP expansion port speaking ANSI C12.19, on a connector unlike anything
here; generation 4 uses wireless M-Bus and needs an 868 MHz radio.

All three are in the operator list so that selecting one gives you this
explanation instead of a device dropdown and a long silence.

## What you need

- The adapter for your interface from the table above. They are not
  interchangeable, and plugging the wrong one in produces no error, just
  nothing.
- For M-Bus and P1, an RJ12 (6P6C) cable to reach the socket on the meter.
  Infrared needs no cable to the meter.
- The encryption key from your grid operator. You have to ask for it; it is not
  printed on the meter. The P1 operators issue two keys.
- An MQTT broker. The Mosquitto broker add-on is the usual choice, and this
  add-on picks up its address and credentials on its own.

## Before you start

The customer interface is a separate, galvanically isolated, SELV output. It is
outside the utility seal and reading it does not require opening anything, does
not affect billing and is a service the operators offer deliberately. If a
procedure you find anywhere asks you to remove a seal or open the meter, it is
not this one.

## Wiring

The same RJ12 socket is wired two different ways depending on the operator.

```
   RJ12 6P6C socket, looking at the meter, latch downwards

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

For M-Bus: it is not polarity sensitive in practice, but wire it the right way
round anyway. Take the power for the adapter from the USB side, because the
meter supplies at most 6 mA, which is four M-Bus loads, and an adapter drawing
its power from the bus will brown out.

For P1: the meter only sends while pin 2 is held high. Most cables tie it to the
+5V on pin 1. If yours does not, the add-on raises DTR and RTS on opening the
port, which covers the cables that expect that instead.

Wiener Netze has nothing to wire. The customer interface is the optical port on
the front of the meter and the read head holds itself on with a magnet.

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

| Operator | Where to ask | Keys |
|---|---|---|
| TINETZ | Customer portal, then by post | 1 |
| Salzburg Netz | Customer portal | 1 |
| IKB | Customer service | 1 |
| Vorarlberger Energienetze | Customer service | 1 |
| Netz NÖ | smartmeter.netz-noe.at | 1 |
| Netz Burgenland | Request activation | 1 |
| Energienetze Steiermark | Portal at e-netze.at | 2 |
| Energienetze Graz, Feistritzwerke | Customer service | 2 |
| Kärnten Netz | +43 50 525 6000 | 2 |
| Stadtwerke Klagenfurt | Customer service | 1 |
| Wiener Netze | Portal: Details, then Show Key | 1 |

Expect a wait of one to three weeks, except at Wiener Netze where the key is on
screen straight away. The interface itself often has to be enabled at the meter
as well, which some operators do remotely and some do on a site visit.

Where two keys are issued, the portal calls them GUEK (global unicast encryption
key) and GAK (global authentication key). The GUEK goes in `key` and the GAK in
`auth_key`. At Energienetze Steiermark they are well hidden in the portal, and
activation needs a meter in the "IME Opt-In" or "IMS Standard" configuration.

## Options

| Option | Default | What it does |
|---|---|---|
| `supplier` | `tinetz` | Which operator profile to use. This also decides the interface, the serial settings and which adapter you need. See the README for how much each one is worth trusting. |
| `port` | none | The serial device the adapter appears as. |
| `key` | none | The 32 character key from your operator, the GUEK where two are issued. |
| `auth_key` | empty | The second key, where your operator issues one. The P1 operators do. |
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
First check that the adapter matches your operator's interface. An M-Bus slave
adapter, a P1 cable and an infrared read head all appear as serial devices and
none of them reads another's interface, so the wrong one gives exactly this: no
error, no bytes. After that: the customer interface may not be switched on at
the meter, the wiring may be wrong, or an M-Bus adapter may be a master rather
than a slave. On many meters an LED near the socket blinks when it is pushing
data. If the log shows bytes being discarded rather than nothing at all, then
bytes are arriving and the framing does not match, which usually means the wrong
operator profile is selected.

**Nothing arrives on a P1 cable.**
P1 meters send only while the Data Request line on pin 2 is high. Most cables
tie it to +5V on pin 1; the add-on also raises DTR and RTS for the cables that
expect that. A cable that does neither will stay silent for ever with nothing in
the log to explain it.

**The log says the meter is sending readable DSMR text telegrams.**
The P1 interface is running in an older unencrypted mode. This add-on reads the
encrypted DLMS form. Ask your operator to enable the encrypted interface, or
open an issue with a capture.

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
