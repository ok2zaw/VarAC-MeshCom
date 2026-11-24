#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cstdarg>

// Hardware-independent capture queue and wire format. One consumer owns pop().
// Producers never wait: contention and full queues are counted as lost events.
namespace rf_monitor {
constexpr size_t MaxPayload = 255;
constexpr uint32_t QueueSlots = 8;

struct Frame {
    uint32_t seq = 0;
    uint32_t ms = 0;
    uint16_t len = 0;
    int16_t rssi = 0;
    int8_t snr = 0;
    uint8_t data[MaxPayload] = {};
};

class Queue {
    Frame frames_[QueueSlots];
    std::atomic<uint32_t> head_{0}, tail_{0}, seen_{0}, dropped_{0}, suppressed_{0};
    std::atomic<uint32_t> high_water_{0};
    std::atomic_flag writing_ = ATOMIC_FLAG_INIT;
public:
    bool push(const uint8_t *data, size_t len, int16_t rssi, int8_t snr,
              uint32_t ms, bool suppressed = false) {
        const uint32_t seq = seen_.fetch_add(1, std::memory_order_relaxed);
        if (suppressed) {
            suppressed_.fetch_add(1, std::memory_order_relaxed);
            return false;
        }
        if (!data || len == 0 || len > MaxPayload) {
            dropped_.fetch_add(1, std::memory_order_relaxed);
            return false;
        }
        if (writing_.test_and_set(std::memory_order_acquire)) {
            dropped_.fetch_add(1, std::memory_order_relaxed);
            return false;
        }
        const uint32_t head = head_.load(std::memory_order_relaxed);
        const uint32_t depth = head - tail_.load(std::memory_order_acquire);
        if (depth >= QueueSlots) {
            dropped_.fetch_add(1, std::memory_order_relaxed);
            writing_.clear(std::memory_order_release);
            return false;
        }
        Frame &f = frames_[head % QueueSlots];
        f.seq = seq; f.ms = ms; f.len = static_cast<uint16_t>(len);
        f.rssi = rssi; f.snr = snr;
        memcpy(f.data, data, len);
        if (depth + 1 > high_water_.load(std::memory_order_relaxed))
            high_water_.store(depth + 1, std::memory_order_relaxed);
        head_.store(head + 1, std::memory_order_release);
        writing_.clear(std::memory_order_release);
        return true;
    }
    bool pop(Frame &f) {
        const uint32_t tail = tail_.load(std::memory_order_relaxed);
        if (tail == head_.load(std::memory_order_acquire)) return false;
        f = frames_[tail % QueueSlots];
        tail_.store(tail + 1, std::memory_order_release);
        return true;
    }
    uint32_t seen() const { return seen_.load(std::memory_order_relaxed); }
    uint32_t dropped() const { return dropped_.load(std::memory_order_relaxed); }
    uint32_t suppressed() const { return suppressed_.load(std::memory_order_relaxed); }
    void discardSuppressed() {
        Frame f;
        while (pop(f)) suppressed_.fetch_add(1, std::memory_order_relaxed);
    }
    uint32_t depth() const { return head_.load() - tail_.load(); }
    uint32_t highWater() const { return high_water_.load(std::memory_order_relaxed); }
};

class JsonWriter {
    char *out_;
    size_t cap_, size_ = 0;
    bool ok_;
public:
    JsonWriter(char *out, size_t cap) : out_(out), cap_(cap), ok_(out && cap > 0) {
        if (ok_) out_[0] = 0;
    }
    void append(const char *fmt, ...) {
        if (!ok_) return;
        va_list args;
        va_start(args, fmt);
        const int n = vsnprintf(out_ + size_, cap_ - size_, fmt, args);
        va_end(args);
        if (n < 0 || static_cast<size_t>(n) >= cap_ - size_) { ok_ = false; return; }
        size_ += static_cast<size_t>(n);
    }
    void string(const char *s) {
        append("\"");
        if (s) for (const unsigned char *p = reinterpret_cast<const unsigned char *>(s); *p; ++p) {
            if (*p == '"' || *p == '\\') append("\\%c", *p);
            else if (*p < 32) append("\\u%04x", static_cast<unsigned>(*p));
            else append("%c", *p);
        }
        append("\"");
    }
    size_t finish() {
        if (!ok_) { if (out_ && cap_) out_[0] = 0; return 0; }
        return size_;
    }
};

inline void identity(JsonWriter &w, const char *type, const char *node, const char *boot) {
    w.append("{\"monitor_version\":1,\"type\":"); w.string(type);
    w.append(",\"node\":"); w.string(node);
    w.append(",\"boot_id\":"); w.string(boot);
}

inline size_t formatRx(char *out, size_t cap, const Frame &f, const char *node, const char *boot) {
    if (f.len > MaxPayload) { if (out && cap) out[0] = 0; return 0; }
    JsonWriter w(out, cap);
    identity(w, "rf_rx", node, boot);
    w.append(",\"event_seq\":%lu,\"uptime_ms\":%lu,\"len\":%u,\"rssi\":%d,\"snr\":%d,\"raw_hex\":\"",
             static_cast<unsigned long>(f.seq), static_cast<unsigned long>(f.ms),
             static_cast<unsigned>(f.len), static_cast<int>(f.rssi), static_cast<int>(f.snr));
    for (size_t i = 0; i < f.len; ++i) w.append("%02X", static_cast<unsigned>(f.data[i]));
    w.append("\"}");
    return w.finish();
}

struct Status {
    uint32_t ms = 0, heap = 0, reset_reason = 0;
    uint32_t seen = 0, dropped = 0, suppressed = 0, depth = 0, high_water = 0;
    uint32_t udp_failures = 0, format_failures = 0;
    bool rx_enabled = false;
    const char *firmware = "", *config_source = "unknown", *profile = "upstream";
};

inline size_t formatStatus(char *out, size_t cap, const Status &s, const char *node, const char *boot) {
    JsonWriter w(out, cap);
    identity(w, "node_status", node, boot);
    w.append(",\"uptime_ms\":%lu,\"heap_bytes\":%lu,\"reset_reason\":%lu,\"rx_seen\":%lu,\"rx_dropped\":%lu,\"rx_suppressed\":%lu,\"queue_depth\":%lu,\"queue_high_water\":%lu,\"udp_failures\":%lu,\"format_failures\":%lu,\"rx_enabled\":%s",
        static_cast<unsigned long>(s.ms), static_cast<unsigned long>(s.heap),
        static_cast<unsigned long>(s.reset_reason), static_cast<unsigned long>(s.seen),
        static_cast<unsigned long>(s.dropped), static_cast<unsigned long>(s.suppressed),
        static_cast<unsigned long>(s.depth), static_cast<unsigned long>(s.high_water),
        static_cast<unsigned long>(s.udp_failures), static_cast<unsigned long>(s.format_failures),
        s.rx_enabled ? "true" : "false");
    w.append(",\"firmware\":"); w.string(s.firmware);
    w.append(",\"config_source\":"); w.string(s.config_source);
    w.append(",\"profile\":"); w.string(s.profile);
    w.append("}");
    return w.finish();
}
} // namespace rf_monitor
