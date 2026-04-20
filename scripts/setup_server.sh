#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$REPO_DIR/.venv"
CONFIG_DIR="$HOME/.config/surfload"
CONFIG_FILE="$CONFIG_DIR/config.yaml"

echo "[1/5] Installiere Systemabhaengigkeiten (Ubuntu/Debian)..."
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git zip p7zip-full

echo "[2/5] Erzeuge Python-Venv..."
python3 -m venv "$VENV_DIR"

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "[3/5] Aktualisiere pip..."
python -m pip install --upgrade pip

echo "[4/5] Installiere Surfload inkl. Dev-Extras..."
python -m pip install -e "$REPO_DIR[dev,sevenzip,keyring]"

echo "[5/5] Lege Default-Config an (falls noch nicht vorhanden)..."
mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_FILE" ] && [ -f "$REPO_DIR/config.example.yaml" ]; then
  cp "$REPO_DIR/config.example.yaml" "$CONFIG_FILE"
  echo "Config erstellt: $CONFIG_FILE"
else
  echo "Config bereits vorhanden: $CONFIG_FILE"
fi

echo
echo "Setup abgeschlossen."
echo "Naechster Schritt:"
echo "  bash scripts/first_real_test.sh /tmp/surfload-test.bin"
