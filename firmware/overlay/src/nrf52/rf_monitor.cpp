#if defined(MC_RF_MONITOR) && MC_RF_MONITOR
#ifndef NRF52_SERIES
#error MC_RF_MONITOR currently supports nRF52 only
#endif
#include <Arduino.h>
#include "configuration.h"
#include "rf_monitor.h"
#include "rf_monitor_core.h"
#include "local_defaults.h"
#include "extudp_functions.h"
#include <nrf_soc.h>

extern uint32_t nrf52_getFreeHeap();
extern uint32_t readResetReason();

namespace {
rf_monitor::Queue queue;
char boot_id[17] = {};
uint32_t udp_failures = 0, format_failures = 0, last_status = 0;
std::atomic<bool> started{false};
bool status_sent = false;
// Main-loop owned buffers; never shared with the radio callback.
rf_monitor::Frame next_frame;
char json[1024];

bool rawEnabled() { return !(meshcom_settings.node_sset3 & 0x8000); }
void sendJson(size_t len) {
    if (!len) { ++format_failures; return; }
    if (!sendExternMonitor(reinterpret_cast<const uint8_t *>(json), static_cast<uint16_t>(len)))
        ++udp_failures;
}
}

void rfMonitorBegin() {
    uint32_t id[2] = {};
    // Bounded retries, loop task only. Do not label a predictable ID as a new boot.
    for (unsigned i = 0; i < 20; ++i) {
        if (sd_rand_application_vector_get(reinterpret_cast<uint8_t *>(id), sizeof(id)) == NRF_SUCCESS) {
            snprintf(boot_id, sizeof(boot_id), "%08lX%08lX",
                     static_cast<unsigned long>(id[0]), static_cast<unsigned long>(id[1]));
            started.store(true, std::memory_order_release);
            return;
        }
        delay(1);
    }
    Serial.println("[RFMON] disabled: boot ID entropy unavailable");
}

void rfMonitorCapture(const uint8_t *data, uint16_t len, int16_t rssi, int8_t snr) {
    if (!started.load(std::memory_order_acquire) || !bEXTUDP) return;
    // With NoPMOther enabled, raw capture is disabled entirely: decoding in this
    // callback would add latency, and malformed frames cannot be safely classified.
    queue.push(data, len, rssi, snr, static_cast<uint32_t>(millis()), !rawEnabled());
}

void rfMonitorPoll() {
    if (!started.load(std::memory_order_acquire)) return;
    // Also discard queued frames when policy changes, before any network send.
    if (!rawEnabled()) queue.discardSuppressed();
    if (!externMonitorReady()) return;
    const uint32_t now = static_cast<uint32_t>(millis());
    if (!status_sent || static_cast<uint32_t>(now - last_status) >= 30000UL) {
        rf_monitor::Status s;
        s.ms = now; s.heap = nrf52_getFreeHeap(); s.reset_reason = readResetReason();
        s.seen = queue.seen(); s.dropped = queue.dropped(); s.suppressed = queue.suppressed();
        s.depth = queue.depth(); s.high_water = queue.highWater();
        s.udp_failures = udp_failures; s.format_failures = format_failures;
        s.rx_enabled = rawEnabled();
        s.firmware = SOURCE_VERSION SOURCE_VERSION_SUB;
        s.config_source = localSettingsSource(); s.profile = localDefaultsProfile();
        sendJson(rf_monitor::formatStatus(json, sizeof(json), s, meshcom_settings.node_call, boot_id));
        last_status = now; status_sent = true;
        return;
    }
    if (queue.pop(next_frame))
        sendJson(rf_monitor::formatRx(json, sizeof(json), next_frame, meshcom_settings.node_call, boot_id));
}
#endif
