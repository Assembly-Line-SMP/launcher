"""Top-level "make sure the instance matches the latest (or a chosen)
Modrinth version" orchestration.

This is what a "Play"/"Update" button in the GUI calls. It's synchronous
and progress-callback driven; the GUI runs it on a worker thread and
relays progress into the UI, rather than this module knowing anything
about Qt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .downloader import ProgressCallback
from .instance import Instance, InstanceManager, InstanceMetadata
from .java_manager import ensure_java, required_java_major
from .loader_installer import install_loader
from .modrinth import ModrinthClient, ModrinthVersion
from .mrpack import download_mrpack, install_mrpack, read_manifest
from .vanilla_installer import install_vanilla_version

logger = logging.getLogger(__name__)

# One stable instance id for "the" Assembly Line SMP install. If multi-pack
# support is ever added, this becomes derived from the project slug instead
# of hardcoded.
DEFAULT_INSTANCE_ID = "assembly-line-smp"


@dataclass
class SyncResult:
    instance: Instance
    java_binary: str
    updated: bool


def get_available_versions(project_slug: str | None = None) -> list[ModrinthVersion]:
    client = ModrinthClient(project_slug) if project_slug else ModrinthClient()
    return [v for v in client.list_versions() if v.is_mrpack]


def sync_instance(
    version: ModrinthVersion,
    *,
    instance_manager: InstanceManager | None = None,
    progress: ProgressCallback | None = None,
) -> SyncResult:
    """Ensure a local instance exists matching ``version``, downloading /
    installing anything missing: the .mrpack contents, the matching
    vanilla Minecraft files, the mod loader, and a Java runtime.

    Safe to call even when everything is already installed -- every step
    underneath is itself cache-aware, so a no-op sync is fast.
    """
    instance_manager = instance_manager or InstanceManager()
    existing = instance_manager.get(DEFAULT_INSTANCE_ID)
    already_current = (
        existing is not None
        and existing.metadata.modrinth_version_id == version.id
    )

    if already_current and existing is not None:
        java_binary = ensure_java(existing.metadata.java_major, progress=progress)
        return SyncResult(
            instance=existing,
            java_binary=str(java_binary),
            updated=False,
        )

    instance_path = instance_manager.get_or_create_path(DEFAULT_INSTANCE_ID)
    placeholder_metadata = InstanceMetadata(
        id=DEFAULT_INSTANCE_ID,
        name="Assembly Line SMP",
        modrinth_version_id=version.id,
        modrinth_version_number=version.version_number,
        minecraft_version="",
        loader_id="",
        loader_version="",
        java_major=21,
    )
    instance = Instance(instance_path, existing.metadata if existing else placeholder_metadata)

    logger.info("Syncing instance to Modrinth version %s", version.version_number)
    mrpack_path = download_mrpack(version, progress=progress)
    manifest = read_manifest(mrpack_path)

    install_mrpack(mrpack_path, instance.minecraft_dir, progress=progress)

    java_major = required_java_major(manifest.dependencies.minecraft_version)
    java_binary = ensure_java(java_major, progress=progress)

    install_vanilla_version(
        manifest.dependencies.minecraft_version,
        progress=progress,
        minecraft_root=instance.minecraft_dir,
    )
    install_loader(
        manifest.dependencies.loader_id,
        manifest.dependencies.minecraft_version,
        manifest.dependencies.loader_version,
        instance.path,
        java_binary,
        progress=progress,
    )

    instance.metadata = InstanceMetadata(
        id=DEFAULT_INSTANCE_ID,
        name=manifest.name,
        modrinth_version_id=version.id,
        modrinth_version_number=version.version_number,
        minecraft_version=manifest.dependencies.minecraft_version,
        loader_id=manifest.dependencies.loader_id,
        loader_version=manifest.dependencies.loader_version,
        java_major=java_major,
        created_at=instance.metadata.created_at,
        last_played_at=instance.metadata.last_played_at,
    )
    instance.save()

    return SyncResult(instance=instance, java_binary=str(java_binary), updated=not already_current)
