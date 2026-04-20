#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$REPO_DIR/.venv"
TEST_FILE="${1:-/tmp/surfload-test.bin}"
HOSTS="${SURFLOAD_HOSTS:-1fichier.com,gofile.io,send.now,upload.ee,vikingfile.com}"

if [ ! -d "$VENV_DIR" ]; then
  echo "Fehler: Venv nicht gefunden: $VENV_DIR"
  echo "Bitte zuerst ausfuehren: bash scripts/setup_server.sh"
  exit 1
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "[1/4] CLI erreichbar?"
surfload --help >/dev/null
surfload list

if [ ! -f "$TEST_FILE" ]; then
  echo "[2/4] Erzeuge Testdatei: $TEST_FILE"
  mkdir -p "$(dirname "$TEST_FILE")"
  python - <<'PY' "$TEST_FILE"
from pathlib import Path
import secrets
import sys

target = Path(sys.argv[1])
target.write_bytes(secrets.token_bytes(512 * 1024))
print(f"Testdatei erstellt: {target} ({target.stat().st_size} bytes)")
PY
else
  echo "[2/4] Nutze vorhandene Testdatei: $TEST_FILE"
fi

echo "[3/4] Optional: Accounts interaktiv hinterlegen"
echo "    (nur wenn noch nicht gespeichert)"
echo "    surfload account add onefichier --interactive"
echo "    surfload account add gofile --interactive"
echo "    surfload account add send_now --interactive"
echo "    surfload account add upload_ee --interactive"
echo "    surfload account add vikingfile --interactive"

echo "[4/4] Starte Real-Hoster-Testupload"
echo "Hosts: $HOSTS"
surfload upload --host "$HOSTS" "$TEST_FILE" --parallel 1 --json
