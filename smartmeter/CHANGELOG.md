# Changelog

## 0.2.0

Austria turned out to have five customer interfaces rather than one, so this
release covers the three of them that carry DLMS and says plainly which
operators cannot be read at all.

**Correction.** 0.1.0 said the western operators use the Sagemcom T210-D. They
do not: TINETZ, Salzburg Netz, IKB and Vorarlberger Energienetze all deploy the
Kaifa MA309M, or the MA110M on single-phase connections. The meter model was
inferred from the KSM West cooperation rather than from evidence, and it was
wrong. It is now taken from the Oesterreichs Energie overview of Austrian meter
types and cross-checked against a real meter in Tirol.

- Added the DSMR P1 interface: 115200 8N1 with the DLMS APDU written straight
  onto the line. This is what Energienetze Steiermark, Energienetze Graz,
  Feistritzwerke and Kaernten Netz use. These meters authenticate their
  telegrams, so with both keys configured the GCM tag is verified for real.
- Added DLMS over HDLC through an infrared read head, which is what Wiener Netze
  uses. Frames between 0x7E flags, CRC-16/X.25, segmentation across frames.
- Operator profiles for all nine Bundeslaender: fifteen named operators plus one
  generic profile per interface.
- Netz Oberoesterreich, Linz Netz and Energie Klagenfurt are in the dropdown but
  refuse to start, with an explanation of what their meters speak and what does
  read them. Selecting them used to be possible and would have produced silence.
- The add-on now names the adapter you need when no device is selected, because
  an M-Bus adapter, a P1 cable and an infrared read head are not interchangeable
  and the meter model does not tell you which one your operator uses.
- P1 profiles raise the DTR and RTS lines on opening the port, since a P1 meter
  stays silent until its Data Request pin goes high.
- A wrong authentication key is now reported on the first telegram rather than
  the third.
- Capture files still replay whichever interface they came from: the format is
  worked out from the frames.

## 0.1.0

First release. Nothing here has been run against a physical meter yet, which is
why the add-on is marked experimental and every operator profile is marked
either "documented" or "assumed".

- Reads the M-Bus customer interface at 2400 baud 8E1 and reassembles telegrams
  that are split across several frames.
- Decrypts DLMS/COSEM general-glo-ciphering with AES-128-GCM and reads the
  fifteen OBIS values the Austrian customer interface documents.
- Publishes to Home Assistant over MQTT Discovery, with a device entry per
  meter, energy and power sensors that work in the energy dashboard, and
  availability through a last will.
- Only creates entities for the values a meter actually sends, so a
  single-phase meter gets eleven and not fifteen.
- Operator profiles for TINETZ, Salzburg Netz, IKB, Vorarlberger Energienetze,
  EVN / Netz Niederoesterreich and Kaernten Netz, plus a generic profile that
  works out the frame layout from the first telegram.
- A status page on ingress showing decode state, live values, error counters
  and the last frames received.
- `capture_raw` writes received frames to `/config/captures` for bug reports.
  The key is redacted from everything the add-on writes.
