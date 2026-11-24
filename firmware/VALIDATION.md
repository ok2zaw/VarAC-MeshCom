# Validation of the first RAK monitor build

Base: `icssw-org/MeshCom-Firmware` commit
`68286e071951bed184ed68ca4e8363e19567aea5`.
Profile: `OK2ZAW-30-v2`, callsign, Ethernet DHCP recovery and webserver enabled. Both custom features enabled for
`wiscore_rak4631`. Validation date: 24 November 2026.

## Build results

| Environment | Result | Feature selection |
| --- | --- | --- |
| `wiscore_rak4631` | Passed | Compiled profile and RX monitor enabled. |
| `heltec_wifi_lora_32_V3` | Passed | Custom features disabled; shared ExtUDP code checked on ESP32-S3. |
| `ttgo_tbeam` | Passed | Custom features disabled; classic ESP32 compatibility. |
| `E22_XML-DevKitC` | Passed | Custom features disabled; constrained ESP32 compatibility. |

Commands used were `pio run -e wiscore_rak4631` and
`pio run -e heltec_wifi_lora_32_V3 -e ttgo_tbeam -e E22_XML-DevKitC`, with an
isolated PlatformIO core directory. The RAK build uses
`framework-arduinoadafruitnrf52@1.10700.0`. Four custom-tree environments
passed, zero failed; the remaining upstream environments were not built.
An additional unmodified RAK baseline build passed. The RAK build and all 133
gateway tests were rerun for profile v2. The three ESP32 compatibility results
remain from v1: their shared code is unchanged, and the changed nRF52 profile
is not compiled into those environments.

| RAK size reported by PlatformIO | Baseline | Custom | Change |
| --- | ---: | ---: | ---: |
| Flash bytes | 674616 | 676944 | +2328 |
| Static RAM bytes | 103092 | 106596 | +3504 |

These are the reported application/static sizes, not a measurement of free
heap or worst-case stack usage on a running radio. The linker also reserves
heap/stack space, so its RAM-region total is different from static RAM.

## Automated checks

- `python -m unittest discover -s tests -v`: **133 tests passed** in the
  project virtual environment with the declared `aprslib` dependency installed.
- Nine RF monitor tests cover byte-exact binary storage, repeated receptions,
  restart IDs, malformed records, independence from email processing, archive
  persistence beyond the short UDP log, filtering, paging, authenticated export,
  date boundaries and HTML escaping.
- The cross-language test compiles the actual C++ queue, formatter and profile.
  It checks the profile webserver flag while preserving other settings bits, Ethernet selection, a 255-byte frame, output-buffer overflow, burst overflow without
  overwrites, suppression, invalid sizes and 100000 concurrent queue operations.
  Its JSON is parsed and archived by the real Python implementation.
- `prepare.py` applied the integration patch and overlay to a fresh clean
  checkout. All 11 changed or added firmware source/config files matched the
  files used for the final RAK build. A second preparation refused to overwrite
  the resulting local changes.
- `git diff --check` passed. Documentation links and the UF2 block structure
  were checked locally.

The tests opening local TCP/UDP sockets require permission to bind loopback
ports in a restricted execution environment. No radio or email traffic was
sent by the tests.

## Scope and compatibility review

| Surface | Change or reason unaffected |
| --- | --- |
| On-air encoder and frame budget | Unchanged; no new RF bytes or scheduled RF transmissions. |
| On-air decoder | Unchanged. The RAK wrapper copies received bytes before calling it. |
| RF ACK, duplicates, HEY and filters | Original processing unchanged; capture precedes it. |
| BLE, OLED, e-paper, TFT and LVGL | No protocol or rendering changes; restored settings use their existing paths. |
| Serial commands | No new command or persisted bit. Profile application emits a diagnostic line. |
| Existing ExtUDP | Existing formats retained. New transport helper is compile guarded. |
| nRF52 settings | Profile applied at both defaults-write sites and for unconfigured startup data. A reset caused by layout mismatch now reloads settings before saving. |
| ESP32 settings | Unchanged; custom nRF52 sources are excluded from these builds. |
| Python gateway | New event types use a separate archive and cannot enter email handling. Existing tests pass. |
| Gateway web | Added `/rf` and `/rf/export` under existing authentication. Template rendering, filters and escaping tested. |

## Build artifacts

Local outputs are in `builds/OK2ZAW-30-monitor-v2/`, excluded from Git:

- `wiscore_rak4631.uf2`, `wiscore_rak4631.hex`, `wiscore_rak4631.zip`.
- `build-info.json` with upstream revision, source and artifact SHA256 hashes,
  feature flags, profile fields, memory results and validation status.
- Baseline, custom build, compatibility build and test logs.

UF2 SHA256:
`cb085679302258674343a084bcae93ec7e327ea40abcb8e29f8a35e16290d97b`.

## Not checked on hardware

No device was flashed. Physical RX/TX behavior, SPI timing under Ethernet
load, callback/loop stack headroom, Ethernet interruption and power-cut
recovery remain unverified. Partial corruption accepted by the upstream
keyed-store sanity gate is not fixed by the fallback profile.

The groups, position and Pi ExtUDP address still need to be supplied for a
fallback that restores the whole installation. Ethernet and DHCP availability
were confirmed by the operator; the v2 profile enables the node webserver for
configuration after settings loss. TX/error
events and neighbor export are later work, not features of this artifact.

A browser screenshot check was attempted but the browser connector reported
that attachment requires a network policy. The local preview was stopped;
visual layout has not been verified in a browser. HTTP/template behavior is
covered by the automated tests.
