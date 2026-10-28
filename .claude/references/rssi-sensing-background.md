# Background: motion sensing from WiFi RSSI

Why the detector is built the way it is, and what it can't do. Read before proposing
"improvements" to detection.

## Mechanism

The signal between router and Mac arrives over a direct path plus reflections off walls,
furniture and people. A moving body changes those paths, so received power fluctuates. A still
body changes the static pattern once and then looks like furniture — which is why a calibrated
baseline catches motion but not stillness.

## What helps

- **Line of sight crossing**: a person walking through the direct path (first Fresnel zone)
  causes the largest change, often several dB. Placing the Mac so the router is across the
  room being watched gives the best coverage.
- **Variance features over level features**: absolute RSSI drifts with temperature, router
  power changes and humidity. Short-window variability (`rssi_std`, `delta_energy`) is much
  more stable, which is why the baseline is on those.
- **Robust statistics**: median/MAD resist the occasional spike (driver glitches, a single
  packet at a different rate) that would skew mean/std.
- **Hysteresis + minimum durations**: one noisy window shouldn't flip state; it's what keeps
  false events near the SPEC §9 target.
- **tx rate changes**: the router's rate adaptation reacts to channel changes, giving a weak
  but independent motion signal.

## Known limits (v1, RSSI only)

- Coarse: one number per sample, refreshed at the driver's pace, quantised to 1 dB.
- No still-presence detection, no counting, no location, no identity. Don't build UI or
  features that imply otherwise.
- False triggers: fans, pets, curtains, doors, a microwave on 2.4 GHz, neighbours' traffic on
  the same channel, router channel/power changes, the Mac itself moving (laptop on a lap).
- Coverage falls off sharply away from the Mac–router line.

## Why CSI is the v2 path

Channel State Information gives amplitude and phase per OFDM subcarrier (dozens to hundreds of
values per packet, at packet rate). That reveals small movements and breathing-scale changes
RSSI can't. Consumer routers and macOS don't expose it; an ESP32 running ESP-IDF's CSI API does.
In the design it is just another `SignalSource` producing richer samples (SPEC §2).

## Further reading (search to find current versions)

- Espressif ESP-IDF docs: "Wi-Fi Channel State Information".
- Survey papers on "WiFi sensing" / "device-free WiFi motion detection" for RSSI vs CSI
  comparisons.
- IEEE 802.11bf (WLAN sensing) for where the standard is heading.
