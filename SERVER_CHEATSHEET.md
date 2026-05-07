# Surfload Server CheatSheet

Kurzreferenz fuer Ubuntu/Debian-Server.

> Hinweis: Surfload benoetigt **Python >= 3.10**.

## 1) Einmalige Installation

```bash
sudo apt update
sudo apt install -y git python3 python3-pip python3-venv zip p7zip-full

# Falls `python3 --version` < 3.10 ist, zusaetzlich 3.10/3.11 installieren:
sudo apt install -y python3.10 python3.10-venv python3.10-distutils
# alternativ (wenn verfuegbar):
# sudo apt install -y python3.11 python3.11-venv

# Repo klonen
# (URL ggf. anpassen)
git clone https://github.com/Shazgul/surfload.git
cd surfload

# venv + Installation
python3.10 -m venv .venv  # oder python3.11 -m venv .venv
source .venv/bin/activate
python --version          # sollte >= 3.10 sein
pip install -U pip
pip install -e .
```

Optional mit Extras:

```bash
pip install -e .[sevenzip,keyring,dev]
```

## 2) Update auf neuesten Stand

```bash
cd ~/surfload
source .venv/bin/activate
git pull
pip install -e .
```

## 3) Verfuegbare Hoster anzeigen

```bash
source ~/surfload/.venv/bin/activate
surfload list
```

## 3b) Interaktives Menue (empfohlen)

```bash
source ~/surfload/.venv/bin/activate
surfload ui
# alternativ: surfload wizard
```

## 4) Accounts hinterlegen (falls benoetigt)

```bash
source ~/surfload/.venv/bin/activate
surfload account add dailyuploads --interactive
surfload account add fileq --interactive
surfload account add gofile --interactive
surfload account add megaup --interactive
surfload account add send_now --interactive
surfload account add upload_ee --interactive
```

## 5) Schnelltest-Upload (ohne Kompression)

```bash
source ~/surfload/.venv/bin/activate
surfload upload --host catbox,dailyuploads,fileq,gofile,megaup,send_now,tmpfiles_org,upload_ee /pfad/datei.bin --parallel 3 --no-progress
```

## 6) Realtest mit Split-Archiv + TXT/JSON Export

```bash
source ~/surfload/.venv/bin/activate
surfload upload \
  --host catbox,dailyuploads,fileq,gofile,megaup,send_now,tmpfiles_org,upload_ee \
  /pfad/datei.bin \
  --compress 7z \
  --archive-name release_test \
  --archive-part-size 1GB \
  --parallel 2 \
  --no-progress \
  --export /tmp/surfload_results.txt \
  --json-file /tmp/surfload_results.json
```

## 7) Nur TXT/JSON aus vorhandenem JSON neu erzeugen

```bash
source ~/surfload/.venv/bin/activate
python3 - << 'PY'
import json
from pathlib import Path
from surfload.core import UploadManager, UploadResult

src = Path('/tmp/surfload_results.json')
out = Path('/tmp/surfload_results_grouped.txt')
results = [UploadResult(**item) for item in json.loads(src.read_text(encoding='utf-8'))]
UploadManager.export_summary_text(results, out)
print(out)
PY
```

## 8) Tests

```bash
cd ~/surfload
source .venv/bin/activate
pytest -q
```

## 9) Haeufige Probleme

- `Unknown host`: Tippfehler im `--host` String; `surfload list` pruefen.
- `7z` fehlt: `sudo apt install -y p7zip-full`.
- Kein `surfload` Befehl: venv nicht aktiv oder Paket nicht installiert.
- Credentials-Fehler: Account neu setzen (`surfload account add <host> --interactive`).

## 10) Empfohlener Daily Workflow

```bash
cd ~/surfload
source .venv/bin/activate
git pull
pip install -e .
surfload list
surfload upload --host catbox,gofile,megaup /pfad/datei.bin --parallel 3 --export /tmp/out.txt --json-file /tmp/out.json
```
