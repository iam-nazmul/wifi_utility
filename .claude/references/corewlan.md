# CoreWLAN reference (pyobjc)

Quick reference for `sources/corewlan.py`. API names change between macOS releases — confirm
against current docs (Context7 / Apple developer docs) before relying on anything here.

## Getting the interface

```python
import CoreWLAN

client = CoreWLAN.CWWiFiClient.sharedWiFiClient()
iface = client.interface()          # default WiFi interface, or None if no WiFi hardware
```

Swift equivalent: `CWWiFiClient.shared().interface()`.

## Fields used by `Sample` (SPEC §4.1)

| Sample field | CWInterface call | Type / unit | Notes |
|---|---|---|---|
| `rssi_dbm` | `iface.rssiValue()` | int, dBm | 0 when not associated |
| `noise_dbm` | `iface.noiseMeasurement()` | int, dBm | 0 when not associated |
| `tx_rate_mbps` | `iface.transmitRate()` | float, Mbps | Changes with rate adaptation |
| `channel` | `iface.wlanChannel().channelNumber()` | int | `wlanChannel()` is None when not associated |
| `bssid` | `iface.bssid()` | str or None | **None without Location Services permission** |
| — | `iface.ssid()` | str or None | Same permission rule; not stored |
| — | `iface.powerOn()` | bool | WiFi radio on/off |
| — | `iface.interfaceName()` | str | e.g. `en0` |

Also available on `wlanChannel()`: `channelBand()` (2.4 / 5 / 6 GHz enum) and `channelWidth()`.

## Gotchas

- **Location permission (macOS 14+)**: `bssid()`/`ssid()` return None unless the process
  (Terminal, iTerm, VS Code, or the packaged app) has Location Services access. RSSI still
  works. The source must raise a clear error rather than run with an unknown link, because
  baselines are keyed by BSSID + channel.
- **Not associated**: `rssiValue()` returns 0 and `wlanChannel()` is None. Treat RSSI 0 as
  "no sample", never as a reading.
- **Update rate**: the driver refreshes RSSI on its own schedule. Polling at 5 Hz can return
  repeated values; that's expected. Don't interpolate.
- **Roaming / mesh networks**: BSSID can change without the SSID changing. Emit
  `link_changed` and recalibrate (SPEC §4.1).
- **Threading**: CoreWLAN calls are fine from a background thread; the rumps menu bar must
  stay on the main thread.
- **Deprecated tools**: the `airport` CLI was removed in recent macOS. `wdutil info` needs
  sudo. Don't shell out to either; use CoreWLAN.

## Out of scope (do not use)

`scanForNetworksWithName`, `associateToNetwork`, monitor mode, `CWEventDelegate` for other
networks, and anything needing root. The utility reads only the Mac's own link.

## Minimal probe

```bash
uv run python -c "import CoreWLAN as C; i=C.CWWiFiClient.sharedWiFiClient().interface(); \
print(i.rssiValue(), i.noiseMeasurement(), i.transmitRate(), \
i.wlanChannel() and i.wlanChannel().channelNumber(), i.bssid())"
```

If the last value prints `None`, grant Location Services access and rerun.
