"""Installing Modrinth's .mrpack format.

An .mrpack file is a zip containing:

    modrinth.index.json   -- manifest: name, mc version, mod loader +
                              version, and a list of {path, downloads[],
                              hashes, env} for every file that must be
                              fetched (mods, resource packs, shader packs).
    overrides/             -- files copied verbatim into the instance
                              (configs, etc.)
    client-overrides/      -- like overrides/, but client-only
    server-overrides/      -- like overrides/, but server-only (ignored;
                              this launcher only ever installs the client)

Spec: https://docs.modrinth.com/docs/modpacks/format_definition/

Installing a version means: download the .mrpack, read the index, download
every referenced file into the instance's mods/resourcepacks/shaderpacks
folders (skipping files not applicable to "client" env), then extract
overrides on top.
"""

from __future__ import annotations

import json
import logging
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.downloader import DownloadTask, ProgressCallback, download_many
from ..core.modrinth import ModrinthVersion
from ..util.paths import mrpack_cache_dir

logger = logging.getLogger(__name__)


class MrpackError(RuntimeError):
    pass


@dataclass
class MrpackDependencyInfo:
    minecraft_version: str
    loader_id: str  # "forge", "neoforge", "fabric-loader", "quilt-loader"
    loader_version: str


@dataclass
class MrpackManifest:
    name: str
    version_id: str
    dependencies: MrpackDependencyInfo
    files: list[dict[str, Any]]
    format_version: int


_LOADER_DEPENDENCY_KEYS = {
    "forge": "forge",
    "neoforge": "neoforge",
    "fabric-loader": "fabric-loader",
    "quilt-loader": "quilt-loader",
}


def _parse_manifest(raw: dict[str, Any]) -> MrpackManifest:
    deps: dict[str, str] = raw.get("dependencies", {})
    mc_version = deps.get("minecraft")
    if not mc_version:
        raise MrpackError("modrinth.index.json is missing a Minecraft version.")

    loader_id = None
    loader_version = None
    for key, name in _LOADER_DEPENDENCY_KEYS.items():
        if key in deps:
            loader_id = name
            loader_version = deps[key]
            break

    if loader_id is None:
        raise MrpackError(
            "modrinth.index.json does not declare a supported mod loader "
            "(expected one of: forge, neoforge, fabric-loader, quilt-loader)."
        )

    return MrpackManifest(
        name=raw.get("name", "modpack"),
        version_id=raw.get("versionId", ""),
        dependencies=MrpackDependencyInfo(
            minecraft_version=mc_version,
            loader_id=loader_id,
            loader_version=loader_version,
        ),
        files=raw.get("files", []),
        format_version=raw.get("formatVersion", 1),
    )


def download_mrpack(version: ModrinthVersion, *, progress: ProgressCallback | None = None) -> Path:
    file = version.primary_file
    if file is None or not file.filename.endswith(".mrpack"):
        raise MrpackError(f"Version {version.version_number} has no .mrpack file.")

    destination = mrpack_cache_dir() / f"{version.id}-{file.filename}"
    task = DownloadTask(
        url=file.url,
        destination=destination,
        label=file.filename,
        sha1=file.sha1 or None,
        sha512=file.sha512 or None,
        expected_size=file.size,
    )
    from launcher.core.downloader import download_file

    return download_file(task, progress=progress)


def read_manifest(mrpack_path: Path) -> MrpackManifest:
    with zipfile.ZipFile(mrpack_path) as zf:
        try:
            raw = json.loads(zf.read("modrinth.index.json"))
        except KeyError as exc:
            raise MrpackError(
                f"{mrpack_path.name} is not a valid .mrpack (missing "
                "modrinth.index.json)."
            ) from exc
    return _parse_manifest(raw)


def _file_applies_to_client(entry: dict[str, Any]) -> bool:
    env = entry.get("env", {})
    # "required", "optional", or "unsupported". Treat missing env as
    # required (older/minimal manifests may omit it).
    return env.get("client", "required") != "unsupported"


def _pick_download_url(entry: dict[str, Any]) -> str:
    downloads = entry.get("downloads", [])
    if not downloads:
        raise MrpackError(f"File entry {entry.get('path')} has no download URLs.")
    return downloads[0]


def install_mrpack(
    mrpack_path: Path,
    instance_dir: Path,
    *,
    progress: ProgressCallback | None = None,
) -> MrpackManifest:
    """Install an .mrpack into instance_dir: download referenced files,
    extract overrides. instance_dir is created if needed.

    Existing content that isn't part of this version's file list is left
    alone -- we don't wipe the instance, so player-generated content
    (saves, screenshots, options.txt tweaks) survives a modpack update.
    """
    instance_dir.mkdir(parents=True, exist_ok=True)
    manifest = read_manifest(mrpack_path)

    tasks: list[DownloadTask] = []
    for entry in manifest.files:
        if not _file_applies_to_client(entry):
            continue
        rel_path = entry["path"]
        hashes = entry.get("hashes", {})
        tasks.append(
            DownloadTask(
                url=_pick_download_url(entry),
                destination=instance_dir / rel_path,
                label=rel_path,
                sha1=hashes.get("sha1"),
                sha512=hashes.get("sha512"),
                expected_size=entry.get("fileSize"),
            )
        )

    logger.info("Installing %d files for %s", len(tasks), manifest.name)
    if tasks:
        download_many(tasks, progress=progress)

    _extract_overrides(mrpack_path, instance_dir)
    return manifest


def _extract_overrides(mrpack_path: Path, instance_dir: Path) -> None:
    """Extract overrides/ and client-overrides/ on top of the instance
    directory, stripping the prefix. server-overrides/ is intentionally
    skipped -- this launcher only ever produces a client install.
    """
    prefixes = ("overrides/", "client-overrides/")
    with zipfile.ZipFile(mrpack_path) as zf:
        for info in zf.infolist():
            matched_prefix = next((p for p in prefixes if info.filename.startswith(p)), None)
            if matched_prefix is None:
                continue
            rel = info.filename[len(matched_prefix):]
            if not rel:
                continue
            target = instance_dir / rel
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
