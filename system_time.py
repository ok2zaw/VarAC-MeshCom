"""System clock settings (time zone, NTP, manual time) for the web UI.

Time zone, NTP on/off and the clock go through `timedatectl` (systemd-timedated
over D-Bus); the polkit rule in polkit/ lets the service user do that without
sudo. NTP servers live in a root-owned timesyncd drop-in, so the web UI only
writes a request file that the root helper scripts/meshcom-ntp applies
(systemd path unit, same pattern as meshcom-restart).
"""

import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
NTP_REQUEST = REPO_DIR / ".meshcom-ntp-request"
NTP_HELPER = "/usr/local/sbin/meshcom-ntp"
NTP_PATH_UNIT = "meshcom-ntp.path"
MAX_NTP_SERVERS = 8
NTP_SERVER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|[0-9A-Fa-f:]{2,39}")
MANUAL_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


class TimeError(RuntimeError):
    pass


def _run(arguments: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(arguments, check=False, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TimeError(f"{arguments[0]} se nepodařilo spustit: {exc}") from exc


def _timedatectl(*arguments: str) -> str:
    result = _run(["timedatectl", "--no-ask-password", *arguments])
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"kód {result.returncode}"
        if "interactive authentication required" in detail.lower() or "access denied" in detail.lower():
            detail += " – chybí polkit pravidlo pro uživatele webu (INSTALL, kapitola 9.5)."
        raise TimeError(detail)
    return result.stdout


def _properties(output: str) -> dict:
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def status() -> dict:
    """Current clock state; `error` is set when timedatectl is unavailable."""
    state = {"now": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
             "utc_now": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"), "error": None,
             "timezone": None, "ntp": None, "synchronized": None, "can_ntp": None,
             "server": None, "servers": "", "fallback_servers": "", "helper_ready": ntp_helper_ready()}
    try:
        props = _properties(_timedatectl("show"))
    except TimeError as exc:
        state["error"] = str(exc)
        return state
    state.update(timezone=props.get("Timezone") or None, ntp=props.get("NTP") == "yes",
                 synchronized=props.get("NTPSynchronized") == "yes", can_ntp=props.get("CanNTP") == "yes")
    try:
        sync = _properties(_timedatectl("show-timesync", "--all"))
    except TimeError:
        sync = {}  # timesyncd not running (e.g. chrony) – servers are then managed elsewhere
    state.update(server=sync.get("ServerName") or None, servers=sync.get("SystemNTPServers", ""),
                 fallback_servers=sync.get("FallbackNTPServers", ""))
    return state


def timezones() -> list[str]:
    try:
        zones = _timedatectl("list-timezones").split()
    except TimeError:
        zones = []
    if not zones:
        import zoneinfo
        zones = sorted(zoneinfo.available_timezones())
    return zones


def set_timezone(zone: str) -> None:
    zone = str(zone).strip()
    if zone not in timezones():
        raise TimeError(f"Neznámé časové pásmo {zone}.")
    _timedatectl("set-timezone", zone)
    time.tzset()  # running processes cache the zone; reload it for this one (the gateway needs a restart)


def set_ntp(enabled: bool) -> None:
    _timedatectl("set-ntp", "true" if enabled else "false")


def set_time(value: str) -> None:
    """Set the clock to local time "YYYY-MM-DD HH:MM[:SS]"; NTP has to be off."""
    value = str(value).strip().replace("T", " ")
    if len(value) == 16:
        value += ":00"
    try:
        datetime.strptime(value, MANUAL_TIME_FORMAT)
    except ValueError as exc:
        raise TimeError(f"Neplatný čas {value}; použijte RRRR-MM-DD HH:MM:SS.") from exc
    _timedatectl("set-time", value)


def parse_ntp_servers(raw: str) -> list[str]:
    servers = [item for item in re.split(r"[\s,]+", str(raw)) if item]
    if len(servers) > MAX_NTP_SERVERS:
        raise TimeError(f"Nejvýše {MAX_NTP_SERVERS} NTP serverů.")
    for server in servers:
        if not NTP_SERVER.fullmatch(server):
            raise TimeError(f"Neplatný NTP server {server}.")
    return servers


def ntp_helper_ready() -> bool:
    if not os.access(NTP_HELPER, os.X_OK):
        return False
    try:
        return _run(["systemctl", "is-active", "--quiet", NTP_PATH_UNIT]).returncode == 0
    except TimeError:
        return False


def request_ntp_servers(raw: str) -> list[str]:
    """Ask the root helper to use these servers (empty list = distribution defaults)."""
    servers = parse_ntp_servers(raw)
    if not ntp_helper_ready():
        raise TimeError(f"Helper {NTP_HELPER} nebo {NTP_PATH_UNIT} není nainstalovaný (INSTALL, kapitola 9.5).")
    tmp_path = NTP_REQUEST.with_suffix(".tmp")
    try:
        tmp_path.write_text(" ".join(servers) + "\n", encoding="ascii")
        os.replace(tmp_path, NTP_REQUEST)
    except OSError as exc:
        raise TimeError(f"Požadavek na NTP servery se nepodařilo zapsat: {exc}") from exc
    return servers
