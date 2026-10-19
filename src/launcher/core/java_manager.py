"""Automatic Java runtime provisioning via the Eclipse Adoptium API.

The launcher never requires the user to have Java installed. Instead, for
whichever Java major version a modpack's Minecraft version needs, we
download and unpack a matching Temurin JRE from Adoptium into the cache
directory, and reuse it across instances that happen to need the same
major version.

API reference: https://api.adoptium.net/q/swagger-ui/
"""

from __future__ import annotations

import logging
import platform
import shutil
import tarfile
import zipfile
from pathlib import Path

import httpx

from ..constants import ADOPTIUM_API_BASE, REQUEST_TIMEOUT_SECONDS, USER_AGENT
from .downloader import DownloadTask, ProgressCallback, download_file
from ..util.paths import jre_dir

logger = logging.getLogger(__name__)


class JavaProvisioningError(RuntimeError):
    pass


# Minecraft's own Java version requirements by game version range. This
# mirrors what the vanilla launcher enforces; modpacks generally follow the
# same requirement as their base Minecraft version.
def required_java_major(minecraft_version: str) -> int:
    try:
        parts = [int(p) for p in minecraft_version.split(".")[:2]]
        major, minor = (parts + [0, 0])[:2]
    except ValueError:
        return 21  # unknown/snapshot version string; assume newest

    if major != 1:
        return 21
    if minor >= 20:
        return 21
    if minor >= 18:
        return 17
    if minor >= 17:
        return 16
    return 8


def _adoptium_os() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "mac"
    return "linux"


def _adoptium_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x64"
    if machine in ("aarch64", "arm64"):
        return "aarch64"
    if machine.startswith("arm"):
        return "arm"
    raise JavaProvisioningError(f"Unsupported CPU architecture: {machine}")


def _install_root(java_major: int) -> Path:
    return jre_dir() / f"jre-{java_major}-{_adoptium_os()}-{_adoptium_arch()}"


def _java_bin_name() -> str:
    return "java.exe" if _adoptium_os() == "windows" else "java"


def find_java_binary(java_major: int) -> Path | None:
    """Return the java executable for an already-installed JRE of this
    major version, or None if it isn't installed yet.
    """
    root = _install_root(java_major)
    if not root.exists():
        return None
    matches = list(root.rglob(_java_bin_name()))
    # Prefer a match inside a "bin" directory (skip stray same-named files).
    for m in matches:
        if m.parent.name == "bin":
            return m
    return matches[0] if matches else None


def _adoptium_download_url(java_major: int) -> tuple[str, str]:
    """Returns (download_url, archive_filename) for the latest GA Temurin
    JRE binary matching this platform.
    """
    os_name = _adoptium_os()
    arch = _adoptium_arch()
    url = (
        f"{ADOPTIUM_API_BASE}/assets/latest/{java_major}/hotspot"
        f"?architecture={arch}&image_type=jre&os={os_name}&vendor=eclipse"
    )
    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}) as client:
        resp = client.get(url)
        if resp.status_code != 200:
            raise JavaProvisioningError(
                f"Failed to look up a Java {java_major} runtime for this "
                f"platform (HTTP {resp.status_code})."
            )
        data = resp.json()

    if not data:
        raise JavaProvisioningError(
            f"Adoptium has no Java {java_major} JRE build for "
            f"{os_name}/{arch}."
        )

    binary = data[0]["binary"]
    package = binary["package"]
    return package["link"], package["name"]


def _extract_archive(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if archive_path.suffix == ".zip":
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(destination)
    else:
        with tarfile.open(archive_path) as tf:
            tf.extractall(destination)  # noqa: S202 - trusted Adoptium source


def ensure_java(
    java_major: int, *, progress: ProgressCallback | None = None
) -> Path:
    """Ensure a Temurin JRE of the given major version is installed and
    return the path to its java executable, downloading it first if
    necessary.
    """
    existing = find_java_binary(java_major)
    if existing is not None:
        logger.debug("Java %d already provisioned at %s", java_major, existing)
        return existing

    logger.info("Provisioning Java %d runtime via Adoptium", java_major)
    url, archive_name = _adoptium_download_url(java_major)

    install_root = _install_root(java_major)
    archive_path = jre_dir() / archive_name

    task = DownloadTask(
        url=url,
        destination=archive_path,
        label=f"Java {java_major} runtime",
    )
    try:
        download_file(task, progress=progress, force=True)
        if not archive_path.is_file():
            raise JavaProvisioningError(
                f"Java {java_major} download completed without creating "
                f"the archive {archive_path.name}."
            )
        _extract_archive(archive_path, install_root)
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        raise JavaProvisioningError(
            f"Could not install the downloaded Java {java_major} runtime: {exc}"
        ) from exc
    finally:
        archive_path.unlink(missing_ok=True)

    if _adoptium_os() != "windows":
        # Archives from Adoptium already set the exec bit correctly, but
        # extraction through some zip/tar implementations can lose it.
        for candidate in install_root.rglob(_java_bin_name()):
            candidate.chmod(candidate.stat().st_mode | 0o111)

    java_bin = find_java_binary(java_major)
    if java_bin is None:
        shutil.rmtree(install_root, ignore_errors=True)
        raise JavaProvisioningError(
            f"Downloaded Java {java_major} runtime but couldn't find the "
            "java executable inside it."
        )
    return java_bin
