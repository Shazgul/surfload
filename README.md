# Surfload

Anfaengerfreundliches, robustes CLI-Tool fuer Ubuntu/Debian zum parallelen Upload auf mehrere Filehoster.

> Fokus: **streaming statt RAM-Spikes**, stabile Retries mit Backoff, sichere lokale Account-Speicherung, klare Link-Ausgabe.

## Features

- Mehrere Hoster parallel (`--parallel`)
- Interaktive Hoster-Auswahl oder per CLI (`--host fileio,transfer.sh`)
- Plugin-Architektur fuer neue Hoster
- Sichere Credentials:
  - optional `keyring`
  - fallback verschluesselte Datei `~/.config/surfload/credentials.enc` (Fernet + PBKDF2)
- Chunked Streaming (keine Komplettdatei im RAM)
- Retry-Strategie mit exponentiellem Backoff
- Fortschritt pro Datei + Gesamtfortschritt (tqdm)
- Optionale Vorbereitung via `zip` / `7z` / `auto`
- JSON-Ausgabe (`--json` / `--json-file`) + Text-Export (`--export`)
- Demo-Kommando mit lokalem Dummy-Hoster (`demo`)

## Status der enthaltenen Hoster-Plugins

Aktuell enthalten:

- `transfer_sh` (real)
- `fileio` (real)
- `catbox` (real)
- `tmpfiles_org` (real)
- `buzzheavier` (real, PolyUploader-orientiert)
- `onefichier` (real, 1fichier.com)
- `gofile` (real, gofile.io)
- `send_now` (real, send.now)
- `upload_ee` (real, upload.ee)
- `vikingfile` (real, vikingfile.com)
- `dummy_local` (fuer Demo/Tests)

Beim `list`-Befehl werden Host-Tags aus PolyUploader-`profiles.json` angezeigt (z. B. `10GB+`, `60d+`, `<1d`, `delete`), falls fuer die Domain vorhanden.

## Installation (Ubuntu/Debian)

### 1) Systempakete

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv zip p7zip-full
```

### 2) Projekt installieren

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

Optional:

```bash
pip install -e .[sevenzip,keyring,dev]
```

## Schnellstart

### Hoster anzeigen

```bash
surfload list
```

### Account anlegen (interaktiv)

```bash
surfload account add fileio --interactive
surfload account add transfer_sh --interactive
surfload account add buzzheavier --interactive
surfload account add gofile --interactive
surfload account add onefichier --interactive
surfload account add vikingfile --interactive
```

Hinweis zu `vikingfile`: hier wird der `user`-Wert (User-Hash) erwartet.

### Upload (mehrere Hoster parallel)

```bash
surfload upload --host fileio,transfer.sh /pfad/datei.iso --parallel 3
```

### Upload mit Kompression

```bash
surfload upload --host transfer.sh /pfad/ordner --compress zip --parallel 2
# Eigener Name + Passwort + Split in 1GB Teile
surfload upload --host vikingfile /pfad/datei.mp4 \
  --compress 7z --archive-name release-2026 \
  --archive-password-prompt --archive-part-size 1GB
```

### JSON fuer Skripte

```bash
surfload upload --host transfer.sh /pfad/datei.bin --json
surfload upload --host transfer.sh /pfad/datei.bin --json-file result.json
```

### Konfiguration

```bash
surfload config show
surfload config set parallel 4
surfload config set chunk_size 2097152
```

## CLI-Referenz

### `upload`

```bash
surfload upload [PATH ...] \
  [--host fileio,transfer.sh] \
  [--account host:account_name] \
  [--compress none|auto|zip|7z] \
  [--archive-name NAME] [--archive-password PASS|--archive-password-prompt] \
  [--archive-part-size SIZE] \
  [--parallel N] [--chunk-size BYTES] [--retries N] \
  [--resume-on-retry|--no-resume-on-retry] \
  [--recursive] [--json] [--json-file PATH] [--export PATH]
