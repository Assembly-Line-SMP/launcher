"""Persistent, user-editable launcher settings (memory allocation, window
size, extra JVM args). Separate from account data, which lives in
AccountStore.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from .launch import LaunchSettings
from ..util.paths import settings_file

logger = logging.getLogger(__name__)


@dataclass
class AppSettings:
    launch: LaunchSettings
    check_for_updates_on_start: bool = True
    close_launcher_on_game_start: bool = False

    def to_dict(self) -> dict:
        return {
            "launch": asdict(self.launch),
            "check_for_updates_on_start": self.check_for_updates_on_start,
            "close_launcher_on_game_start": self.close_launcher_on_game_start,
        }

    @staticmethod
    def from_dict(data: dict) -> AppSettings:
        launch_data = data.get("launch", {})
        return AppSettings(
            launch=LaunchSettings(
                min_memory_mb=launch_data.get("min_memory_mb", 2048),
                max_memory_mb=launch_data.get("max_memory_mb", 4096),
                extra_jvm_args=launch_data.get("extra_jvm_args"),
                window_width=launch_data.get("window_width", 1280),
                window_height=launch_data.get("window_height", 720),
                fullscreen=launch_data.get("fullscreen", False),
            ),
            check_for_updates_on_start=data.get("check_for_updates_on_start", True),
            close_launcher_on_game_start=data.get("close_launcher_on_game_start", False),
        )

    @staticmethod
    def default() -> AppSettings:
        return AppSettings(launch=LaunchSettings())


def load_settings(path: Path | None = None) -> AppSettings:
    path = path or settings_file()
    if not path.exists():
        return AppSettings.default()
    try:
        return AppSettings.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, TypeError, KeyError) as exc:
        logger.error("Failed to load settings from %s, using defaults: %s", path, exc)
        return AppSettings.default()


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    path = path or settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings.to_dict(), indent=2), encoding="utf-8")
