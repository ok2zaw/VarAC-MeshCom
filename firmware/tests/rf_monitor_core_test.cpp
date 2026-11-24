#include "rf_monitor_core.h"
#include "nrf52/local_profile.h"
#include <cassert>
#include <thread>
#include <iostream>

int main() {
    using namespace rf_monitor;
    Queue q;
    Frame f;
    assert(!q.pop(f));
    uint8_t bytes[MaxPayload];
    for (size_t i = 0; i < MaxPayload; ++i) bytes[i] = static_cast<uint8_t>(i);
    assert(q.push(bytes, MaxPayload, -103, -7, UINT32_MAX));
    assert(q.pop(f));
    assert(f.len == MaxPayload && f.ms == UINT32_MAX);
    assert(memcmp(f.data, bytes, MaxPayload) == 0);
    char json[1024];
    assert(formatRx(json, sizeof(json), f, "OK2ZAW-30", "0123456789ABCDEF") > 0);
    std::cout << json << '\n';  // Consumed by Python's real ingest path.
    char tiny[10] = "unchanged";
    assert(formatRx(tiny, sizeof(tiny), f, "node", "boot") == 0);
    assert(tiny[0] == 0);
    assert(formatRx(nullptr, 0, f, "node", "boot") == 0);
    f.len = MaxPayload + 1;
    assert(formatRx(json, sizeof(json), f, "node", "boot") == 0);

    // Bursts never overwrite an unread frame. Repeated bytes are separate events.
    for (uint32_t i = 0; i < QueueSlots; ++i) assert(q.push(bytes, 3, -90, 4, i));
    assert(!q.push(bytes, 3, -90, 4, 99));
    assert(q.dropped() == 1 && q.highWater() == QueueSlots);
    for (uint32_t i = 0; i < QueueSlots; ++i) {
        assert(q.pop(f));
        assert(f.ms == i && f.seq == i + 1);
    }
    assert(q.push(bytes, 3, -90, 4, 101));
    assert(q.pop(f) && f.seq == QueueSlots + 2);
    assert(!q.push(bytes, 3, -90, 4, 102, true));
    assert(q.suppressed() == 1);
    assert(!q.push(nullptr, 3, -90, 4, 102));
    assert(!q.push(bytes, MaxPayload + 1, -90, 4, 102));
    assert(q.dropped() == 3);
    assert(q.push(bytes, 3, -90, 4, 103));
    q.discardSuppressed();
    assert(!q.pop(f) && q.suppressed() == 2);

    // Cross-thread producer/consumer with repeated ring wrap and loss accounting.
    Queue concurrent;
    constexpr uint32_t count = 100000;
    std::atomic<bool> done{false};
    std::thread producer([&] {
        for (uint32_t i = 0; i < count; ++i) {
            uint8_t data[4]; memcpy(data, &i, sizeof(i));
            concurrent.push(data, sizeof(data), -100, 1, i);
        }
        done.store(true, std::memory_order_release);
    });
    uint32_t received = 0, prev = 0;
    while (!done.load(std::memory_order_acquire) || concurrent.depth()) {
        if (!concurrent.pop(f)) continue;
        uint32_t value; memcpy(&value, f.data, sizeof(value));
        assert(value == f.seq && value == f.ms);
        assert(received == 0 || value > prev);
        prev = value; ++received;
    }
    producer.join();
    assert(received + concurrent.dropped() == count);

    Status s;
    s.ms = 123; s.heap = 10000; s.rx_enabled = true;
    s.firmware = "4.40a"; s.config_source = "profile"; s.profile = "profile\"\\\n";
    assert(formatStatus(json, sizeof(json), s, "OK2ZAW-30", "0123456789ABCDEF") > 0);
    std::cout << json << '\n';

    struct Settings {
        char node_call[10] = "XX0XXX-00";
        int node_sset2 = 0x0001;
        int node_netmode = 0;
        int untouched = 77;
    } settings;
    applyLocalProfile(settings);
    assert(strcmp(settings.node_call, "OK2ZAW-30") == 0);
    assert(settings.node_sset2 == 0x0041); // Enables web without clearing other bits.
    assert(settings.node_netmode == 1);
    assert(settings.untouched == 77);
}
