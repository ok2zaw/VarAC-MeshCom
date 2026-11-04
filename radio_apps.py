#!/usr/bin/env python3
"""
Start and stop the HF modem stack (VARA, Mercury, VarAC) from the web UI.

Every program runs as a transient systemd *user* unit (systemd-run --user),
so it survives restarts of the web service and can be stopped cleanly with
its whole process tree. Wine programs share one persistent wineserver unit;
otherwise the wineserver would live inside the first program's unit and
stopping VARA would also kill VarAC.

Run `radio_apps.py autostart` from the desktop session (see
AUTOSTART_FILE) to start the programs enabled for autostart, each after its
configured delay.
"""

import getpass
import os
import shlex
import shutil
import subprocess
import sys
import time

import common

UNIT_PREFIX = "meshcom-radio-"
WINESERVER_UNIT = f"{UNIT_PREFIX}wineserver.service"
AUTOSTART_FILE = os.path.join(os.path.expanduser("~"), ".config", "autostart", "meshcom-radio.desktop")
MAX_DELAY_SECONDS = 600
DEFAULT_MODEM_PORT = 8300
DEFAULT_MERCURY_GUI_PORT = 10088
# Ports >= 1024 that Chrome and Firefox refuse to open (ERR_UNSAFE_PORT, Fetch "bad port" list).
BROWSER_BLOCKED_PORTS = frozenset({1719, 1720, 1723, 2049, 3659, 4045, 4190, 5060, 5061, 6000, 6566,
                                   6665, 6666, 6667, 6668, 6669, 6679, 6697, 10080})

APPS = {
    "vara": {"label": "VARA", "kind": "wine", "path_key": "radio_vara_path"},
    "mercury": {"label": "Mercury", "kind": "native"},
    "varac": {"label": "VarAC", "kind": "wine", "path_key": "radio_varac_path"},
}
WINE_APPS = tuple(app for app, spec in APPS.items() if spec["kind"] == "wine")


class RadioError(RuntimeError):
    pass


def unit_name(app: str) -> str:
    if app not in APPS:
        raise RadioError(f"Neznámý program: {app}")
    return f"{UNIT_PREFIX}{app}.service"


def _user_env() -> dict:
    # The web UI runs as a system service without a session; point systemctl
    # at the user manager of the same account.
    env = dict(os.environ)
    if not hasattr(os, "getuid"):
        return env
    runtime_dir = f"/run/user/{os.getuid()}"
    env.setdefault("XDG_RUNTIME_DIR", runtime_dir)
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime_dir}/bus")
    return env


def _run(arguments: list[str], check: bool = True, timeout: int = 20) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            arguments, check=False, capture_output=True, text=True,
            timeout=timeout, env=_user_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RadioError(f"Příkaz {arguments[0]} se nepodařilo spustit: {exc}") from exc
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        if "Failed to connect to bus" in detail or "No medium found" in detail:
            detail = ("Uživatelský systemd neběží. Přihlaste se do desktopu nebo "
                      f"spusťte `sudo loginctl enable-linger {getpass.getuser()}`.")
        raise RadioError(detail or f"{arguments[0]} skončil s kódem {result.returncode}.")
    return result


def systemctl(*arguments: str, check: bool = True) -> subprocess.CompletedProcess:
    return _run(["systemctl", "--user", *arguments], check=check)


def _mercury_option(cfg: dict, flag: str, default: int) -> int:
    """Integer value of a Mercury option such as `-p 8400` or `-p8400`."""
    try:
        arguments = shlex.split(str(cfg.get("radio_mercury_args", "")))
    except ValueError:
        return default
    for index, argument in enumerate(arguments):
        value = None
        if argument == flag and index + 1 < len(arguments):
            value = arguments[index + 1]
        elif argument.startswith(flag) and len(argument) > 2:
            value = argument[2:]
        if value is not None:
            try:
                return int(value)
            except ValueError:
                return default
    return default


