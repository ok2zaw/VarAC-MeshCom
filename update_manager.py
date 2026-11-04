"""Fast-forward-only application updates for the authenticated web UI."""

import logging
import os
import re
import subprocess
import threading
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
REMOTE = "origin"
BRANCH = "main"
REMOTE_REF = f"{REMOTE}/{BRANCH}"
RESTART_REQUEST = REPO_DIR / ".meshcom-restart-request"
EXPECTED_REMOTES = {
    "https://github.com/ok2zaw/VarAC-MeshCom.git",
    "git@github.com:ok2zaw/VarAC-MeshCom.git",
}
VERSION_PATTERN = re.compile(r"^\d+\.\d+(?:\.\d+)?$")
MANUAL_UPDATE_PATHS = ("requirements.txt", "systemd", "setup_rpi.sh", "scripts/meshcom-restart",
                       "scripts/meshcom-ntp", "polkit")
_UPDATE_LOCK = threading.Lock()
log = logging.getLogger("meshcom-updater")


class UpdateError(RuntimeError):
    pass


def _git(arguments: list[str], check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=REPO_DIR,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(f"Git se nepodařilo spustit nebo vypršel čas: {exc}") from exc

    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise UpdateError(detail or f"Git skončil s kódem {result.returncode}.")
    return result


def _read_version(path: Path) -> str:
    try:
        value = path.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise UpdateError(f"Nelze přečíst soubor verze {path.name}: {exc}") from exc
    if not VERSION_PATTERN.fullmatch(value):
        raise UpdateError(f"Neplatné číslo verze v {path.name}.")
    return value


def current_state() -> dict:
    return {
        "version": _read_version(REPO_DIR / "VERSION"),
        "commit": _git(["rev-parse", "--short=12", "HEAD"]).stdout.strip(),
        "branch": _git(["branch", "--show-current"]).stdout.strip(),
    }


def _changed_paths(*arguments: str) -> set[str]:
    return {
        line.strip()
        for line in _git(list(arguments)).stdout.splitlines()
        if line.strip()
    }


def check_for_updates() -> dict:
    state = current_state()
    if state["branch"] != BRANCH:
        raise UpdateError(f"Aktualizace jsou povolené pouze na větvi {BRANCH}.")

    remote_url = _git(["remote", "get-url", REMOTE]).stdout.strip().rstrip("/")
    if remote_url not in EXPECTED_REMOTES:
        raise UpdateError("Remote origin neukazuje na očekávaný repozitář projektu.")

    _git(["fetch", "--quiet", REMOTE], timeout=90)
    ancestor = _git(["merge-base", "--is-ancestor", "HEAD", REMOTE_REF], check=False)
    if ancestor.returncode:
        raise UpdateError("Lokální větev není předkem origin/main; automatický update není bezpečný.")

    ahead = int(_git(["rev-list", "--count", f"{REMOTE_REF}..HEAD"]).stdout.strip() or "0")
    behind = int(_git(["rev-list", "--count", f"HEAD..{REMOTE_REF}"]).stdout.strip() or "0")
    remote_changes = _changed_paths("diff", "--name-only", "HEAD", REMOTE_REF)
    local_changes = (
        _changed_paths("diff", "--name-only")
        | _changed_paths("diff", "--cached", "--name-only")
        | _changed_paths("ls-files", "--others", "--exclude-standard")
    )
    overlapping_changes = sorted(remote_changes & local_changes)
    manual_changes = sorted(
        path for path in remote_changes
        if path in MANUAL_UPDATE_PATHS or any(path.startswith(f"{item}/") for item in MANUAL_UPDATE_PATHS)
    )
    remote_version = _git(["show", f"{REMOTE_REF}:VERSION"], check=False)
    if remote_version.returncode:
        raise UpdateError("Vzdálená verze neobsahuje soubor VERSION.")
    latest_version = remote_version.stdout.strip()
    if not VERSION_PATTERN.fullmatch(latest_version):
        raise UpdateError("Vzdálený soubor VERSION nemá platný formát čísla verze.")

    blockers = []
    if ahead:
        blockers.append("Lokální větev obsahuje vlastní commity.")
    if overlapping_changes:
        blockers.append("Lokální změny kolidují s aktualizací: " + ", ".join(overlapping_changes))
    if manual_changes:
        blockers.append(
            "Aktualizace mění systémové soubory vyžadující ruční postup: "
            + ", ".join(manual_changes)
        )

    return {
        **state,
        "latest_version": latest_version,
        "latest_commit": _git(["rev-parse", "--short=12", REMOTE_REF]).stdout.strip(),
        "commits_behind": behind,
        "update_available": behind > 0,
        "blockers": blockers,
        "can_update": behind > 0 and not blockers,
    }


def check_restart_helper() -> None:
    if not os.path.isfile("/usr/local/sbin/meshcom-restart") or not os.access(
        "/usr/local/sbin/meshcom-restart", os.X_OK
    ):
        raise UpdateError("Restart helper /usr/local/sbin/meshcom-restart není nainstalovaný a spustitelný.")

    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", "meshcom-restart.path"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(f"Nelze ověřit restart systemd služby: {exc}") from exc
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise UpdateError(detail or "Restartovací služba meshcom-restart.path není aktivní.")
    if not os.access(REPO_DIR, os.W_OK):
        raise UpdateError("Uživatel webové služby nemůže vytvořit restartovací požadavek v repozitáři.")


def apply_update() -> dict:
    with _UPDATE_LOCK:
        check = check_for_updates()
        if not check["can_update"]:
            if check["blockers"]:
                raise UpdateError(" ".join(check["blockers"]))
            raise UpdateError("Není dostupná žádná nová verze aplikace.")

        _git(["merge", "--ff-only", REMOTE_REF], timeout=120)
        return current_state()


def launch_service_restart() -> None:
    try:
        with RESTART_REQUEST.open("x", encoding="ascii") as request_file:
            request_file.write("restart\n")
    except FileExistsError:
        log.warning("Restartovací požadavek již čeká na zpracování")
    except OSError:
        log.exception("Nepodařilo se vytvořit restartovací požadavek")