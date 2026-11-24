# RAK4630 local defaults and UDP monitor

This directory contains a reproducible patch and source overlay for
`icssw-org/MeshCom-Firmware`, based on the revision in [upstream.json](upstream.json).
It adds an opt-in nRF52 RF receive monitor and a compiled fallback profile.
It does not change the on-air protocol or the existing ExtUDP message formats.
Measured build sizes, test results and unverified hardware checks are in
[VALIDATION.md](VALIDATION.md).

The current profile **OK2ZAW-30-v2** restores the callsign, enables the node's
webserver and selects Ethernet. After settings loss, empty static address
fields select DHCP. This installation has an Ethernet module and a DHCP server.
Find the assigned IP in the router's leases or the node's status and open
`http://<node-IP>/` to configure it.

BT and the node's web interface save changes to the normal settings store;
these survive a restart. They do not rewrite the compiled fallback profile.
After the settings store is erased, the compiled profile takes effect again.
To preserve more values in that profile, edit
[local_profile.h](overlay/src/nrf52/local_profile.h) and rebuild.

Groups, position and the Pi's ExtUDP destination are not in the profile yet.
They can be entered through BT or the node web UI after recovery. A working
DHCP lease restores web access; the UDP feed still needs its destination.
Valid saved settings, including an intentionally disabled webserver, take precedence. Both automatic recovery
and an explicit factory reset use the compiled profile; partial corruption of a
still-readable settings file remains an upstream limitation.

## Prepare and build

From the gateway repository, prepare a new checkout in an empty destination:

```sh
python3 firmware/prepare.py /tmp/meshcom-custom
pio run -d /tmp/meshcom-custom -e wiscore_rak4631
```

The script can also use an existing clean checkout at the exact base revision.
It refuses local changes or overlay paths that already exist. It applies the
integration patch and copies the overlay. It never flashes hardware.

For a reviewed port to a newer version, pass `--revision <commit-or-tag>` to a
new destination, inspect the complete diff, and update `upstream.json` after
verification. A patch applying without conflicts does not prove semantic
compatibility. The Czech maintenance notes are in
[MESHCOM_FIRMWARE_NOTES.md](../MESHCOM_FIRMWARE_NOTES.md).

The integration patch enables `MC_RF_MONITOR=1` and `MC_LOCAL_DEFAULTS=1` only
for `wiscore_rak4631`. Each feature can be removed by setting its flag to zero.
The nRF52 monitor is independent of `MC_DIAG` and the serial capture buffer.

To create UF2 after a successful build, use the matching Adafruit framework's
`tools/uf2conv/uf2conv.py` with family `0xADA52840`, as in the upstream flashing
instructions. Keep a configuration export before upgrading. No hardware has
been flashed or power-cut tested as part of this implementation.

## Enable UDP reception

Configure the node's existing ExtUDP interface for the Pi:

```text
--extudpip <Pi-IP>
--extudp on
```

Match the gateway's `callsign` to `OK2ZAW-30`. The monitor uses the existing
ExtUDP destination and port 1799. RX collection starts after monitor
initialization while ExtUDP is enabled; toggling ExtUDP off stops collection.
`--nopmother on` disables **all raw RX export**, including queued records, while
status messages continue. This avoids exposing direct messages through the raw
capture path. The status reports this as `rx_enabled=false`.

## Wire protocol version 1

Every new datagram has `monitor_version=1`, `type`, receiver identity `node`,
a randomly generated 16-hex-digit `boot_id`, and unsigned 32-bit `uptime_ms`.
The node disables the monitor if its bounded entropy request cannot obtain a
boot ID. No boot counter is written to flash.

### RF receive events

`type=rf_rx` is emitted for each captured callback, before the shared MeshCom
decoder, ACK handling and filtering. Its other fields are:

| Field | Meaning |
| --- | --- |
| `event_seq` | Unsigned 32-bit RX event number, starting at zero; increments before queue insertion, including dropped or suppressed records. |
| `uptime_ms` | Capture time, not UDP send time. Wraps modulo 2^32. |
| `len` | Original byte count, 1 through 255. Oversized records are counted as dropped rather than truncated. |
| `raw_hex` | Exactly `2 * len` hex digits; all bytes preserved, including NUL and invalid UTF-8. |
| `rssi`, `snr` | Signed integer dBm and dB values provided by the radio callback. They describe the last RF link. |

