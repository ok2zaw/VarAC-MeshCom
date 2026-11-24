#pragma once
#include <cstdio>

// This file is compiled into the application, outside the InternalFS volume.
// Recovery uses Ethernet DHCP and starts the node's web configuration server.
// Groups, position and the ExtUDP destination retain upstream defaults.
// The same profile is applied after an explicit factory reset.
constexpr char LocalProfileId[] = "OK2ZAW-30-v2";

template <typename Settings>
void applyLocalProfile(Settings &s) {
    static_assert(sizeof(s.node_call) >= sizeof("OK2ZAW-30"), "Callsign does not fit");
    snprintf(s.node_call, sizeof(s.node_call), "%s", "OK2ZAW-30");
    s.node_sset2 |= 0x0040; // Same persisted webserver bit as --webserver on.
    s.node_netmode = 1;     // Ethernet; empty static IP fields select DHCP on RAK.
}
