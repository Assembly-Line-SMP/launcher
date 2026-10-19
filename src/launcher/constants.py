"""Central, hand-tunable constants for the launcher.

Everything a maintainer is likely to need to change lives here rather than
scattered across modules.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Modrinth project this launcher is built around.
#
# "assembly-line-smp" is a Modrinth *server* project. Server projects expose
# the same /v2/project and /v2/project/{id}/version endpoints as regular
# modpack projects, and versions carry a downloadable .mrpack file the same
# way. See https://docs.modrinth.com/api/ for the general schema.
# --------------------------------------------------------------------------
MODRINTH_PROJECT_SLUG = "assembly-line-smp"
MODRINTH_API_BASE = "https://api.modrinth.com/v2"
MODRINTH_STAGING_API_BASE = "https://staging-api.modrinth.com/v2"

# Modrinth asks API consumers to identify themselves with a descriptive
# User-Agent. See https://docs.modrinth.com/api/#authentication
USER_AGENT = "Pavle012/assembly-line-launcher/0.1.0 (github.com/Pavle012)"

# --------------------------------------------------------------------------
# Microsoft / Xbox Live / Minecraft Services authentication.
#
# CLIENT_ID must be an Azure AD "public client" application registration
# (Mobile and desktop applications platform, no client secret, with the
# "Allow public client flows" toggle ON so the device-code flow works).
#
# This currently reuses Prism Launcher's public MSA client ID, published
# deliberately in their own CMakeLists.txt for exactly this kind of reuse
# by forks/rebrands (see the comment above that line in their repo:
# https://github.com/PrismLauncher/PrismLauncher/blob/develop/CMakeLists.txt).
# It is a stand-in, not a permanent choice -- switch to your own the moment
# you can register one (see docs/MICROSOFT_AUTH_SETUP.md). This is the only
# line that needs to change to do that.
# --------------------------------------------------------------------------
MS_CLIENT_ID = "0fa03691-5074-4856-907e-c7db32d9e444"

MS_DEVICE_CODE_URL = (
    "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
)
MS_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
MS_OAUTH_SCOPE = "XboxLive.signin offline_access"

XBOX_LIVE_AUTH_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_AUTH_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN_WITH_XBOX_URL = (
    "https://api.minecraftservices.com/authentication/login_with_xbox"
)
MC_ENTITLEMENT_URL = "https://api.minecraftservices.com/entitlements/mcstore"
MC_PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"

# --------------------------------------------------------------------------
# Java runtime provisioning (Eclipse Adoptium / Temurin).
# --------------------------------------------------------------------------
ADOPTIUM_API_BASE = "https://api.adoptium.net/v3"

# --------------------------------------------------------------------------
# Mojang piston meta, used to resolve vanilla version manifests / asset
# indexes / natives that the modpack's minecraft version depends on.
# --------------------------------------------------------------------------
PISTON_META_MANIFEST = (
    "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
)
PISTON_META_RESOURCES = "https://resources.download.minecraft.net"

# --------------------------------------------------------------------------
# Application identity, used for platform-appropriate data directories.
# --------------------------------------------------------------------------
APP_NAME = "AssemblyLineLauncher"
APP_AUTHOR = "Pavle012"

REQUEST_TIMEOUT_SECONDS = 30
DOWNLOAD_CHUNK_SIZE = 1024 * 512
MAX_PARALLEL_DOWNLOADS = 8
