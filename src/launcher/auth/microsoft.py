"""Microsoft account authentication.

Implements the standard "Java Edition" login chain used by every
third-party launcher:

    1. OAuth2 device-code flow against Microsoft identity platform
       (no client secret; this is a public client) -> MS access/refresh
       token.
    2. Xbox Live "user authenticate" with the MS access token -> Xbox
       Live token + user hash.
    3. XSTS "authorize" with the Xbox Live token -> XSTS token.
    4. Minecraft Services "login_with_xbox" with the XSTS token + user
       hash -> Minecraft access token.
    5. Minecraft Services entitlement check (does this account actually
       own the game?) and profile fetch (username + UUID + skin).

This is documented (unofficially, but very thoroughly and stably) at
https://wiki.vg/Microsoft_Authentication_Scheme -- that's the reference
this implementation follows.

Every step raises AuthError with a human-readable message on failure so
the UI can show something actionable instead of a raw traceback.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from ..constants import (
    MC_ENTITLEMENT_URL,
    MC_LOGIN_WITH_XBOX_URL,
    MC_PROFILE_URL,
    MS_CLIENT_ID,
    MS_DEVICE_CODE_URL,
    MS_OAUTH_SCOPE,
    MS_TOKEN_URL,
    REQUEST_TIMEOUT_SECONDS,
    USER_AGENT,
    XBOX_LIVE_AUTH_URL,
    XSTS_AUTH_URL,
)

logger = logging.getLogger(__name__)

_PLACEHOLDER_CLIENT_ID = "00000000-0000-0000-0000-000000000000"


class AuthError(RuntimeError):
    """Raised for any failure in the Microsoft/Xbox/Minecraft auth chain."""


class ClientNotConfiguredError(AuthError):
    """Raised when MS_CLIENT_ID is still the placeholder value.

    This is deliberately its own exception type so the UI can show setup
    instructions instead of a generic error.
    """


@dataclass
class DeviceCodeInfo:
    device_code: str
    user_code: str
    verification_uri: str
    expires_at: float
    interval_seconds: int
    message: str


@dataclass
class MinecraftAuthResult:
    minecraft_access_token: str
    minecraft_uuid: str
    minecraft_username: str
    ms_refresh_token: str
    ms_access_token_expires_in: int
    microsoft_user_id: str


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )


def _require_configured_client() -> None:
    if MS_CLIENT_ID == _PLACEHOLDER_CLIENT_ID:
        raise ClientNotConfiguredError(
            "Microsoft sign-in isn't configured yet: MS_CLIENT_ID in "
            "constants.py is still the placeholder GUID. See "
            "docs/MICROSOFT_AUTH_SETUP.md for how to register a free Azure "
            "AD application and plug in a real client ID."
        )


def request_device_code() -> DeviceCodeInfo:
    """Step 1a: ask Microsoft for a device code + user code.

    The caller shows ``user_code`` and ``verification_uri`` to the user
    (e.g. "go to microsoft.com/link and enter ABCD-EFGH"), then polls
    ``poll_device_code`` until the user finishes signing in elsewhere.
    """
    _require_configured_client()
    with _client() as client:
        resp = client.post(
            MS_DEVICE_CODE_URL,
            data={"client_id": MS_CLIENT_ID, "scope": MS_OAUTH_SCOPE},
        )
        if resp.status_code != 200:
            raise AuthError(
                f"Failed to start Microsoft sign-in (HTTP {resp.status_code})."
            )
        data = resp.json()

    return DeviceCodeInfo(
        device_code=data["device_code"],
        user_code=data["user_code"],
        verification_uri=data["verification_uri"],
        expires_at=time.time() + data["expires_in"],
        interval_seconds=data.get("interval", 5),
        message=data.get("message", ""),
    )


@dataclass
class _MsTokenResult:
    access_token: str
    refresh_token: str
    expires_in: int


def poll_device_code(
    info: DeviceCodeInfo,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> _MsTokenResult:
    """Step 1b: poll until the user completes sign-in in their browser.

    Blocks (with sleeps) until success, expiry, denial, or cancellation.
    ``should_cancel`` is polled between attempts so a GUI can offer a
    Cancel button without needing threads-within-threads.
    """
    interval = info.interval_seconds
    with _client() as client:
        while True:
            if should_cancel and should_cancel():
                raise AuthError("Sign-in cancelled.")
            if time.time() > info.expires_at:
                raise AuthError("Sign-in code expired. Please try again.")

            time.sleep(interval)

            resp = client.post(
                MS_TOKEN_URL,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": MS_CLIENT_ID,
                    "device_code": info.device_code,
                },
            )
            data = resp.json()

            if resp.status_code == 200:
                return _MsTokenResult(
                    access_token=data["access_token"],
                    refresh_token=data["refresh_token"],
                    expires_in=data["expires_in"],
                )

            error = data.get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            if error == "expired_token":
                raise AuthError("Sign-in code expired. Please try again.")
            if error == "authorization_declined":
                raise AuthError("Sign-in was declined.")
            raise AuthError(f"Microsoft sign-in failed: {error or resp.status_code}")


def refresh_ms_token(refresh_token: str) -> _MsTokenResult:
    """Use a stored refresh token to get a fresh MS access token without
    prompting the user again. Called automatically before launch when an
    account's Minecraft token is close to expiring.
    """
    _require_configured_client()
    with _client() as client:
        resp = client.post(
            MS_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": MS_CLIENT_ID,
                "refresh_token": refresh_token,
                "scope": MS_OAUTH_SCOPE,
            },
        )
        if resp.status_code != 200:
            raise AuthError(
                "Microsoft session expired and couldn't be refreshed. "
                "Please sign in again."
            )
        data = resp.json()

    return _MsTokenResult(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", refresh_token),
        expires_in=data["expires_in"],
    )


def _xbox_live_authenticate(ms_access_token: str) -> tuple[str, str]:
    """Returns (xbl_token, user_hash)."""
    with _client() as client:
        resp = client.post(
            XBOX_LIVE_AUTH_URL,
            json={
                "Properties": {
                    "AuthMethod": "RPS",
                    "SiteName": "user.auth.xboxlive.com",
                    "RpsTicket": f"d={ms_access_token}",
                },
                "RelyingParty": "http://auth.xboxlive.com",
                "TokenType": "JWT",
            },
        )
        if resp.status_code != 200:
            raise AuthError(f"Xbox Live authentication failed (HTTP {resp.status_code}).")
        data = resp.json()

    token = data["Token"]
    user_hash = data["DisplayClaims"]["xui"][0]["uhs"]
    return token, user_hash


def _xsts_authorize(xbl_token: str) -> tuple[str, str]:
    """Returns (xsts_token, user_hash)."""
    with _client() as client:
        resp = client.post(
            XSTS_AUTH_URL,
            json={
                "Properties": {
                    "SandboxId": "RETAIL",
                    "UserTokens": [xbl_token],
                },
                "RelyingParty": "rp://api.minecraftservices.com/",
                "TokenType": "JWT",
            },
        )
        data = resp.json()
        if resp.status_code == 401:
            xerr = data.get("XErr")
            raise AuthError(_explain_xsts_error(xerr))
        if resp.status_code != 200:
            raise AuthError(f"Xbox authorization failed (HTTP {resp.status_code}).")

    token = data["Token"]
    user_hash = data["DisplayClaims"]["xui"][0]["uhs"]
    return token, user_hash


def _explain_xsts_error(xerr: int | None) -> str:
    # Well-known XSTS error codes, per wiki.vg's Microsoft auth documentation.
    known = {
        2148916233: (
            "This Microsoft account has no Xbox Live profile. Sign in to "
            "xbox.com once with this account to create one, then try again."
        ),
        2148916235: "Xbox Live is not available in this account's region.",
        2148916236: "This account needs adult verification (South Korea).",
        2148916237: "This account needs adult verification (South Korea).",
        2148916238: (
            "This is a child account. It must be added to a Family group "
            "before it can sign in."
        ),
    }
    if xerr in known:
        return known[xerr]
    return f"Xbox authorization was rejected (error {xerr})."


def _minecraft_login_with_xbox(xsts_token: str, user_hash: str) -> str:
    with _client() as client:
        resp = client.post(
            MC_LOGIN_WITH_XBOX_URL,
            json={"identityToken": f"XBL3.0 x={user_hash};{xsts_token}"},
        )
        if resp.status_code != 200:
            raise AuthError(
                f"Minecraft Services login failed (HTTP {resp.status_code})."
            )
        return resp.json()["access_token"]


def _check_entitlement(mc_access_token: str) -> bool:
    with _client() as client:
        resp = client.get(
            MC_ENTITLEMENT_URL,
            headers={"Authorization": f"Bearer {mc_access_token}"},
        )
        if resp.status_code != 200:
            return False
        items = resp.json().get("items", [])
        return len(items) > 0


def _fetch_profile(mc_access_token: str) -> tuple[str, str]:
    """Returns (uuid, username)."""
    with _client() as client:
        resp = client.get(
            MC_PROFILE_URL,
            headers={"Authorization": f"Bearer {mc_access_token}"},
        )
        if resp.status_code == 404:
            raise AuthError(
                "This Microsoft account does not own Minecraft: Java Edition."
            )
        if resp.status_code != 200:
            raise AuthError(f"Failed to fetch Minecraft profile (HTTP {resp.status_code}).")
        data = resp.json()
    return data["id"], data["name"]


def complete_minecraft_login(ms_result: _MsTokenResult, microsoft_user_id: str) -> MinecraftAuthResult:
    """Steps 2-5: turn an MS access token into a usable Minecraft session.

    Shared by both the initial device-code sign-in and by silent
    refreshes, since everything past step 1 is identical either way.
    """
    xbl_token, user_hash = _xbox_live_authenticate(ms_result.access_token)
    xsts_token, user_hash = _xsts_authorize(xbl_token)
    mc_token = _minecraft_login_with_xbox(xsts_token, user_hash)

    if not _check_entitlement(mc_token):
        raise AuthError(
            "This Microsoft account does not own Minecraft: Java Edition."
        )

    uuid, username = _fetch_profile(mc_token)

    return MinecraftAuthResult(
        minecraft_access_token=mc_token,
        minecraft_uuid=uuid,
        minecraft_username=username,
        ms_refresh_token=ms_result.refresh_token,
        ms_access_token_expires_in=ms_result.expires_in,
        microsoft_user_id=microsoft_user_id,
    )


def _decode_ms_user_id(ms_access_token: str) -> str:
    """Pull the stable Microsoft account identifier out of the access
    token's JWT payload (the 'oid' claim), used only to de-duplicate
    re-added accounts. We don't validate the signature -- we're the
    intended audience reading our own token, not a relying party
    verifying an assertion from someone else.
    """
    import base64
    import json

    try:
        payload_b64 = ms_access_token.split(".")[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return payload.get("oid") or payload.get("sub") or ms_access_token[:16]
    except Exception:  # noqa: BLE001 - fall back to something stable-ish
        return ms_access_token[:16]


def sign_in_with_device_code(
    on_code_ready: Callable[[DeviceCodeInfo], None],
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> MinecraftAuthResult:
    """Full first-time sign-in flow.

    ``on_code_ready`` is called once with the DeviceCodeInfo so the caller
    (GUI) can display the code/URL to the user before this function blocks
    on polling.
    """
    info = request_device_code()
    on_code_ready(info)
    ms_result = poll_device_code(info, should_cancel=should_cancel)
    ms_user_id = _decode_ms_user_id(ms_result.access_token)
    return complete_minecraft_login(ms_result, ms_user_id)


def refresh_minecraft_session(refresh_token: str, microsoft_user_id: str) -> MinecraftAuthResult:
    """Silent re-auth using a stored refresh token. Called before launch
    when an account's cached Minecraft token is expired/near-expiry.
    """
    ms_result = refresh_ms_token(refresh_token)
    return complete_minecraft_login(ms_result, microsoft_user_id)
