"""Cross-platform filesystem locations for the launcher.

Keeps all "where do we put things" logic in one place so the rest of the
codebase never has to think about platform differences.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ..constants import APP_NAME


def _windows_appdata() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base)
    return Path.home() / "AppData" / "Roaming"


def data_dir() -> Path:
    """Root directory for persistent launcher data (instances, accounts,
    cached JREs, logs). Created on first access.
    """
    if sys.platform == "win32":
        root = _windows_appdata() / APP_NAME
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg_data) if xdg_data else Path.home() / ".local" / "share"
        root = base / APP_NAME.lower()

    root.mkdir(parents=True, exist_ok=True)
    return root


def config_dir() -> Path:
    """Root directory for user configuration/settings/accounts."""
    if sys.platform == "win32":
        root = _windows_appdata() / APP_NAME
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Preferences" / APP_NAME
    else:
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg_config) if xdg_config else Path.home() / ".config"
        root = base / APP_NAME.lower()

    root.mkdir(parents=True, exist_ok=True)
    return root


def cache_dir() -> Path:
    """Root directory for re-downloadable cached content (JREs, libraries,
    mrpack downloads, Modrinth API cache).
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        root = (Path(base) if base else _windows_appdata()) / APP_NAME / "cache"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Caches" / APP_NAME
    else:
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        base = Path(xdg_cache) if xdg_cache else Path.home() / ".cache"
        root = base / APP_NAME.lower()

    root.mkdir(parents=True, exist_ok=True)
    return root


def instances_dir() -> Path:
    root = data_dir() / "instances"
    root.mkdir(parents=True, exist_ok=True)
    return root


def accounts_file() -> Path:
    return config_dir() / "accounts.json"


def settings_file() -> Path:
    return config_dir() / "settings.json"


def jre_dir() -> Path:
    root = cache_dir() / "jre"
    root.mkdir(parents=True, exist_ok=True)
    return root


def libraries_dir() -> Path:
    root = cache_dir() / "libraries"
    root.mkdir(parents=True, exist_ok=True)
    return root


def assets_dir() -> Path:
    root = cache_dir() / "assets"
    root.mkdir(parents=True, exist_ok=True)
    return root


def mrpack_cache_dir() -> Path:
    root = cache_dir() / "mrpack"
    root.mkdir(parents=True, exist_ok=True)
    return root


def logs_dir() -> Path:
    root = data_dir() / "logs"
    root.mkdir(parents=True, exist_ok=True)
    return root
