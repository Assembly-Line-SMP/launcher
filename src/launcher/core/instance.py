"""An "instance" is one locally installed copy of the modpack at a
specific Modrinth version. The launcher keeps exactly the instances the
user has installed (usually one -- "current" -- but updating in place vs.
keeping an old version side-by-side are both supported).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..util.paths import instances_dir

logger = logging.getLogger(__name__)

INSTANCE_METADATA_FILENAME = ".al_launcher_instance.json"


@dataclass
class InstanceMetadata:
    id: str
    name: str
    modrinth_version_id: str
    modrinth_version_number: str
    minecraft_version: str
    loader_id: str
    loader_version: str
    java_major: int
    created_at: float = field(default_factory=lambda: __import__("time").time())
    last_played_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> InstanceMetadata:
        return InstanceMetadata(**data)


class Instance:
    def __init__(self, path: Path, metadata: InstanceMetadata) -> None:
        self.path = path
        self.metadata = metadata

    @property
    def minecraft_dir(self) -> Path:
        """Where mods/, config/, saves/, options.txt etc. actually live.

        Kept as a subdirectory (rather than the instance root itself) so
        launcher-owned bookkeeping files never collide with modpack content
        or with the .minecraft tree that Forge/NeoForge installers expect
        to manage.
        """
        d = self.path / "minecraft"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save(self) -> None:
        meta_path = self.path / INSTANCE_METADATA_FILENAME
        meta_path.write_text(
            json.dumps(self.metadata.to_dict(), indent=2), encoding="utf-8"
        )

    @staticmethod
    def load(path: Path) -> Instance | None:
        meta_path = path / INSTANCE_METADATA_FILENAME
        if not meta_path.exists():
            return None
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            return Instance(path, InstanceMetadata.from_dict(data))
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.error("Corrupt instance metadata at %s: %s", meta_path, exc)
            return None


class InstanceManager:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or instances_dir()

    def list_instances(self) -> list[Instance]:
        if not self.root.exists():
            return []
        instances = []
        for child in self.root.iterdir():
            if not child.is_dir():
                continue
            instance = Instance.load(child)
            if instance:
                instances.append(instance)
        return sorted(instances, key=lambda i: i.metadata.created_at)

    def get(self, instance_id: str) -> Instance | None:
        for instance in self.list_instances():
            if instance.metadata.id == instance_id:
                return instance
        return None

    def get_or_create_path(self, instance_id: str) -> Path:
        path = self.root / instance_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def create_or_update(self, metadata: InstanceMetadata) -> Instance:
        path = self.get_or_create_path(metadata.id)
        instance = Instance(path, metadata)
        instance.save()
        return instance

    def remove(self, instance_id: str) -> None:
        import shutil

        path = self.root / instance_id
        if path.exists():
            shutil.rmtree(path)
