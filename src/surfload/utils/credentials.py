from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

try:
    import keyring  # type: ignore
except Exception:  # pragma: no cover - optional
    keyring = None


DEFAULT_CREDENTIALS_PATH = Path.home() / ".config" / "surfload" / "credentials.enc"


class CredentialsError(Exception):
    pass


class CredentialStore:
    def __init__(
        self,
        path: Path | None = None,
        backend: str = "file",
        keyring_service: str = "surfload",
    ) -> None:
        self.path = (path or DEFAULT_CREDENTIALS_PATH).expanduser().resolve()
        self.backend = backend
        self.keyring_service = keyring_service
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _derive_key(self, password: str, salt: bytes, iterations: int = 390_000) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=iterations,
        )
        return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))

    def _empty_payload(self) -> Dict[str, Any]:
        return {"version": 1, "hosts": {}}

    def exists(self) -> bool:
        if self.backend == "keyring":
            if keyring is None:
                return False
            return bool(keyring.get_password(self.keyring_service, "credentials"))
        return self.path.exists()

    def load(self, master_password: str | None = None) -> Dict[str, Any]:
        if self.backend == "keyring":
            return self._load_from_keyring()

        if not self.path.exists():
            return self._empty_payload()
        if not master_password:
            raise CredentialsError("Master password required for encrypted credential file")

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        salt = base64.b64decode(payload["salt"])
        token = payload["token"].encode("utf-8")
        iterations = int(payload.get("iterations", 390_000))

        key = self._derive_key(master_password, salt, iterations=iterations)
        try:
            decrypted = Fernet(key).decrypt(token)
        except InvalidToken as exc:
            raise CredentialsError("Wrong master password or corrupted credential store") from exc
        return json.loads(decrypted.decode("utf-8"))

    def save(self, data: Dict[str, Any], master_password: str | None = None) -> None:
        if self.backend == "keyring":
            self._save_to_keyring(data)
            return

        if not master_password:
            raise CredentialsError("Master password required to save encrypted credentials")

        salt = b""
        if self.path.exists():
            try:
                previous_payload = json.loads(self.path.read_text(encoding="utf-8"))
                previous_salt = previous_payload.get("salt")
                if isinstance(previous_salt, str):
                    salt = base64.b64decode(previous_salt)
            except Exception:
                salt = b""

        if len(salt) != 16:
            salt = os.urandom(16)
        iterations = 390_000

        key = self._derive_key(master_password, salt, iterations=iterations)
        token = Fernet(key).encrypt(json.dumps(data).encode("utf-8"))

        payload = {
            "kdf": "pbkdf2_sha256",
            "iterations": iterations,
            "salt": base64.b64encode(salt).decode("ascii"),
            "token": token.decode("ascii"),
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load_from_keyring(self) -> Dict[str, Any]:
        if keyring is None:
            raise CredentialsError("keyring backend requested but keyring package is not installed")
        raw = keyring.get_password(self.keyring_service, "credentials")
        if not raw:
            return self._empty_payload()
        return json.loads(raw)

    def _save_to_keyring(self, data: Dict[str, Any]) -> None:
        if keyring is None:
            raise CredentialsError("keyring backend requested but keyring package is not installed")
        keyring.set_password(self.keyring_service, "credentials", json.dumps(data))

    def list_accounts(self, host: str, master_password: str | None = None) -> List[Dict[str, Any]]:
        data = self.load(master_password=master_password)
        hosts = data.setdefault("hosts", {})
        return list(hosts.get(host, []))

    def add_account(
        self,
        host: str,
        account_data: Dict[str, Any],
        name: str,
        master_password: str | None = None,
    ) -> None:
        data = self.load(master_password=master_password)
        hosts = data.setdefault("hosts", {})
        host_accounts = hosts.setdefault(host, [])

        filtered = [acc for acc in host_accounts if acc.get("name") != name]
        filtered.append({"name": name, "data": account_data})
        hosts[host] = filtered
        self.save(data, master_password=master_password)

    def remove_account(self, host: str, name: str, master_password: str | None = None) -> bool:
        data = self.load(master_password=master_password)
        hosts = data.setdefault("hosts", {})
        previous = list(hosts.get(host, []))
        updated = [acc for acc in previous if acc.get("name") != name]
        changed = len(updated) != len(previous)
        hosts[host] = updated
        if changed:
            self.save(data, master_password=master_password)
        return changed
