"""
Manage two LoRa KISS TNCs (TNC1, TNC2) and LinBPQ as systemd user services.

Unlike the HF modem programs (radio_apps.py), these run without a desktop,
so the web UI writes regular unit files to ~/.config/systemd/user and uses
`systemctl --user enable` for start at boot (requires lingering for the
account: `sudo loginctl enable-linger ok2zaw`). The configured delay becomes an
ExecStartPre sleep; LinBPQ is ordered after the TNCs so their KISS TCP
ports are already listening.

Both TNCs run the bundled lora_tnc/Start_lora-tnc.py; each gets its own
settings through a JSON file passed in LORA_TNC_CONFIG.
"""

import ipaddress
import json
import os

import common
from radio_apps import RadioError, is_active, systemctl, unit_log, unit_status

UNIT_DIR = os.path.join(os.path.expanduser("~"), ".config", "systemd", "user")
TNC_CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "meshcom")
TNC_SCRIPT = os.path.join(common.BASE_DIR, "lora_tnc", "Start_lora-tnc.py")
MAX_DELAY_SECONDS = 600

TNCS = ("tnc1", "tnc2")
SERVICES = {
    "tnc1": {"label": "TNC1", "unit": "meshcom-tnc1.service"},
    "tnc2": {"label": "TNC2", "unit": "meshcom-tnc2.service"},
    "linbpq": {"label": "LinBPQ", "unit": "meshcom-linbpq.service"},
}
BANDWIDTHS = (7800, 10400, 15600, 20800, 31250, 41700, 62500, 125000, 250000, 500000)


def unit_name(service: str) -> str:
    if service not in SERVICES:
        raise RadioError(f"Neznámá služba: {service}")
    return SERVICES[service]["unit"]


def is_enabled(service: str, cfg: dict) -> bool:
    return service == "linbpq" or bool(cfg.get(f"{service}_enabled"))


def _integer(cfg: dict, key: str, label: str, minimum: int, maximum: int) -> int:
    try:
        value = int(str(cfg.get(key, "")).strip(), 0)
    except ValueError:
        raise RadioError(f"{label} musí být celé číslo.") from None
    if not minimum <= value <= maximum:
        raise RadioError(f"{label} musí být v rozsahu {minimum} až {maximum}.")
    return value


def tnc_settings(tnc: str, cfg: dict) -> dict:
    """Validated settings for lora_tnc/config.py; raises RadioError."""
    label = SERVICES[tnc]["label"]
    host = str(cfg.get(f"{tnc}_tcp_host", "")).strip()
    try:
        ipaddress.IPv4Address(host)
    except ValueError:
        raise RadioError(f"{label}: TCP adresa musí být IPv4, např. 127.0.0.1.") from None
    try:
        frequency_mhz = float(str(cfg.get(f"{tnc}_frequency_mhz", "")).replace(",", "."))
    except ValueError:
        raise RadioError(f"{label}: kmitočet musí být číslo v MHz.") from None
    if not 137 <= frequency_mhz <= 1020:
        raise RadioError(f"{label}: nastavte kmitočet v rozsahu 137 až 1020 MHz.")
    bandwidth = _integer(cfg, f"{tnc}_bandwidth", f"{label}: šířka pásma", 7800, 500000)
    if bandwidth not in BANDWIDTHS:
        raise RadioError(f"{label}: šířka pásma musí být jedna z {', '.join(map(str, BANDWIDTHS))} Hz.")
    return {
        "sx127x": True,
        "TCP_HOST": host,
        "TCP_PORT": _integer(cfg, f"{tnc}_tcp_port", f"{label}: TCP port", 1024, 65535),
        "busId": _integer(cfg, f"{tnc}_bus_id", f"{label}: SPI sběrnice", 0, 6),
        "csId": _integer(cfg, f"{tnc}_cs_id", f"{label}: SPI chip select", 0, 2),
        "irqPin": _integer(cfg, f"{tnc}_irq_pin", f"{label}: IRQ pin", 0, 27),
        "frequency": int(round(frequency_mhz * 1_000_000)),
        "spreadingFactor": _integer(cfg, f"{tnc}_spreading_factor", f"{label}: spreading factor", 6, 12),
        "bandwidth": bandwidth,
        "codingrate": _integer(cfg, f"{tnc}_coding_rate", f"{label}: coding rate", 5, 8),
        "preamble": _integer(cfg, f"{tnc}_preamble", f"{label}: preambule", 6, 65535),
        "outputPower": _integer(cfg, f"{tnc}_output_power", f"{label}: výkon", 0, 15),
        "sync_word": _integer(cfg, f"{tnc}_sync_word", f"{label}: sync word", 0, 0xFF),
        "appendSignalReport": False,
        "TX_OE_Style": False,
        "log_enable": False,
        "disp_en": False,
    }


