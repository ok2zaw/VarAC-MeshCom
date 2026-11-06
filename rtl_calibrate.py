#!/usr/bin/env python3
"""
Calibrate the RTL-SDR crystal against GSM base stations with kalibrate-rtl (`kal`).

GSM base stations keep their frequency within 0.05 ppm, so the offset kal
measures on one of them is the dongle's own error. The web UI starts this
file as a transient systemd user unit (`rtl_calibrate.py run`), because a
band scan takes minutes and the dongle must be free: APRS reception is
stopped for the run and started again afterwards. Progress and the result
go to a small JSON state file the APRS tab polls.

kal reports the absolute error including the correction passed with -e,
so the result is the value for `rtl_fm -p`.
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

import common
from radio_apps import RadioError, is_active, systemctl, _run

UNIT = "meshcom-rtl-calibrate.service"
STATE_FILE = os.path.join(os.path.expanduser("~"), ".config", "meshcom", "rtl-calibration.json")
KAL_TIMEOUT = 900
BANDS = {
    "GSM900": "GSM 900 (935–960 MHz)",
    "EGSM": "E-GSM 900 (925–960 MHz)",
    "DCS": "DCS 1800 (1805–1880 MHz)",
    "GSM-R": "GSM-R železnice (921–925 MHz)",
    "GSM850": "GSM 850 (Amerika)",
    "PCS": "PCS 1900 (Amerika)",
}
RUNNING = ("starting", "stopping-aprs", "scanning", "measuring")
STATE_LABELS = {
    "idle": "zatím neproběhla",
    "starting": "spouští se",
    "stopping-aprs": "zastavuji APRS příjem",
    "scanning": "hledám základnové stanice GSM",
    "measuring": "měřím odchylku",
    "done": "hotovo",
    "error": "chyba",
}

# "    chan:   37 (942.4MHz - 13.234kHz)    power:  123456.78"
_SCAN_LINE = re.compile(r"chan:\s*(\d+)\s*\(([\d.]+)MHz\s*([+-])\s*([\d.]+)(k?Hz)\)\s*power:\s*([\d.]+)")
_PPM_LINE = re.compile(r"average absolute error:\s*([-+]?[\d.]+)\s*ppm")


def parse_scan(text: str) -> list[dict]:
    channels = []
    for match in _SCAN_LINE.finditer(text):
        channel, freq, sign, offset, unit, power = match.groups()
        offset_hz = float(offset) * (1000 if unit == "kHz" else 1) * (-1 if sign == "-" else 1)
        channels.append({"channel": int(channel), "freq_mhz": float(freq),
                         "offset_hz": round(offset_hz), "power": float(power)})
    return channels


def parse_ppm(text: str) -> float | None:
    match = _PPM_LINE.search(text)
    return float(match.group(1)) if match else None


def read_state(path: str | None = None) -> dict:
    try:
        with open(path or STATE_FILE, encoding="utf-8") as handle:
            state = json.load(handle)
        return state if isinstance(state, dict) else {"state": "idle"}
    except (OSError, ValueError):
        return {"state": "idle"}


def write_state(state: dict, path: str | None = None) -> None:
    path = path or STATE_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False)
    os.replace(tmp_path, path)


def status() -> dict:
    """State for the web UI; a run whose unit is gone was interrupted."""
    state = read_state()
    try:
        state["active"] = is_active(UNIT)
    except RadioError:  # user systemd unreachable: show the last saved state
        state["active"] = False
    if state.get("state") in RUNNING and not state["active"] and time.time() - state.get("updated_at", 0) > 15:
        state.update(state="error", error="Kalibrace byla přerušena.")
    state["label"] = STATE_LABELS.get(state.get("state"), state.get("state"))
    return state


def kal_options(cfg: dict) -> list[str]:
    """Device, gain and starting correction shared by the scan and the measurement."""
    device = str(cfg.get("aprs_device", "0")).strip() or "0"
    if not device.isdigit():
        raise RadioError("Kalibrace: kal vybírá RTL-SDR jen podle indexu (0, 1…), ne podle sériového čísla.")
    options = ["-d", device]
    gain = str(cfg.get("rtl_cal_gain", "")).strip()
    if gain:
        options += ["-g", gain]
    try:
        ppm = int(cfg.get("aprs_ppm", 0) or 0)
    except (TypeError, ValueError):
        ppm = 0
    if ppm:
        options += ["-e", str(ppm)]  # start near the known error; kal adds it back into the result
    return options


def validate(band: str, channel: str = "", gain: str = "") -> None:
    if band not in BANDS:
        raise RadioError("Kalibrace: neznámé pásmo.")
    if channel and not (channel.isdigit() and int(channel) <= 1023):
        raise RadioError("Kalibrace: kanál (ARFCN) je číslo 0–1023, nebo prázdné pro nejsilnější nalezený.")
    if gain:
        try:
            if not 0 <= float(gain) <= 50:
                raise ValueError
        except ValueError:
            raise RadioError("Kalibrace: zisk musí být 0–50 dB.") from None


def start(cfg: dict, band: str, channel: str = "") -> None:
    """Launch the calibration as a transient user unit; raises RadioError."""
    validate(band, channel, str(cfg.get("rtl_cal_gain", "")).strip())
    if shutil.which("kal") is None:
        raise RadioError("Program kal (kalibrate-rtl) není nainstalovaný – viz INSTALL, kapitola 13.4.")
    kal_options(cfg)  # report a serial-number device before anything is stopped
    if is_active(UNIT):
        raise RadioError("Kalibrace už běží.")
    write_state({"state": "starting", "band": band, "channel": int(channel) if channel else None,
                 "started_at": time.time(), "updated_at": time.time()})
    systemctl("reset-failed", UNIT, check=False)
    arguments = [sys.executable, "-u", os.path.abspath(__file__), "run", "--band", band]
    if channel:
        arguments += ["--channel", channel]
    _run(["systemd-run", "--user", "--collect", f"--unit={UNIT}", "--description=MeshCom RTL-SDR calibration",
          f"--working-directory={common.BASE_DIR}", "--", *arguments])


def stop() -> None:
    systemctl("stop", UNIT, check=False)


def _kal(arguments: list[str], log: list[str]) -> str:
    result = subprocess.run(["kal", *arguments], capture_output=True, text=True, timeout=KAL_TIMEOUT)
    output = (result.stdout or "") + (result.stderr or "")
    log.extend(line for line in output.replace("\r", "\n").splitlines() if line.strip())
    del log[:-60]
    if result.returncode and parse_ppm(output) is None and not parse_scan(output):
        raise RadioError(f"kal skončil s kódem {result.returncode}: " + " ".join(log[-3:]))
    return output


def run(band: str, channel: int | None = None) -> dict:
    """The calibration itself (runs inside the transient unit)."""
    import aprs_rx

    def terminate(*_):
        raise SystemExit(143)  # run the finally block: APRS must come back

    signal.signal(signal.SIGTERM, terminate)
    cfg = common.load_config()
    state = read_state()
    state.update(state="starting", band=band, channel=channel, ppm=None, error=None, channels=[], log=[],
                 started_at=state.get("started_at", time.time()))

    def save(**changes):
        state.update(changes, updated_at=time.time())
        write_state(state)

    aprs_was_running = is_active(aprs_rx.UNIT)
    try:
        options = kal_options(cfg)
        if aprs_was_running:
            save(state="stopping-aprs")
            systemctl("stop", aprs_rx.UNIT, check=False)
            time.sleep(2)
        if channel is None:
            save(state="scanning")
            channels = parse_scan(_kal(["-s", band, *options], state["log"]))
            if not channels:
                raise RadioError("V pásmu nebyla nalezena žádná základnová stanice GSM – zkuste jiné pásmo "
                                 "nebo vyšší zisk.")
            channel = max(channels, key=lambda item: item["power"])["channel"]
            save(channels=sorted(channels, key=lambda item: -item["power"])[:12], channel=channel)
        save(state="measuring", channel=channel)
        ppm = parse_ppm(_kal(["-c", str(channel), "-b", band, *options], state["log"]))
        if ppm is None:
            raise RadioError("kal nevypsal výsledek – kanál je asi příliš slabý; zkuste jiný.")
        save(state="done", ppm=ppm, finished_at=time.time())
    except (RadioError, OSError, subprocess.TimeoutExpired) as exc:
        save(state="error", error=str(exc), finished_at=time.time())
    except SystemExit:
        save(state="error", error="Kalibrace byla zastavena.", finished_at=time.time())
        raise
    finally:
        if aprs_was_running:
            systemctl("start", "--no-block", aprs_rx.UNIT, check=False)
    return state


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--band", required=True, choices=list(BANDS))
    parser.add_argument("--channel", type=int)
    arguments = parser.parse_args(argv)
    state = run(arguments.band, arguments.channel)
    print(json.dumps({key: state.get(key) for key in ("state", "band", "channel", "ppm", "error")}))
    return 0 if state.get("state") == "done" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
