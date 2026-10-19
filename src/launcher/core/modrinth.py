"""Client for the Modrinth v2 API, scoped to what the launcher needs.

The launcher targets a *server* project (assembly-line-smp), not a
standalone modpack. Modrinth exposes the same /project and
/project/{id}/version endpoints for both project types -- server projects
just additionally carry a "required content" link to the modpack project
that defines their .mrpack. In practice, versions of the server project
itself carry the .mrpack file the same way modpack versions do, so the
version-listing/download logic below is written against the general
project/version schema and works for either.

Reference: https://docs.modrinth.com/api/
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from ..constants import (
    MODRINTH_API_BASE,
    MODRINTH_PROJECT_SLUG,
    REQUEST_TIMEOUT_SECONDS,
    USER_AGENT,
)

logger = logging.getLogger(__name__)


class ModrinthError(RuntimeError):
    pass


@dataclass
class ModrinthFile:
    filename: str
    url: str
    sha1: str
    sha512: str
    size: int
    primary: bool


@dataclass
class ModrinthVersion:
    id: str
    project_id: str
    name: str
    version_number: str
    changelog: str
    date_published: str
    game_versions: list[str]
    loaders: list[str]
    files: list[ModrinthFile]

    @property
    def primary_file(self) -> ModrinthFile | None:
        for f in self.files:
            if f.primary:
                return f
        return self.files[0] if self.files else None

    @property
    def is_mrpack(self) -> bool:
        f = self.primary_file
        return f is not None and f.filename.endswith(".mrpack")


@dataclass
class ModrinthProject:
    id: str
    slug: str
    title: str
    description: str
    icon_url: str | None
    project_type: str


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=MODRINTH_API_BASE,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )


def _parse_file(data: dict[str, Any]) -> ModrinthFile:
    hashes = data.get("hashes", {})
    return ModrinthFile(
        filename=data["filename"],
        url=data["url"],
        sha1=hashes.get("sha1", ""),
        sha512=hashes.get("sha512", ""),
        size=data.get("size", 0),
        primary=data.get("primary", False),
    )


def _parse_version(data: dict[str, Any]) -> ModrinthVersion:
    return ModrinthVersion(
        id=data["id"],
        project_id=data["project_id"],
        name=data.get("name", data["version_number"]),
        version_number=data["version_number"],
        changelog=data.get("changelog") or "",
        date_published=data.get("date_published", ""),
        game_versions=data.get("game_versions", []),
        loaders=data.get("loaders", []),
        files=[_parse_file(f) for f in data.get("files", [])],
    )


class ModrinthClient:
    """Thin wrapper around the subset of the Modrinth API this launcher
    uses: fetch the project, list its versions, resolve the latest
    .mrpack, and (elsewhere) download it.
    """

    def __init__(self, project_slug: str = MODRINTH_PROJECT_SLUG) -> None:
        self.project_slug = project_slug

    def get_project(self) -> ModrinthProject:
        with _client() as client:
            resp = client.get(f"/project/{self.project_slug}")
            if resp.status_code == 404:
                raise ModrinthError(
                    f"Modrinth project '{self.project_slug}' was not found. "
                    "It may have been renamed or removed."
                )
            if resp.status_code != 200:
                raise ModrinthError(
                    f"Failed to fetch project info (HTTP {resp.status_code})."
                )
            data = resp.json()

        return ModrinthProject(
            id=data["id"],
            slug=data["slug"],
            title=data["title"],
            description=data.get("description", ""),
            icon_url=data.get("icon_url"),
            project_type=data.get("project_type", ""),
        )

    def list_versions(self) -> list[ModrinthVersion]:
        with _client() as client:
            resp = client.get(f"/project/{self.project_slug}/version")
            if resp.status_code != 200:
                raise ModrinthError(
                    f"Failed to fetch versions (HTTP {resp.status_code})."
                )
            data = resp.json()

        versions = [_parse_version(v) for v in data]
        # Modrinth returns versions newest-first already, but don't rely on
        # that implicitly -- sort defensively by date_published.
        versions.sort(key=lambda v: v.date_published, reverse=True)
        return versions

    def get_latest_version(
        self, *, loader: str | None = None, game_version: str | None = None
    ) -> ModrinthVersion:
        versions = self.list_versions()
        for v in versions:
            if loader and loader not in v.loaders:
                continue
            if game_version and game_version not in v.game_versions:
                continue
            if v.is_mrpack:
                return v
        raise ModrinthError(
            "No .mrpack version found for this project matching the "
            "requested filters."
        )

    def get_version(self, version_id: str) -> ModrinthVersion:
        with _client() as client:
            resp = client.get(f"/version/{version_id}")
            if resp.status_code == 404:
                raise ModrinthError(f"Version '{version_id}' not found.")
            if resp.status_code != 200:
                raise ModrinthError(
                    f"Failed to fetch version (HTTP {resp.status_code})."
                )
            return _parse_version(resp.json())