def mercury_port(cfg: dict) -> int:
    return _mercury_option(cfg, "-p", DEFAULT_MODEM_PORT)


def mercury_gui_port(cfg: dict) -> int:
    """WebSocket/HTTP port of the Mercury web GUI (-U overrides the setting)."""
    try:
        configured = int(cfg.get("radio_mercury_gui_port", DEFAULT_MERCURY_GUI_PORT))
    except (TypeError, ValueError):
        configured = DEFAULT_MERCURY_GUI_PORT
    if configured in BROWSER_BLOCKED_PORTS:
        configured = DEFAULT_MERCURY_GUI_PORT
    return _mercury_option(cfg, "-U", configured)


def app_command(app: str, cfg: dict) -> list[str]:
    spec = APPS[app]
    if spec["kind"] == "wine":
        path = str(cfg.get(spec["path_key"], "")).strip()
        if not path.lower().endswith(".exe"):
            raise RadioError(f"{spec['label']}: cesta musí končit na .exe.")
        if not os.path.isfile(path):
            raise RadioError(f"{spec['label']}: soubor {path} neexistuje.")
        return ["wine", path]

    command = str(cfg.get("radio_mercury_command", "")).strip() or "mercury"
    if os.path.basename(command) != "mercury":
        raise RadioError("Mercury: příkaz musí být program mercury nebo cesta k němu.")
    if shutil.which(command) is None:
        raise RadioError(f"Mercury: program {command} nebyl nalezen.")
    try:
        arguments = shlex.split(str(cfg.get("radio_mercury_args", "")))
    except ValueError as exc:
        raise RadioError(f"Mercury: neplatné parametry: {exc}") from exc
    if cfg.get("radio_mercury_gui"):
        # -G serves the built-in web GUI (spectrum, waterfall, status) and
        # its WebSocket on the -U port.
        if "-G" not in arguments:
            arguments.append("-G")
        if not any(argument.startswith("-U") for argument in arguments):
            arguments += ["-U", str(mercury_gui_port(cfg))]
        if cfg.get("radio_mercury_no_waterfall") and "-W" not in arguments:
            arguments.append("-W")
    return [command, *arguments]


def _environment(cfg: dict) -> list[str]:
    home = os.path.expanduser("~")
    values = {
        "WINEPREFIX": str(cfg.get("radio_wine_prefix", "")).strip() or os.path.join(home, ".wine"),
        "WINEDEBUG": "-all",
        "BOX86_DYNAREC_BIGBLOCK": "0",
        "BOX64_DYNAREC_STRONGMEM": "1",
        "MESA_GL_VERSION_OVERRIDE": "3.2",
    }
    display = str(cfg.get("radio_display", "")).strip()
    if display:
        values["DISPLAY"] = display
    xauthority = os.path.join(home, ".Xauthority")
    if os.path.isfile(xauthority):
        values["XAUTHORITY"] = xauthority
    return [f"{key}={value}" for key, value in values.items()]


def _launch(unit: str, description: str, command: list[str], cfg: dict) -> None:
    systemctl("reset-failed", unit, check=False)
    arguments = ["systemd-run", "--user", "--collect", f"--unit={unit}",
                 f"--description={description}"]
    for item in _environment(cfg):
        arguments.append(f"--setenv={item}")
    _run([*arguments, "--", *command])


def is_active(unit: str) -> bool:
    return systemctl("is-active", "--quiet", unit, check=False).returncode == 0


def _ensure_wineserver(cfg: dict) -> None:
    if is_active(WINESERVER_UNIT) or shutil.which("wineserver") is None:
        return
    _launch(WINESERVER_UNIT, "MeshCom shared wineserver",
            ["wineserver", "--foreground", "--persistent"], cfg)
    time.sleep(1)


