"""
TCP ports opened by the programs the web UI configures, and clashes between them.

Each tab saves its own settings, but the ports share one machine: a KISS
port set on the APRS tab must not take the LinBPQ web port from the BPQ
tab. The modems VARA, XARA and Mercury are alternatives that are expected
to share 8300; radio_apps.port_conflicts warns when two of them would run
together, so they do not count as a clash here. Clashes inside one tab are
checked by that tab's own validation, so only clashes between tabs count.
"""

import bpq_services
import radio_apps

WEB_PORT = 8899          # waitress in systemd/meshcom-web.service
DIREWOLF_AGW_PORT = 8000  # Dire Wolf default; aprs_rx leaves it on


def _int(cfg: dict, key: str, default: int = 0) -> int:
    try:
        return int(cfg.get(key, default))
    except (TypeError, ValueError):
        return 0


def tcp_ports(cfg: dict) -> list[tuple[int, str, str, str]]:
    """(port, owner label, section, modem) for every TCP port in use; modem is "" unless a VARA-like modem."""
    ports = [(WEB_PORT, "web gateway", "web", "")]
    modems = {"vara": (radio_apps.VARA_PORT, "VARA"), "xara": (radio_apps.xara_port(cfg), "XARA"),
              "mercury": (radio_apps.mercury_port(cfg), "Mercury")}
    for name, (port, label) in modems.items():
        ports += [(port, f"{label} (příkazy)", "radio", name), (port + 1, f"{label} (data)", "radio", name)]
    ports.append((radio_apps.XARA_KISS_PORT, "XARA KISS", "radio", "xara"))
    if radio_apps.xara_ui_port(cfg):
        ports.append((radio_apps.xara_ui_port(cfg), "web XARA", "radio", ""))
    if cfg.get("radio_mercury_gui"):
        ports.append((radio_apps.mercury_gui_port(cfg), "GUI Mercury", "radio", ""))
    ports.append((radio_apps.rigctld_port(cfg), "rigctld", "radio", ""))
    for tnc in bpq_services.TNCS:
        if cfg.get(f"{tnc}_enabled"):
            ports.append((_int(cfg, f"{tnc}_tcp_port"), f"{bpq_services.SERVICES[tnc]['label']} KISS", "bpq", ""))
    ports += [(_int(cfg, "linbpq_web_port"), "web LinBPQ", "bpq", ""),
              (_int(cfg, "bpq_telnet_port"), "telnet LinBPQ", "bpq", "")]
    if cfg.get("aprs_enabled"):
        ports += [(_int(cfg, "aprs_kiss_port"), "Dire Wolf KISS (APRS)", "aprs", ""),
                  (DIREWOLF_AGW_PORT, "Dire Wolf AGW (APRS)", "aprs", "")]
    return [item for item in ports if item[0]]


def conflicts(cfg: dict, section: str | None = None) -> list[str]:
    """Messages for ports used by two owners; only clashes touching `section` when given."""
    by_port = {}
    for port, label, owner_section, modem in tcp_ports(cfg):
        by_port.setdefault(port, []).append((label, owner_section, modem))
    messages = []
    for port, owners in sorted(by_port.items()):
        if len({owner[1] for owner in owners}) < 2 or all(owner[2] for owner in owners):
            continue  # one tab only (checked there), or shared only by alternative modems
        if section and not any(owner[1] == section for owner in owners):
            continue
        messages.append(f"Port {port} používají zároveň: {', '.join(owner[0] for owner in owners)}.")
    return messages
