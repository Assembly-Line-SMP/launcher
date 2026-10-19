"""Generic download helpers used by the mrpack installer, JRE provisioner,
and asset/library fetchers.

Kept dependency-light and callback-based (rather than tied to Qt signals)
so it's usable from CLI code and tests without pulling in PySide6.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..constants import (
    DOWNLOAD_CHUNK_SIZE,
    MAX_PARALLEL_DOWNLOADS,
    REQUEST_TIMEOUT_SECONDS,
    USER_AGENT,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, int, int], None]
"""(label, bytes_done, bytes_total) -> None. Called frequently; keep it fast."""


class DownloadError(RuntimeError):
    pass


@dataclass
class DownloadTask:
    url: str
    destination: Path
    label: str
    sha1: str | None = None
    sha512: str | None = None
    expected_size: int | None = None


def _hash_matches(path: Path, sha1: str | None, sha512: str | None) -> bool:
    if not sha1 and not sha512:
        return path.is_file()
    if not path.exists():
        return False

    h1 = hashlib.sha1() if sha1 else None  # noqa: S324
    h512 = hashlib.sha512() if sha512 else None
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(DOWNLOAD_CHUNK_SIZE), b""):
            if h1:
                h1.update(chunk)
            if h512:
                h512.update(chunk)

    if sha1 and h1 and h1.hexdigest().lower() != sha1.lower():
        return False
    return not (sha512 and h512 and h512.hexdigest().lower() != sha512.lower())


def download_file(
    task: DownloadTask,
    *,
    progress: ProgressCallback | None = None,
    force: bool = False,
) -> Path:
    """Download a single file to task.destination, skipping the request
    entirely if a hash-matching copy already exists on disk (cache hit).
    """
    task.destination.parent.mkdir(parents=True, exist_ok=True)

    if not force and _hash_matches(task.destination, task.sha1, task.sha512):
        logger.debug("Cache hit, skipping download: %s", task.label)
        if progress and task.expected_size:
            progress(task.label, task.expected_size, task.expected_size)
        return task.destination

    tmp_path = task.destination.with_suffix(task.destination.suffix + ".part")

    with httpx.stream(
        "GET",
        task.url,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as resp:
        if resp.status_code != 200:
            raise DownloadError(
                f"Failed to download {task.label}: HTTP {resp.status_code}"
            )
        total = int(resp.headers.get("Content-Length", task.expected_size or 0))
        done = 0
        with tmp_path.open("wb") as fh:
            for chunk in resp.iter_bytes(DOWNLOAD_CHUNK_SIZE):
                fh.write(chunk)
                done += len(chunk)
                if progress:
                    progress(task.label, done, total)

    if not _hash_matches(tmp_path, task.sha1, task.sha512):
        tmp_path.unlink(missing_ok=True)
        raise DownloadError(f"Hash mismatch after downloading {task.label}.")

    shutil.move(str(tmp_path), str(task.destination))
    return task.destination


def download_many(
    tasks: Iterable[DownloadTask],
    *,
    progress: ProgressCallback | None = None,
    max_workers: int = MAX_PARALLEL_DOWNLOADS,
    force: bool = False,
) -> list[Path]:
    """Download several files in parallel. Raises the first DownloadError
    encountered (after letting already-started downloads finish) rather
    than leaving the caller guessing which of many tasks failed silently.
    """
    tasks = list(tasks)
    results: list[Path | None] = [None] * len(tasks)
    errors: list[BaseException] = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_index = {
            pool.submit(download_file, task, progress=progress, force=force): i
            for i, task in enumerate(tasks)
        }
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            try:
                results[index] = future.result()
            except BaseException as exc:  # noqa: BLE001
                logger.error("Download failed: %s (%s)", tasks[index].label, exc)
                errors.append(exc)

    if errors:
        raise errors[0]

    return results  # type: ignore[return-value]
