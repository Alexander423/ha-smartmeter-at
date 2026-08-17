# Changelog

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
