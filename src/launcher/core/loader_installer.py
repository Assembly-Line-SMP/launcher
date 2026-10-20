"""Mod loader profile resolution.

For Fabric and Quilt this is simple: both publish a "launcher meta" v2 API
that hands back a ready-to-use launch profile (main class + full library
list) for any (minecraft_version, loader_version) pair -- no local
installer JAR needs to run.

Forge and NeoForge are messier: they distribute a Java "installer" that
patches vanilla libraries and generates a version JSON locally. Running
that installer headlessly is supported (`-installClient`) but pulls in a
lot of surface area. To keep this launcher's first version shippable, we
support Fabric/Quilt fully and support NeoForge/Forge via their installer
JAR run as a subprocess, parsing the version JSON it produces the same way
PrismLauncher/MultiMC's Forge support works under the hood.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ..constants import REQUEST_TIMEOUT_SECONDS, USER_AGENT
from ..core.downloader import DownloadTask, ProgressCallback, download_file, download_many
from ..core.vanilla_installer import _rules_allow
from ..util.paths import libraries_dir

logger = logging.getLogger(__name__)

FABRIC_META_BASE = "https://meta.fabricmc.net/v2"
QUILT_META_BASE = "https://meta.quiltmc.org/v3"
NEOFORGE_MAVEN_BASE = "https://maven.neoforged.net/releases"
FORGE_MAVEN_BASE = "https://maven.minecraftforge.net"


class LoaderInstallError(RuntimeError):
    pass


@dataclass
class LoaderProfile:
    main_class: str
    extra_classpath: list[Path]
    extra_game_arguments: list[Any]
    extra_jvm_arguments: list[Any]
    profile_id: str | None = None


def _client() -> httpx.Client:
    return httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})


# -- Fabric / Quilt (same launcher-meta v2/v3 shape) -----------------------


def _install_fabric_like(
    meta_base: str,
    minecraft_version: str,
    loader_version: str,
    *,
    progress: ProgressCallback | None = None,
) -> LoaderProfile:
    with _client() as client:
        resp = client.get(
            f"{meta_base}/versions/loader/{minecraft_version}/{loader_version}/profile/json"
        )
        if resp.status_code != 200:
            raise LoaderInstallError(
                f"Failed to fetch Fabric/Quilt loader profile for "
                f"{minecraft_version}/{loader_version} (HTTP {resp.status_code})."
            )
        profile = resp.json()

    libs_root = libraries_dir()
    tasks: list[DownloadTask] = []
    classpath: list[Path] = []

    for lib in profile.get("libraries", []):
        if not _rules_allow(lib.get("rules")):
            continue
        name = lib["name"]
        url_base = lib.get("url", "https://maven.fabricmc.net/")
        rel_path = _maven_coords_to_path(name)
        dest = libs_root / rel_path
        tasks.append(
            DownloadTask(
                url=f"{url_base.rstrip('/')}/{rel_path}",
                destination=dest,
                label=name,
            )
        )
        classpath.append(dest)

    if tasks:
        download_many(tasks, progress=progress)

    return LoaderProfile(
        main_class=profile["mainClass"],
        extra_classpath=classpath,
        extra_game_arguments=profile.get("arguments", {}).get("game", []),
        extra_jvm_arguments=profile.get("arguments", {}).get("jvm", []),
    )


def install_fabric(
    minecraft_version: str, loader_version: str, *, progress: ProgressCallback | None = None
) -> LoaderProfile:
    return _install_fabric_like(
        FABRIC_META_BASE, minecraft_version, loader_version, progress=progress
    )


def install_quilt(
    minecraft_version: str, loader_version: str, *, progress: ProgressCallback | None = None
) -> LoaderProfile:
    return _install_fabric_like(
        QUILT_META_BASE, minecraft_version, loader_version, progress=progress
    )


def _maven_coords_to_path(coords: str) -> str:
    """'net.fabricmc:fabric-loader:0.15.0' -> maven repo relative path."""
    group, artifact, version, *classifier = coords.split(":")
    group_path = group.replace(".", "/")
    classifier_suffix = f"-{classifier[0]}" if classifier else ""
    filename = f"{artifact}-{version}{classifier_suffix}.jar"
    return f"{group_path}/{artifact}/{version}/{filename}"


# -- NeoForge / Forge (installer JAR) --------------------------------------


def _run_installer(
    installer_url: str,
    installer_filename: str,
    instance_root: Path,
    java_binary: Path,
    *,
    progress: ProgressCallback | None = None,
) -> Path:
    """Downloads an installer jar and runs it in headless client-install
    mode against a private "mc install root" inside the instance, then
    returns the path to the generated version JSON.
    """
    mc_root = instance_root / "minecraft"
    mc_root.mkdir(parents=True, exist_ok=True)

    versions_dir = mc_root / "versions"
    generated_profiles = [
        path
        for path in versions_dir.glob("*/*.json")
        if "forge" in path.stem.lower()
        and installer_filename.removesuffix("-installer.jar").lower()
        in path.stem.lower()
        and _profile_has_loader_arguments(path)
    ] if versions_dir.exists() else []
    if generated_profiles:
        return sorted(generated_profiles)[-1]

    from ..util.paths import cache_dir

    installer_path = cache_dir() / "installers" / installer_filename
    download_file(
        DownloadTask(url=installer_url, destination=installer_path, label=installer_filename),
        progress=progress,
    )

    launcher_profiles = mc_root / "launcher_profiles.json"
    if not launcher_profiles.exists():
        launcher_profiles.write_text(json.dumps({"profiles": {}}), encoding="utf-8")

    logger.info("Running loader installer: %s", installer_filename)
    result = subprocess.run(  # noqa: S603
        [str(java_binary), "-jar", str(installer_path), "--install-client", str(mc_root)],
        cwd=str(mc_root),
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        logger.error("Installer stdout: %s", result.stdout)
        logger.error("Installer stderr: %s", result.stderr)
        raise LoaderInstallError(
            f"{installer_filename} failed to install (exit code {result.returncode}). "
            f"Installer output: {(result.stderr or result.stdout).strip()[-2000:]}"
        )

    candidates = list(versions_dir.glob("*/*.json")) if versions_dir.exists() else []
    if not candidates:
        raise LoaderInstallError(
            f"{installer_filename} reported success but no version JSON was found."
        )
    generated = [c for c in candidates if "forge" in c.stem.lower()]
    generated = [c for c in generated if _profile_has_loader_arguments(c)]
    if not generated:
        raise LoaderInstallError(
            f"{installer_filename} installed without the required loader launch arguments."
        )
    # The installer-generated profile is the one that isn't the plain
    # vanilla version id (it has "forge"/"neoforge" in its id).
    return (generated or candidates)[0]


def _profile_has_loader_arguments(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        game_arguments = data.get("arguments", {}).get("game", [])
        return "--launchTarget" in game_arguments and "--fml.mcVersion" in game_arguments
    except (OSError, json.JSONDecodeError, TypeError):
        return False


def _read_installer_generated_profile(version_json_path: Path) -> LoaderProfile:
    data = json.loads(version_json_path.read_text(encoding="utf-8"))
    libs_root = libraries_dir()
    classpath: list[Path] = []
    tasks: list[DownloadTask] = []

    for lib in data.get("libraries", []):
        if not _rules_allow(lib.get("rules")):
            continue
        downloads = lib.get("downloads", {})
        artifact = downloads.get("artifact")
        if artifact and artifact.get("url"):
            dest = libs_root / artifact["path"]
            tasks.append(
                DownloadTask(
                    url=artifact["url"],
                    destination=dest,
                    label=lib["name"],
                    sha1=artifact.get("sha1"),
                )
            )
            classpath.append(dest)
        elif artifact:
            # Some Forge libraries are marked local-only (already placed by
            # the installer under the instance's own libraries dir).
            local_path = version_json_path.parent.parent.parent / "libraries" / artifact["path"]
            if local_path.exists():
                classpath.append(local_path)

    if tasks:
        download_many(tasks)

    return LoaderProfile(
        main_class=data["mainClass"],
        extra_classpath=classpath,
        extra_game_arguments=data.get("arguments", {}).get("game", []),
        extra_jvm_arguments=data.get("arguments", {}).get("jvm", []),
        profile_id=data.get("id", version_json_path.stem),
    )


def install_neoforge(
    minecraft_version: str,
    loader_version: str,
    instance_root: Path,
    java_binary: Path,
    *,
    progress: ProgressCallback | None = None,
) -> LoaderProfile:
    filename = f"neoforge-{loader_version}-installer.jar"
    url = f"{NEOFORGE_MAVEN_BASE}/net/neoforged/neoforge/{loader_version}/{filename}"
    version_json = _run_installer(
        url, filename, instance_root, java_binary, progress=progress
    )
    return _read_installer_generated_profile(version_json)


def install_forge(
    minecraft_version: str,
    loader_version: str,
    instance_root: Path,
    java_binary: Path,
    *,
    progress: ProgressCallback | None = None,
) -> LoaderProfile:
    full_version = f"{minecraft_version}-{loader_version}"
    filename = f"forge-{full_version}-installer.jar"
    url = f"{FORGE_MAVEN_BASE}/net/minecraftforge/forge/{full_version}/{filename}"
    version_json = _run_installer(
        url, filename, instance_root, java_binary, progress=progress
    )
    return _read_installer_generated_profile(version_json)


def install_loader(
    loader_id: str,
    minecraft_version: str,
    loader_version: str,
    instance_root: Path,
    java_binary: Path,
    *,
    progress: ProgressCallback | None = None,
) -> LoaderProfile:
    if loader_id == "fabric-loader":
        return install_fabric(minecraft_version, loader_version, progress=progress)
    if loader_id == "quilt-loader":
        return install_quilt(minecraft_version, loader_version, progress=progress)
    if loader_id == "neoforge":
        return install_neoforge(
            minecraft_version, loader_version, instance_root, java_binary, progress=progress
        )
    if loader_id == "forge":
        return install_forge(
            minecraft_version, loader_version, instance_root, java_binary, progress=progress
        )
    raise LoaderInstallError(f"Unsupported mod loader: {loader_id}")