The independent 8-record queue never overwrites an unread entry. It copies
without allocation, printing or networking in the callback. The loop formats
and sends at most one monitor datagram per pass under the existing SPI guard.
The sequence is separate from MeshCom `msg_id`; repeated packets are retained.

This is a capture of frames delivered by the configured receiver, not an IQ
recording or a record of other frequencies. Bad radio CRC frames without a
payload callback, receptions missed during TX and events before monitor
initialization are not included. `rf_tx`, `rf_error`, decoded extra metadata and
neighbor reports are not implemented in this first version.

### Node status

`type=node_status` is sent when the UDP transport first becomes ready and every
30 seconds while ready. It includes `firmware`, `profile`, `config_source`,
`heap_bytes`, `reset_reason`, `rx_enabled`, `rx_seen`, `rx_dropped`,
`rx_suppressed`, `queue_depth`, `queue_high_water`, `udp_failures` and
`format_failures`. Counters are unsigned 32-bit since boot and may wrap.

`config_source` is `keyed`, `legacy`, `defaults`, `profile` or `unknown`.
The `profile` identifies the compiled fallback, even when saved settings are
currently used. `reset_reason` is the core's saved reset-reason bitmask; it
does not by itself prove a power failure.

`rx_dropped` includes full-queue, contended-producer and invalid-size records.
`rx_suppressed` includes policy-suppressed arrivals and records discarded on a
NoPMOther policy change. `udp_failures` counts failed monitor sends, including
short writes and failed `endPacket()`. It is separate from RF errors. Failed
datagrams are not retried; status counters and sequence gaps help detect loss,
but UDP does not guarantee delivery or ordering.

## Gateway archive and validation

The gateway stores valid monitor events in a separate SQLite `rf_events` table,
including the original payload JSON, binary RF bytes, sender address and Pi
receive timestamp. It preserves repeats and UDP duplicates; it has no automatic
retention limit. The existing 300-datagram UDP log remains a separate short log.
Malformed or unsupported monitor records are reported there and are not added
to the RF archive or forwarded to email.

The **RF archiv** page at `/rf` provides date, node, boot, source, destination,
frame type and decode-result filters, cursor paging and NDJSON export at
`/rf/export`. Existing web authentication covers both routes. An exported line
contains `id`, `received_at`, `peer`, the original `packet` object and an
additional `decoded` object for RF frames. Query parameters are `start`, `end`,
`kind`, `node`, `boot`, `source`, `destination`, `frame_type` and `decode_status`.
`refresh=1` enables a five-second full-page refresh on the newest archive page.
Date endpoints follow the gateway display timezone; `end` includes that day.
Archive growth must be included in disk capacity planning.

`mesh_frame.py` inspects message, position, HEY, HN and RF ACK frames using the
pinned firmware format. It checks the MeshCom byte-sum FCS separately from radio
CRC; ACKs do not have that FCS or a source callsign. This diagnostic parser does
not decide relay eligibility and does not feed the email pipeline. The original
bytes are unchanged. Derived fields are held in `rf_frame_details`; older captures
are backfilled in bounded batches at database initialization. The parser has its
own version, independent of the UDP monitor wire protocol.

Status cards consider a status fresh for 90 seconds, show readable reset and
configuration-source labels, and warn when the configured gateway callsign differs
from the node. The session view separates RF events from duplicate deliveries of
the same event sequence. Missing sequences are counted only inside the observed
range, may be filled by reordered UDP, and include firmware-side losses or
suppression. No gap estimate is shown across an ambiguous 32-bit sequence wrap.

Run gateway and cross-language queue/codec tests with:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

The firmware core test compiles the actual queue, formatter and profile with
the host C++ compiler, checks burst loss and concurrent access, and ingests its
JSON through the real Python archive. Firmware builds verify the hardware
adapter; physical radio, Ethernet and power interruption tests remain required
before field deployment.
