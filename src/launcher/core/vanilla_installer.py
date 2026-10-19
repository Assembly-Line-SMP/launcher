"""Installs the vanilla Minecraft pieces that every instance needs
underneath its mod loader: the client jar, library jars, native libraries,
and the asset index + objects.

This follows Mojang's public piston-meta version manifest format, the same
one the official launcher and every third-party launcher (PrismLauncher,
MultiMC, etc.) consume.
"""

from __future__ import annotations

import json
import logging
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..constants import (
    PISTON_META_MANIFEST,
    PISTON_META_RESOURCES,
    REQUEST_TIMEOUT_SECONDS,
    USER_AGENT,
)
from ..core.downloader import DownloadTask, ProgressCallback, download_many
from ..util.paths import assets_dir, libraries_dir

logger = logging.getLogger(__name__)


class VanillaInstallError(RuntimeError):
    pass


@dataclass
class VanillaVersionInfo:
    """Everything the launch command builder needs from the vanilla side."""

    version_id: str
    client_jar: Path
    main_class: str
    libraries: list[Path]
    natives_dir: Path
    asset_index_id: str
    assets_dir: Path
    minecraft_arguments: str | None  # legacy (<1.13) single-string args
    game_arguments: list[Any] = field(default_factory=list)  # modern structured args
    jvm_arguments: list[Any] = field(default_factory=list)


def _client() -> httpx.Client:
    return httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})


def _current_os_name() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "osx"
    return "linux"


def _rule_applies(rule: dict[str, Any]) -> bool:
    action_allow = rule.get("action", "allow") == "allow"
    os_rule = rule.get("os")
    if os_rule:
        name = os_rule.get("name")
        if name and name != _current_os_name():
            return not action_allow

    features_rule = rule.get("features")
    if features_rule:
        active_features = {
            "has_custom_resolution": True,
            "is_demo_user": False,
            "has_quick_play_singleplayer": False,
            "has_quick_play_multiplayer": False,
            "has_quick_play_realms": False,
        }
        for feature_name, expected_val in features_rule.items():
            if active_features.get(feature_name, False) != expected_val:
                return not action_allow

    return action_allow


def _rules_allow(rules: list[dict[str, Any]] | None) -> bool:
    if not rules:
        return True
    # Later rules override earlier ones; default is disallow if any rule
    # exists and none explicitly allow the current platform.
    allowed = False
    for rule in rules:
        if _rule_applies(rule):
            allowed = rule.get("action", "allow") == "allow"
    return allowed


def _fetch_version_manifest() -> dict[str, Any]:
    with _client() as client:
        resp = client.get(PISTON_META_MANIFEST)
        if resp.status_code != 200:
            raise VanillaInstallError(
                f"Failed to fetch Minecraft version manifest (HTTP {resp.status_code})."
            )
        return resp.json()


def _fetch_version_meta(version_id: str) -> dict[str, Any]:
    manifest = _fetch_version_manifest()
    entry = next((v for v in manifest["versions"] if v["id"] == version_id), None)
    if entry is None:
        raise VanillaInstallError(
            f"Minecraft version '{version_id}' was not found in Mojang's "
            "version manifest."
        )
    with _client() as client:
        resp = client.get(entry["url"])
        if resp.status_code != 200:
            raise VanillaInstallError(
                f"Failed to fetch version metadata for {version_id} "
                f"(HTTP {resp.status_code})."
            )
        return resp.json()


def _library_download_tasks(
    libraries: list[dict[str, Any]], libs_root: Path, natives_root: Path
) -> tuple[list[DownloadTask], list[Path], list[tuple[Path, Path]]]:
    """Returns (download_tasks, classpath_jar_paths, native_zip_extractions).

    native_zip_extractions is a list of (zip_path, natives_root) pairs to
    unpack after download -- native libraries ship as jars-that-are-really-
    zips containing platform .dll/.so/.dylib files.
    """
    tasks: list[DownloadTask] = []
    classpath: list[Path] = []
    natives_to_extract: list[tuple[Path, Path]] = []

    for lib in libraries:
        if not _rules_allow(lib.get("rules")):
            continue

        downloads = lib.get("downloads", {})

        artifact = downloads.get("artifact")
        if artifact:
            dest = libs_root / artifact["path"]
            tasks.append(
                DownloadTask(
                    url=artifact["url"],
                    destination=dest,
                    label=lib["name"],
                    sha1=artifact.get("sha1"),
                    expected_size=artifact.get("size"),
                )
            )
            classpath.append(dest)

        classifiers = downloads.get("classifiers", {})
        natives_map = lib.get("natives", {})
        native_key = natives_map.get(_current_os_name()) if natives_map else None
        if native_key and native_key in classifiers:
            native_info = classifiers[native_key]
            dest = libs_root / native_info["path"]
            tasks.append(
                DownloadTask(
                    url=native_info["url"],
                    destination=dest,
                    label=f"{lib['name']} (natives)",
                    sha1=native_info.get("sha1"),
                    expected_size=native_info.get("size"),
                )
            )
            natives_to_extract.append((dest, natives_root))

    return tasks, classpath, natives_to_extract


