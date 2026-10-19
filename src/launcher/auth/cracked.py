"""Cracked (offline) account creation.

Deliberately tiny: there's no network round-trip, no token, nothing to
refresh. All the actual logic (deterministic UUID derivation) lives in
``launcher.auth.models`` since the account store needs it too.
"""

from __future__ import annotations

import re

from .models import Account

_VALID_USERNAME = re.compile(r"^[A-Za-z0-9_]{3,16}$")


class InvalidUsernameError(ValueError):
    pass


def validate_username(username: str) -> None:
    """Raise InvalidUsernameError if the username isn't a legal Minecraft
    username shape (3-16 chars, alphanumeric + underscore).

    We enforce vanilla's rules even for offline accounts so that in-game
    behavior (chat, skin lookups some servers attempt, etc.) doesn't
    surprise players who later "go legit" with the same name.
    """
    if not _VALID_USERNAME.match(username):
        raise InvalidUsernameError(
            "Username must be 3-16 characters: letters, numbers, and "
            "underscores only."
        )


def create_cracked_account(username: str) -> Account:
    username = username.strip()
    validate_username(username)
    return Account.new_cracked(username)
