#pragma once
#include <stdint.h>

#if defined(MC_RF_MONITOR) && MC_RF_MONITOR
void rfMonitorBegin();
void rfMonitorCapture(const uint8_t *data, uint16_t len, int16_t rssi, int8_t snr);
// Called only by the main loop, while it owns the Ethernet SPI guard.
void rfMonitorPoll();
#else
inline void rfMonitorBegin() {}
inline void rfMonitorCapture(const uint8_t *, uint16_t, int16_t, int8_t) {}
inline void rfMonitorPoll() {}
#endif
