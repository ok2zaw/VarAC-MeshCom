#!/usr/bin/env bash
set -eu

PROJECT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$PROJECT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Python 3 first."
  exit 1
fi

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

mkdir -p /home/pi/Maildir/new /home/pi/Maildir/cur /home/pi/Maildir/tmp
if [ -d /home/pi ] && [ "$(id -u)" -eq 0 ]; then
  chown -R pi:pi /home/pi/Maildir 2>/dev/null || true
fi

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Update it with your real SMTP/IMAP values."
fi

echo
printf 'Setup finished.\n'
printf 'Next steps:\n'
printf '  1. Edit .env with your SMTP/IMAP and MeshCom values\n'
printf '  2. source .venv/bin/activate\n'
printf '  3. python3 gateway.py\n'
printf '  4. python3 webapp.py\n'
