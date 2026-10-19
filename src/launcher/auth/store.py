"""Persistence for the multi-account list.

Design goals:

- Any number of accounts, cracked and premium mixed freely.
- One of them is marked "active" -- that's who gets launched by default.
- Refresh tokens are sensitive (they're effectively a login credential), so
  when a system keyring is available we store tokens there and keep only
  non-sensitive metadata (username/uuid/type) in the plain JSON file. If no
  keyring backend is available (e.g. headless Linux CI, minimal WM with no
  secret service), we fall back to storing tokens in the JSON file directly
  rather than failing outright -- degraded-but-working beats broken.
"""

from __future__ import annotations

import contextlib
import json
import logging
from pathlib import Path

from .models import Account, AccountType
from ..util.paths import accounts_file

logger = logging.getLogger(__name__)

_KEYRING_SERVICE = "AssemblyLineLauncher"

try:
    import keyring
    import keyring.errors

    _KEYRING_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised via packaging, not tests
    keyring = None  # type: ignore[assignment]
    _KEYRING_AVAILABLE = False


def _keyring_usable() -> bool:
    if not _KEYRING_AVAILABLE:
        return False
    try:
        # get_keyring() raises if no backend is configured at all.
        backend = keyring.get_keyring()
        return backend is not None
    except Exception:  # noqa: BLE001 - keyring backends raise all sorts
        return False


class AccountStore:
    """In-memory account list backed by a JSON file (+ optional keyring)."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or accounts_file()
        self._accounts: dict[str, Account] = {}
        self._active_id: str | None = None
        self._use_keyring = _keyring_usable()
        if not self._use_keyring:
            logger.warning(
                "No system keyring backend available; refresh tokens will be "
                "stored in plain JSON at %s instead of the OS credential "
                "store.",
                self._path,
            )
        self.load()

    # -- persistence ------------------------------------------------------

    def load(self) -> None:
        self._accounts.clear()
        if not self._path.exists():
            return

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to read accounts file %s: %s", self._path, exc)
            return

        self._active_id = raw.get("active_id")
        for entry in raw.get("accounts", []):
            account = Account.from_dict(entry)
            if self._use_keyring and account.is_premium:
                account.refresh_token = self._keyring_get(
                    account.id, "refresh_token"
                ) or account.refresh_token
                account.access_token = self._keyring_get(
                    account.id, "access_token"
                ) or account.access_token
            self._accounts[account.id] = account

    def save(self) -> None:
        accounts_out = []
        for account in self._accounts.values():
            entry = account.to_dict()
            if self._use_keyring and account.is_premium:
                # Don't duplicate secrets into the plaintext file when a
                # keyring is available.
                if account.refresh_token:
                    self._keyring_set(
                        account.id, "refresh_token", account.refresh_token
                    )
                    entry["refresh_token"] = None
                if account.access_token:
                    self._keyring_set(
                        account.id, "access_token", account.access_token
                    )
                    entry["access_token"] = None
            accounts_out.append(entry)

        payload = {"active_id": self._active_id, "accounts": accounts_out}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    def _keyring_set(self, account_id: str, field: str, value: str) -> None:
        try:
            keyring.set_password(_KEYRING_SERVICE, f"{account_id}:{field}", value)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Keyring write failed for %s/%s: %s", account_id, field, exc)

    def _keyring_get(self, account_id: str, field: str) -> str | None:
        try:
            return keyring.get_password(_KEYRING_SERVICE, f"{account_id}:{field}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Keyring read failed for %s/%s: %s", account_id, field, exc)
            return None

    def _keyring_delete(self, account_id: str, field: str) -> None:
        with contextlib.suppress(Exception):
            keyring.delete_password(_KEYRING_SERVICE, f"{account_id}:{field}")

    # -- CRUD ---------------------------------------------------------

    def list_accounts(self) -> list[Account]:
        return sorted(
            self._accounts.values(), key=lambda a: a.added_at
        )

    def get(self, account_id: str) -> Account | None:
        return self._accounts.get(account_id)

    def add(self, account: Account, *, make_active: bool = True) -> Account:
        # Cracked accounts are keyed by username -- adding the same offline
        # username twice should update, not duplicate.
        for existing in self._accounts.values():
            if (
                existing.type is AccountType.CRACKED
                and account.type is AccountType.CRACKED
                and existing.username.lower() == account.username.lower()
            ):
                account.id = existing.id
                break
            if (
                existing.type is AccountType.PREMIUM
                and account.type is AccountType.PREMIUM
                and existing.microsoft_user_id == account.microsoft_user_id
                and account.microsoft_user_id is not None
            ):
                account.id = existing.id
                break

        self._accounts[account.id] = account
        if make_active or self._active_id is None:
            self._active_id = account.id
        self.save()
        return account

    def remove(self, account_id: str) -> None:
        account = self._accounts.pop(account_id, None)
        if account is None:
            return
        if self._use_keyring and account.is_premium:
            self._keyring_delete(account_id, "refresh_token")
            self._keyring_delete(account_id, "access_token")
        if self._active_id == account_id:
            remaining = self.list_accounts()
            self._active_id = remaining[0].id if remaining else None
        self.save()

    def set_active(self, account_id: str) -> None:
        if account_id not in self._accounts:
            raise KeyError(f"No such account: {account_id}")
        self._active_id = account_id
        self.save()

    @property
    def active_account(self) -> Account | None:
        if self._active_id is None:
            return None
        return self._accounts.get(self._active_id)

    def update(self, account: Account) -> None:
        self._accounts[account.id] = account
        self.save()
