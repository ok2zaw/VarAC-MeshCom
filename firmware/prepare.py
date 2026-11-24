#!/usr/bin/env python3
"""Prepare a new or clean MeshCom checkout; never overwrite local edits."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent


def git(dest, *args, capture=False):
    result = subprocess.run(["git", "-C", str(dest), *args], check=True,
                            text=True, stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else None


def prepare(dest, revision=None):
    manifest = json.loads((HERE / "upstream.json").read_text())
    revision = revision or manifest["revision"]
    dest = Path(dest).resolve()
    patches = sorted((HERE / "patches").glob("*.patch"))
    if not patches:
        raise ValueError("Integration patches are missing")
    if not dest.exists() or not any(dest.iterdir()):
        dest.mkdir(parents=True, exist_ok=True)
        git(dest, "init")
        git(dest, "remote", "add", "origin", manifest["repository"])
        git(dest, "fetch", "--depth", "1", "origin", revision)
        git(dest, "checkout", "--detach", "FETCH_HEAD")
    else:
        if git(dest, "rev-parse", "--show-toplevel", capture=True) != str(dest):
            raise ValueError("Destination must be the root of a Git checkout")
        if git(dest, "status", "--porcelain", "--untracked-files=all", capture=True):
            raise ValueError("Checkout has local changes; use a new destination")
        if git(dest, "rev-parse", "HEAD", capture=True) != git(dest, "rev-parse", "--verify", revision + "^{commit}", capture=True):
            raise ValueError("Checkout is not at the requested revision; use a new destination")
    overlay = [(p, dest / p.relative_to(HERE / "overlay"))
               for p in (HERE / "overlay").rglob("*") if p.is_file()]
    if any(target.exists() for _, target in overlay):
        raise ValueError("An overlay path already exists upstream; review the new firmware")
    git(dest, "apply", "--check", *map(str, patches))
    git(dest, "apply", *map(str, patches))
    for source, target in overlay:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    print(f"Prepared {dest} at {git(dest, 'rev-parse', 'HEAD', capture=True)}")
    print(f"Build with: pio run -d {dest} -e {manifest['environment']}")
    print("Profile: OK2ZAW-30-v2 (callsign, Ethernet DHCP, webserver); review src/nrf52/local_profile.h")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--revision", help="Explicit upstream revision for a reviewed port")
    args = parser.parse_args()
    try:
        prepare(args.destination, args.revision)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Preparation failed: {exc}\n")


if __name__ == "__main__":
    main()