def validate(cfg: dict) -> list[str]:
    """All configuration errors of enabled TNCs, including conflicts."""
    errors = []
    settings = {}
    for tnc in TNCS:
        if not is_enabled(tnc, cfg):
            continue
        try:
            settings[tnc] = tnc_settings(tnc, cfg)
        except RadioError as exc:
            errors.append(str(exc))
    if len(settings) == 2:
        first, second = settings["tnc1"], settings["tnc2"]
        if first["TCP_PORT"] == second["TCP_PORT"]:
            errors.append("TNC1 a TNC2 musí mít rozdílný TCP port.")
        if first["irqPin"] == second["irqPin"]:
            errors.append("TNC1 a TNC2 musí mít rozdílný IRQ pin.")
        if (first["busId"], first["csId"]) == (second["busId"], second["csId"]):
            errors.append("TNC1 a TNC2 nemohou sdílet stejnou SPI sběrnici i chip select.")
    if not str(cfg.get("linbpq_path", "")).strip():
        errors.append("Zadejte cestu k programu LinBPQ.")
    return errors


def _linbpq_command(cfg: dict) -> tuple[str, list[str]]:
    binary = str(cfg.get("linbpq_path", "")).strip()
    if not os.path.isabs(binary) or any(char.isspace() for char in binary):
        raise RadioError("LinBPQ: zadejte absolutní cestu bez mezer.")
    if not os.path.basename(binary).endswith("linbpq"):
        raise RadioError("LinBPQ: povolen je jen program linbpq (případně pilinbpq).")
    if not os.path.isfile(binary) or not os.access(binary, os.X_OK):
        raise RadioError(f"LinBPQ: soubor {binary} neexistuje nebo není spustitelný.")
    return os.path.dirname(binary), [binary]


def tnc_config_path(tnc: str) -> str:
    return os.path.join(TNC_CONFIG_DIR, f"{tnc}.json")


def _delay(cfg: dict, service: str) -> int:
    try:
        delay = int(cfg.get(f"{service}_delay", 0))
    except (TypeError, ValueError):
        delay = 0
    return max(0, min(delay, MAX_DELAY_SECONDS))


def unit_content(service: str, cfg: dict) -> str:
    lines = [
        "# Generated by the MeshCom gateway web UI (BPQ tab); changes are overwritten.",
        "[Unit]",
        f"Description=MeshCom {SERVICES[service]['label']}",
    ]
    if service == "linbpq":
        directory, command = _linbpq_command(cfg)
        lines.append("After=" + " ".join(unit_name(tnc) for tnc in TNCS))
        environment = []
    else:
        if " " in TNC_SCRIPT:
            raise RadioError("Cesta k projektu gateway nesmí obsahovat mezery.")
        directory = os.path.dirname(TNC_SCRIPT)
        command = ["/usr/bin/python3", "-u", TNC_SCRIPT]
        environment = [f"Environment=LORA_TNC_CONFIG={tnc_config_path(service)}"]
    lines += ["", "[Service]", "Type=simple", f"WorkingDirectory={directory}", *environment]
    delay = _delay(cfg, service)
    if delay:
        lines.append(f"ExecStartPre=/bin/sleep {delay}")
    lines += [
        f"ExecStart={' '.join(command)}",
        "Restart=on-failure",
        "RestartSec=10",
        f"TimeoutStartSec={delay + 90}",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ]
    return "\n".join(lines)


def _write_file(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.replace(tmp_path, path)


def write_units(cfg: dict) -> list[str]:
    """Write TNC settings and unit files; returns errors that were skipped."""
    errors = []
    removed = []
    for service, spec in SERVICES.items():
        unit_path = os.path.join(UNIT_DIR, spec["unit"])
        if not is_enabled(service, cfg):
            if os.path.exists(unit_path):
                systemctl("disable", "--now", spec["unit"], check=False)
                os.remove(unit_path)
                removed.append(spec["unit"])
            continue
        try:
            if service in TNCS:
                settings = tnc_settings(service, cfg)
                _write_file(tnc_config_path(service), json.dumps(settings, indent=2) + "\n")
            content = unit_content(service, cfg)
        except RadioError as exc:
            errors.append(str(exc))
            continue
        _write_file(unit_path, content)
    systemctl("daemon-reload")
    for service, spec in SERVICES.items():
        if spec["unit"] in removed or not os.path.exists(os.path.join(UNIT_DIR, spec["unit"])):
            continue
        action = "enable" if cfg.get(f"{service}_autostart") else "disable"
        systemctl(action, spec["unit"], check=False)
    return errors


def _require_unit_file(service: str) -> str:
    unit = unit_name(service)
    if not os.path.exists(os.path.join(UNIT_DIR, unit)):
        raise RadioError("Služba není vytvořená; povolte ji a uložte nastavení.")
    return unit


def start(service: str) -> bool:
    unit = _require_unit_file(service)
    if is_active(unit):
        return False
    systemctl("start", "--no-block", unit)
    return True


def stop(service: str) -> None:
    systemctl("stop", _require_unit_file(service))


def restart(service: str) -> None:
    systemctl("restart", "--no-block", _require_unit_file(service))


def status(service: str) -> dict:
    return unit_status(unit_name(service))


def recent_log(service: str, lines: int = 15) -> str:
    return unit_log(unit_name(service), lines)