```

Archiv-Optionen:

- `--archive-name`: eigener Basisname fuer das erzeugte Archiv (`upload_bundle` ist nur Default).
- `--archive-password`: Passwort fuer `zip`/`7z` Archiv.
- `--archive-password-prompt`: Passwort interaktiv eingeben (empfohlen, damit es nicht in Shell-History landet).
- `--archive-part-size`: Split-Groesse pro Part, z. B. `500MB`, `1GB`, `1536MiB`.

Hinweis: Passwortschutz und Splitting verwenden die `7z`-CLI (Paket `p7zip-full`).

`resume_on_retry` kann auch in der Config gesetzt werden (Default: `true`).
Host-seitig ist Resume fuer `dummy_local` umgesetzt und fuer `transfer_sh`, `buzzheavier` sowie `gofile` optional aktivierbar (`host_defaults.<host>.enable_resume: true`).
Fuer `gofile` wird zusaetzlich ein Probe-Endpoint fuer Offset-Lookup ueber `host_defaults.gofile.resume_probe_url_template` benoetigt.
Alle anderen Hoster fallen sauber auf normale Retry-Uploads zurueck.

### `account`

```bash
surfload account add HOST --interactive
surfload account add HOST --name work --field token=abc123
surfload account list [--host HOST]
surfload account remove HOST NAME
```

### `list`

```bash
surfload list
```

### `config`

```bash
surfload config show
surfload config set KEY VALUE
```

### `demo`

```bash
surfload demo --port 8765
```

## Beispielkonfiguration

Siehe `config.example.yaml`.

Runtime-Config liegt standardmaessig unter:

- `~/.config/surfload/config.yaml`

## PolyUploader-Form- und Endpunkt-Mapping (Auszug)

Das Projekt nutzt PolyUploader-Muster als Vorlage fuer Host-Implementierungen (insb. Form-Felder und Endpunkte):

- **transfer.sh** (aus `src/js/upload.js`, `case "transfer.sh"`):
  - Methode: `PUT`
  - Endpoint-Muster: `https://transfer.sh/<filename>`
  - Direkte Link-Antwort als Text
- **buzzheavier.com** (aus `src/js/upload.js`, `case "buzzheavier.com"`):
  - Methode: `PUT`
  - Endpoint-Muster: `https://w.buzzheavier.com/<parent>/<filename>`
  - Optional `Authorization: Bearer ...`
  - URL-Extraktion aus JSON (`data.id`, `url`, etc.)
- **file.io**:
  - Oeffentliche API per Multipart-POST
  - Felder wie `maxDownloads`, `autoDelete`, `expires`
- **Neue Multipart-Hoster**:
  - `1fichier.com`, `gofile.io`, `send.now`, `upload.ee`, `vikingfile.com`
  - Endpunkte/Feldnamen sind per `host_defaults` konfigurierbar, damit API-Aenderungen leicht angepasst werden koennen.

## Plugin-API

Neue Hoster lassen sich als Klasse in `src/surfload/plugins/` ergaenzen:

```python
class MyHostPlugin(BaseHostPlugin):
    host_key = "myhost"
    display_name = "My Host"

    def init(self) -> None:
        ...

    def auth(self, account: dict | None) -> None:
        ...

    def upload_file(self, stream, size: int, metadata: dict):
        ...

    def finalize(self, response_data, metadata: dict) -> str:
        ...
```

Danach in `plugins/__init__.py` registrieren.

## RAM-/Stabilitaets-Strategie

- Keine komplette Datei im Speicher
- Uploads lesen gestueckelt (Default `chunk_size=1MB`)
- Upload-Requests mit Body ohne automatische HTTPAdapter-Retries
- Eigene Retry-Schleife auf Task-Ebene mit Backoff (`2^n`, gedeckelt)
- Optionales Resume bei Retry (`resume_on_retry`) fuer Hoster mit Resume-Support
- Begrenzte Worker-Pools (`--parallel`)

Damit wird das RAM-Verhalten deutlich stabiler als bei Implementierungen mit grossen In-Memory-Buffern.

## Tests

```bash
pytest -q
```

## Server-Setup und Real-Hoster-Test

Fuer eine schnelle Inbetriebnahme auf Ubuntu/Debian liegen zwei Hilfsskripte bereit:

```bash
bash scripts/setup_server.sh
bash scripts/first_real_test.sh /tmp/test-upload.bin
```

- `setup_server.sh` installiert Abhaengigkeiten, legt `.venv` an und installiert Surfload inkl. Dev-Extras.
- `first_real_test.sh` prueft `surfload list`, erstellt optional eine Testdatei und startet einen Mehrhost-Upload.

Vorhandene Tests:

- `tests/test_streaming.py`
- `tests/test_credentials.py`
- `tests/test_compression.py`
- `tests/test_plugins.py`

## Repository-Struktur

```text
src/surfload/
  cli.py
  core.py
  plugins/
    base.py
    fileio.py
    catbox.py
    tmpfiles_org.py
    transfer_sh.py
    buzzheavier.py
    onefichier.py
    gofile.py
    send_now.py
    upload_ee.py
    vikingfile.py
    dummy_local.py
  utils/
    credentials.py
    compression.py
    streaming.py
    logger.py
    config.py
tests/
pyproject.toml
setup.cfg
```

## Git-Workflow

Branch- und Commit-Richtlinien stehen in `CONTRIBUTING.md`.
