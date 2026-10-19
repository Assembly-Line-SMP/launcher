"""Builds and runs the actual `java ...` invocation that starts Minecraft.

Combines: the instance's installed loader profile, the vanilla libraries/
assets, the active account's identity/token, and per-instance settings
(memory allocation, extra JVM args) into a single argv list, then spawns it
as a subprocess whose stdout/stderr the GUI can stream into a log view.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..auth.models import Account, AccountType
from .instance import Instance
from .loader_installer import LoaderProfile, install_loader
from .vanilla_installer import VanillaVersionInfo, install_vanilla_version
from ..util.paths import libraries_dir

logger = logging.getLogger(__name__)

OFFLINE_ACCESS_TOKEN = "0"  # what vanilla itself uses for offline sessions


@dataclass
class LaunchSettings:
    min_memory_mb: int = 2048
    max_memory_mb: int = 4096
    extra_jvm_args: list[str] | None = None
    window_width: int = 1280
    window_height: int = 720
    fullscreen: bool = False


class LaunchError(RuntimeError):
    pass


def _classpath_separator() -> str:
    return ";" if platform.system() == "Windows" else ":"


def _substitute(template: str, values: dict[str, str]) -> str:
    result = template
    for key, val in values.items():
        result = result.replace("${" + key + "}", val)
    return result


def _flatten_modern_args(
    arg_list: list[Any], values: dict[str, str]
) -> list[str]:
    """Modern (1.13+) argument lists mix plain strings with conditional
    {rules, value} objects (used for things like -demo or fullscreen
    flags). We evaluate the rules the same way library rules work and drop
    anything that doesn't apply to this platform/account type.
    """
    from .vanilla_installer import _rules_allow

    out: list[str] = []
    for entry in arg_list:
        if isinstance(entry, str):
            out.append(_substitute(entry, values))
            continue
        if not _rules_allow(entry.get("rules")):
            continue
        value = entry["value"]
        if isinstance(value, list):
            out.extend(_substitute(v, values) for v in value)
        else:
            out.append(_substitute(value, values))
    return out


def build_launch_command(
    java_binary: Path,
    instance: Instance,
    vanilla: VanillaVersionInfo,
    loader: LoaderProfile,
    account: Account,
    settings: LaunchSettings,
) -> list[str]:
    if loader.profile_id and loader.profile_id.lower().startswith(("neoforge-", "forge-")):
        loader_arguments = _flatten_modern_args(loader.extra_game_arguments, {})
        if "--launchTarget" not in loader_arguments or "--fml.mcVersion" not in loader_arguments:
            raise LaunchError(
                f"Loader profile {loader.profile_id} is incomplete: missing "
                "NeoForge launch arguments. Reinstall the mod loader for this instance."
            )

    is_forge_like = loader.profile_id and loader.profile_id.lower().startswith(("neoforge-", "forge-"))
    lib_dir = instance.minecraft_dir / "libraries" if is_forge_like else libraries_dir()

    # Build values with a placeholder classpath first so we can resolve JVM arguments
    values = {
        "auth_player_name": account.username,
        "version_name": vanilla.version_id,
        "game_directory": str(instance.minecraft_dir),
        "assets_root": str(vanilla.assets_dir),
        "assets_index_name": vanilla.asset_index_id,
        "auth_uuid": account.uuid,
        "auth_access_token": account.access_token or OFFLINE_ACCESS_TOKEN,
        "clientid": account.microsoft_user_id or "",
        "auth_xuid": account.microsoft_user_id or "",
        "user_type": "msa" if account.is_premium else "legacy",
        "version_type": "release",
        "natives_directory": str(vanilla.natives_dir),
        "launcher_name": "AssemblyLineLauncher",
        "launcher_version": "0.1.0",
        "classpath": "",
        "classpath_separator": _classpath_separator(),
        "library_directory": str(lib_dir),
        "resolution_width": str(settings.window_width),
        "resolution_height": str(settings.window_height),
    }

    # Evaluate JVM arguments early to identify any jars placed on the module path
    jvm_arg_source = loader.extra_jvm_arguments or vanilla.jvm_arguments
    module_path_jars = set()
    if jvm_arg_source:
        flat_jvm_args = _flatten_modern_args(jvm_arg_source, values)
        for i, arg in enumerate(flat_jvm_args):
            if arg in ("-p", "--module-path") and i + 1 < len(flat_jvm_args):
                for path_str in flat_jvm_args[i + 1].split(_classpath_separator()):
                    if path_str.endswith(".jar"):
                        module_path_jars.add(Path(path_str).name)

    classpath_entries = [str(p) for p in vanilla.libraries] + [
        str(p) for p in loader.extra_classpath
    ]
    if not is_forge_like:
        classpath_entries.insert(0, str(vanilla.client_jar))

    # De-duplicate while preserving order, and filter out module-path jars.
    seen: set[str] = set()
    classpath = []
    for entry in classpath_entries:
        if Path(entry).name in module_path_jars:
            continue
        if entry not in seen:
            seen.add(entry)
            classpath.append(entry)

    # Set the finalized classpath
    values["classpath"] = _classpath_separator().join(classpath)

    argv: list[str] = [str(java_binary)]

    argv.extend(
        [
            "-Duser.language=en",
            "-Dminecraft.api.auth.host=https://nope.invalid",
            "-Dminecraft.api.account.host=https://nope.invalid",
            "-Dminecraft.api.session.host=https://nope.invalid",
            "-Dminecraft.api.services.host=https://nope.invalid",
        ]
    )
    argv.append(f"-Xms{settings.min_memory_mb}M")
    argv.append(f"-Xmx{settings.max_memory_mb}M")
    if settings.extra_jvm_args:
        argv.extend(settings.extra_jvm_args)

    # JVM args: prefer the loader profile's (Forge/NeoForge/Fabric often
    # add module-system opens/exports that are required for the game to
    # even boot), falling back to vanilla's own if the loader didn't
    # specify any (Fabric/Quilt profiles usually don't need extra JVM
    # args beyond vanilla's).
    jvm_arg_source = loader.extra_jvm_arguments or vanilla.jvm_arguments
    if jvm_arg_source:
        argv.extend(_flatten_modern_args(jvm_arg_source, values))
    else:
        argv.append(f"-Djava.library.path={vanilla.natives_dir}")
        argv.extend(["-cp", values["classpath"]])

    if "-cp" not in argv and "--class-path" not in argv:
        argv.extend(["-cp", values["classpath"]])

    argv.append(loader.main_class)

    if vanilla.game_arguments:
        argv.extend(_flatten_modern_args(vanilla.game_arguments, values))
    elif vanilla.minecraft_arguments:
        argv.extend(_substitute(vanilla.minecraft_arguments, values).split())

    if loader.extra_game_arguments:
        argv.extend(_flatten_modern_args(loader.extra_game_arguments, values))

    if settings.fullscreen:
        argv.append("--fullscreen")

    return argv


def launch_instance(
    java_binary: Path,
    instance: Instance,
    account: Account,
    settings: LaunchSettings,
    *,
    on_output: Callable[[str], None] | None = None,
) -> subprocess.Popen:
    """Ensures the vanilla+loader pieces for this instance are installed
    (cheap no-op if already cached) and spawns the game process.

    Returns the running Popen immediately; does not block until exit. If
    ``on_output`` is given, a background thread streams the process's
    combined stdout/stderr to it line-by-line -- callers that just want to
    fire-and-forget can omit it.
    """
    meta = instance.metadata
    vanilla = install_vanilla_version(
        meta.minecraft_version, minecraft_root=instance.minecraft_dir
    )
    loader = install_loader(
        meta.loader_id, meta.minecraft_version, meta.loader_version, instance.path, java_binary
    )

    if account.type is AccountType.PREMIUM and not account.access_token:
        raise LaunchError(f"Account {account.username} has no valid access token.")

    argv = build_launch_command(java_binary, instance, vanilla, loader, account, settings)
    if meta.loader_id == "neoforge":
        required_loader_flags = {
            "--fml.neoForgeVersion",
            "--fml.fmlVersion",
            "--fml.mcVersion",
            "--fml.neoFormVersion",
            "--launchTarget",
        }
        missing_loader_flags = sorted(
            flag for flag in required_loader_flags if flag not in argv
        )
        if missing_loader_flags:
            raise LaunchError(
                "NeoForge launch command is incomplete; missing: "
                + ", ".join(missing_loader_flags)
                + ". Rebuild or reinstall the launcher profile."
            )
    logger.info("Launching command (%d args): %s", len(argv), " ".join(argv))

    env = os.environ.copy()
    process = subprocess.Popen(  # noqa: S603
        argv,
        cwd=str(instance.minecraft_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    if on_output is not None:
        import threading

        def _pump() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                on_output(line.rstrip("\n"))

        threading.Thread(target=_pump, daemon=True).start()

    import time

    instance.metadata.last_played_at = time.time()
    instance.save()

    return process