def _extract_natives(pairs: list[tuple[Path, Path]]) -> None:
    import zipfile

    for zip_path, natives_root in pairs:
        natives_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                # Skip META-INF and directories; natives archives are flat
                # collections of shared libraries at the root.
                if info.is_dir() or info.filename.startswith("META-INF"):
                    continue
                zf.extract(info, natives_root)


def _asset_download_tasks(
    asset_index: dict[str, Any], objects_root: Path
) -> list[DownloadTask]:
    tasks = []
    for obj in asset_index.get("objects", {}).values():
        obj_hash = obj["hash"]
        subdir = obj_hash[:2]
        dest = objects_root / subdir / obj_hash
        tasks.append(
            DownloadTask(
                url=f"{PISTON_META_RESOURCES}/{subdir}/{obj_hash}",
                destination=dest,
                label=f"asset {obj_hash[:8]}",
                sha1=obj_hash,
                expected_size=obj.get("size"),
            )
        )
    return tasks


def install_vanilla_version(
    version_id: str,
    *,
    progress: ProgressCallback | None = None,
    minecraft_root: Path | None = None,
) -> VanillaVersionInfo:
    """Ensure the vanilla client jar, all required libraries, natives, and
    the asset index/objects for ``version_id`` are present in the shared
    cache, and return everything the launch command builder needs.

    Everything here is content-addressed and shared across instances --
    installing two modpacks that both target 1.20.1 only downloads the
    vanilla 1.20.1 pieces once.
    """
    meta = _fetch_version_meta(version_id)

    libs_root = libraries_dir()
    natives_root = libraries_dir().parent / "natives" / version_id
    version_cache_root = libraries_dir().parent / "versions" / version_id
    version_cache_root.mkdir(parents=True, exist_ok=True)

    # Client jar.
    client_download = meta["downloads"]["client"]
    client_jar_path = version_cache_root / "client.jar"
    client_task = DownloadTask(
        url=client_download["url"],
        destination=client_jar_path,
        label=f"Minecraft {version_id} client",
        sha1=client_download.get("sha1"),
        expected_size=client_download.get("size"),
    )

    lib_tasks, classpath, native_extractions = _library_download_tasks(
        meta.get("libraries", []), libs_root, natives_root
    )

    # Asset index.
    asset_index_meta = meta["assetIndex"]
    asset_index_id = asset_index_meta["id"]
    objects_root = assets_dir() / "objects"
    indexes_root = assets_dir() / "indexes"
    indexes_root.mkdir(parents=True, exist_ok=True)
    asset_index_path = indexes_root / f"{asset_index_id}.json"

    with _client() as client:
        resp = client.get(asset_index_meta["url"])
        if resp.status_code != 200:
            raise VanillaInstallError("Failed to fetch asset index.")
        asset_index_data = resp.json()
    asset_index_path.write_text(json.dumps(asset_index_data), encoding="utf-8")

    asset_tasks = _asset_download_tasks(asset_index_data, objects_root)

    all_tasks = [client_task, *lib_tasks, *asset_tasks]
    logger.info(
        "Installing vanilla %s: %d libraries, %d assets",
        version_id,
        len(lib_tasks),
        len(asset_tasks),
    )
    download_many(all_tasks, progress=progress)
    _extract_natives(native_extractions)

    if minecraft_root is not None:
        profile_dir = minecraft_root / "versions" / version_id
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / f"{version_id}.json").write_text(
            json.dumps(meta), encoding="utf-8"
        )

    return VanillaVersionInfo(
        version_id=version_id,
        client_jar=client_jar_path,
        main_class=meta["mainClass"],
        libraries=classpath,
        natives_dir=natives_root,
        asset_index_id=asset_index_id,
        assets_dir=assets_dir(),
        minecraft_arguments=meta.get("minecraftArguments"),
        game_arguments=meta.get("arguments", {}).get("game", []),
        jvm_arguments=meta.get("arguments", {}).get("jvm", []),
    )
