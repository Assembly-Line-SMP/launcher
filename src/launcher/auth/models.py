"""Account data models.

The launcher supports two account kinds side by side:

- ``AccountType.CRACKED``  -- an offline account, identity is just a chosen
  username. UUID is derived deterministically (offline-player UUID, same
  algorithm the vanilla client uses when running in offline mode), so the
  same username always maps to the same UUID across sessions/machines.

- ``AccountType.PREMIUM``  -- a real Microsoft account, authenticated via
  the OAuth device-code flow, exchanged through Xbox Live/XSTS for a
  Minecraft access token. This is what lets the launcher join servers with
  online-mode enabled.

Both kinds implement the same shape the launch flow needs: username, UUID,
and (for premium) an access token. The instance/launch code doesn't need to
know which kind it's holding.
"""

from __future__ import annotations

import hashlib
import time
import uuid as uuid_lib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AccountType(str, Enum):
    CRACKED = "cracked"
    PREMIUM = "premium"


def offline_uuid(username: str) -> str:
    """Derive a deterministic offline-mode UUID from a username.

    Matches vanilla's offline-player UUID derivation: an MD5-based UUID3
    over the bytes of "OfflinePlayer:<username>". This is the same scheme
    every mainstream launcher (vanilla, PrismLauncher, etc.) uses for
    cracked accounts, so the UUID a player gets here will match what they'd
    get anywhere else -- important for per-player data (stats, playerdata,
    permissions) staying consistent.
    """
    seed = f"OfflinePlayer:{username}".encode()
    digest = bytearray(hashlib.md5(seed).digest())  # noqa: S324 - not security-sensitive
    digest[6] = (digest[6] & 0x0F) | 0x30  # version 3
    digest[8] = (digest[8] & 0x3F) | 0x80  # variant RFC 4122
    return str(uuid_lib.UUID(bytes=bytes(digest)))


@dataclass
class Account:
    """A single stored account, cracked or premium."""

    id: str
    type: AccountType
    username: str
    uuid: str

    # Premium-only fields. None for cracked accounts.
    access_token: str | None = None
    refresh_token: str | None = None
    access_token_expires_at: float | None = None  # unix timestamp
    microsoft_user_id: str | None = None

    added_at: float = field(default_factory=time.time)
    last_used_at: float | None = None

    @staticmethod
    def new_cracked(username: str) -> Account:
        return Account(
            id=str(uuid_lib.uuid4()),
            type=AccountType.CRACKED,
            username=username,
            uuid=offline_uuid(username),
        )

    @staticmethod
    def new_premium(
        *,
        username: str,
        uuid: str,
        access_token: str,
        refresh_token: str,
        expires_in_seconds: int,
        microsoft_user_id: str,
    ) -> Account:
        return Account(
            id=str(uuid_lib.uuid4()),
            type=AccountType.PREMIUM,
            username=username,
            uuid=uuid,
            access_token=access_token,
            refresh_token=refresh_token,
            access_token_expires_at=time.time() + expires_in_seconds,
            microsoft_user_id=microsoft_user_id,
        )

    @property
    def is_premium(self) -> bool:
        return self.type is AccountType.PREMIUM

    @property
    def needs_refresh(self) -> bool:
        if not self.is_premium or self.access_token_expires_at is None:
            return False
        # Refresh a bit early to avoid racing expiry mid-launch.
        return time.time() > (self.access_token_expires_at - 60)

    def touch(self) -> None:
        self.last_used_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "username": self.username,
            "uuid": self.uuid,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "access_token_expires_at": self.access_token_expires_at,
            "microsoft_user_id": self.microsoft_user_id,
            "added_at": self.added_at,
            "last_used_at": self.last_used_at,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Account:
        return Account(
            id=data["id"],
            type=AccountType(data["type"]),
            username=data["username"],
            uuid=data["uuid"],
            access_token=data.get("access_token"),
            refresh_token=data.get("refresh_token"),
            access_token_expires_at=data.get("access_token_expires_at"),
            microsoft_user_id=data.get("microsoft_user_id"),
            added_at=data.get("added_at", time.time()),
            last_used_at=data.get("last_used_at"),
        )
