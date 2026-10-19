"""High-level account operations used by the UI layer.

Wraps AccountStore + the cracked/microsoft auth modules so callers (GUI
controllers, CLI) work with one simple API: add an account, list accounts,
switch active, remove, and "give me a launch-ready account" which
transparently refreshes premium tokens as needed.
"""

from __future__ import annotations

import logging

from .cracked import create_cracked_account
from .microsoft import (
    AuthError,
    refresh_minecraft_session,
    sign_in_with_device_code,
)
from .models import Account, AccountType
from .store import AccountStore

logger = logging.getLogger(__name__)


class AccountManager:
    def __init__(self, store: AccountStore | None = None) -> None:
        self.store = store or AccountStore()

    # -- listing / selection -----------------------------------------

    def list_accounts(self) -> list[Account]:
        return self.store.list_accounts()

    @property
    def active_account(self) -> Account | None:
        return self.store.active_account

    def set_active(self, account_id: str) -> None:
        self.store.set_active(account_id)

    def remove(self, account_id: str) -> None:
        self.store.remove(account_id)

    # -- adding accounts ------------------------------------------------

    def add_cracked_account(self, username: str, *, make_active: bool = True) -> Account:
        account = create_cracked_account(username)
        return self.store.add(account, make_active=make_active)

    def add_premium_account(
        self,
        on_code_ready,
        *,
        should_cancel=None,
        make_active: bool = True,
    ) -> Account:
        """Runs the full device-code sign-in flow and stores the result.

        ``on_code_ready(DeviceCodeInfo)`` is invoked so the UI can display
        the user code / verification URL before this call blocks polling
        for completion. Intended to be called from a background thread in
        a GUI context -- this function itself is synchronous/blocking.
        """
        result = sign_in_with_device_code(on_code_ready, should_cancel=should_cancel)
        account = Account.new_premium(
            username=result.minecraft_username,
            uuid=result.minecraft_uuid,
            access_token=result.minecraft_access_token,
            refresh_token=result.ms_refresh_token,
            expires_in_seconds=result.ms_access_token_expires_in,
            microsoft_user_id=result.microsoft_user_id,
        )
        return self.store.add(account, make_active=make_active)

    # -- launch-time resolution -----------------------------------------

    def get_launch_ready_account(self, account_id: str | None = None) -> Account:
        """Return an account guaranteed to have a valid access token,
        refreshing a premium account's session first if needed.

        Raises AuthError if refresh fails (e.g. the user revoked access
        and needs to sign in again) or if there are no accounts at all.
        """
        account = (
            self.store.get(account_id) if account_id else self.active_account
        )
        if account is None:
            raise AuthError(
                "No account selected. Add a cracked or Microsoft account first."
            )

        if account.type is AccountType.PREMIUM and account.needs_refresh:
            if not account.refresh_token:
                raise AuthError(
                    f"Session for {account.username} expired and there is no "
                    "saved refresh token. Please sign in again."
                )
            logger.info("Refreshing Minecraft session for %s", account.username)
            result = refresh_minecraft_session(
                account.refresh_token, account.microsoft_user_id or ""
            )
            account.access_token = result.minecraft_access_token
            account.refresh_token = result.ms_refresh_token
            account.username = result.minecraft_username
            account.uuid = result.minecraft_uuid
            import time as _time

            account.access_token_expires_at = (
                _time.time() + result.ms_access_token_expires_in
            )
            self.store.update(account)

        account.touch()
        self.store.update(account)
        return account
