from __future__ import annotations

from pathlib import Path

import pytest

from surfload.utils.credentials import CredentialStore, CredentialsError


def test_credential_store_roundtrip_file_backend(tmp_path: Path) -> None:
    store = CredentialStore(path=tmp_path / "credentials.enc", backend="file")

    store.add_account(
        host="gofile",
        name="main",
        account_data={"token": "secret-token"},
        master_password="pw123",
    )

    accounts = store.list_accounts("gofile", master_password="pw123")
    assert len(accounts) == 1
    assert accounts[0]["name"] == "main"
    assert accounts[0]["data"]["token"] == "secret-token"


def test_credential_store_rejects_wrong_password(tmp_path: Path) -> None:
    store = CredentialStore(path=tmp_path / "credentials.enc", backend="file")
    store.add_account(
        host="gofile",
        name="default",
        account_data={"token": "abc"},
        master_password="correct",
    )

    with pytest.raises(CredentialsError):
        store.list_accounts("gofile", master_password="wrong")


def test_credential_store_remove_account(tmp_path: Path) -> None:
    store = CredentialStore(path=tmp_path / "credentials.enc", backend="file")
    store.add_account(
        host="dailyuploads",
        name="acc1",
        account_data={"token": "xyz"},
        master_password="pw",
    )

    removed = store.remove_account("dailyuploads", "acc1", master_password="pw")
    assert removed is True
    assert store.list_accounts("dailyuploads", master_password="pw") == []