def unit_status(unit: str) -> dict:
    result = {"state": "unknown", "sub_state": "", "since": "", "enabled": "", "error": None}
    try:
        output = systemctl(
            "show", unit, "--property=ActiveState,SubState,ActiveEnterTimestamp,UnitFileState",
        ).stdout
    except RadioError as exc:
        result["error"] = str(exc)
        return result
    properties = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    result["state"] = properties.get("ActiveState", "unknown")
    result["sub_state"] = properties.get("SubState", "")
    result["enabled"] = properties.get("UnitFileState", "")
    if result["state"] == "active":
        result["since"] = properties.get("ActiveEnterTimestamp", "")
    return result


def status(app: str) -> dict:
    return unit_status(unit_name(app))


def start(app: str, cfg: dict | None = None) -> bool:
    """Start the program; returns False if it was already running."""
    cfg = cfg or common.load_config()
    unit = unit_name(app)
    if is_active(unit):
        return False
    command = app_command(app, cfg)
    if APPS[app]["kind"] == "wine":
        _ensure_wineserver(cfg)
    _launch(unit, f"MeshCom {APPS[app]['label']}", command, cfg)
    return True


def stop(app: str) -> None:
    systemctl("stop", unit_name(app))
    if APPS[app]["kind"] == "wine" and not any(is_active(unit_name(item)) for item in WINE_APPS):
        systemctl("stop", WINESERVER_UNIT, check=False)


def restart(app: str, cfg: dict | None = None) -> None:
    stop(app)
    start(app, cfg)


def unit_log(unit: str, lines: int = 15) -> str:
    try:
        result = _run(
            ["journalctl", "--user", "-u", unit, "-n", str(lines),
             "--no-pager", "-o", "short-iso"],
            check=False,
        )
    except RadioError:
        return ""
    return result.stdout.strip()


def recent_log(app: str, lines: int = 15) -> str:
    return unit_log(unit_name(app), lines)


def autostart_plan(cfg: dict) -> list[tuple[int, str]]:
    plan = []
    for app in APPS:
        if cfg.get(f"radio_{app}_autostart"):
            try:
                delay = int(cfg.get(f"radio_{app}_delay", 0))
            except (TypeError, ValueError):
                delay = 0
            plan.append((max(0, min(delay, MAX_DELAY_SECONDS)), app))
    return sorted(plan, key=lambda item: (item[0], list(APPS).index(item[1])))


def _desktop_quote(argument: str) -> str:
    # Desktop Entry Exec keys accept only double quotes with these escapes.
    escaped = "".join(f"\\{char}" if char in '"`$\\' else char for char in argument)
    return f'"{escaped}"'


def sync_autostart_entry(cfg: dict) -> bool:
    """Create or remove the desktop autostart entry; returns True if present."""
    if not autostart_plan(cfg):
        if os.path.exists(AUTOSTART_FILE):
            os.remove(AUTOSTART_FILE)
        return False

    script = os.path.abspath(__file__)
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=MeshCom VARA/Mercury/VarAC\n"
        "Comment=Starts the HF modem programs enabled in the MeshCom web UI\n"
        f"Exec={_desktop_quote(sys.executable)} {_desktop_quote(script)} autostart\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
    os.makedirs(os.path.dirname(AUTOSTART_FILE), exist_ok=True)
    tmp_path = AUTOSTART_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.replace(tmp_path, AUTOSTART_FILE)
    return True


def run_autostart(cfg: dict | None = None, sleep=time.sleep, clock=time.monotonic) -> None:
    cfg = cfg or common.load_config()
    session_vars = [name for name in ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY") if os.environ.get(name)]
    if session_vars:
        systemctl("import-environment", *session_vars, check=False)

    started_at = clock()
    for delay, app in autostart_plan(cfg):
        remaining = started_at + delay - clock()
        if remaining > 0:
            sleep(remaining)
        try:
            started = start(app, cfg)
            print(f"{APPS[app]['label']}: {'spuštěno' if started else 'již běží'}", flush=True)
        except RadioError as exc:
            print(f"{APPS[app]['label']}: {exc}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    if sys.argv[1:] != ["autostart"]:
        sys.exit(f"Použití: {sys.argv[0]} autostart")
    run_autostart()
