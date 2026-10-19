"""Settings dialog: memory allocation, window size, extra JVM args, misc
toggles.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)

from launcher.core.settings import AppSettings


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(380, 300)
        self._settings = settings

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.min_mem = QSpinBox()
        self.min_mem.setRange(512, 65536)
        self.min_mem.setSingleStep(512)
        self.min_mem.setSuffix(" MB")
        self.min_mem.setValue(settings.launch.min_memory_mb)
        form.addRow("Minimum memory:", self.min_mem)

        self.max_mem = QSpinBox()
        self.max_mem.setRange(512, 65536)
        self.max_mem.setSingleStep(512)
        self.max_mem.setSuffix(" MB")
        self.max_mem.setValue(settings.launch.max_memory_mb)
        form.addRow("Maximum memory:", self.max_mem)

        self.width = QSpinBox()
        self.width.setRange(320, 7680)
        self.width.setValue(settings.launch.window_width)
        form.addRow("Window width:", self.width)

        self.height = QSpinBox()
        self.height.setRange(240, 4320)
        self.height.setValue(settings.launch.window_height)
        form.addRow("Window height:", self.height)

        self.fullscreen = QCheckBox()
        self.fullscreen.setChecked(settings.launch.fullscreen)
        form.addRow("Start fullscreen:", self.fullscreen)

        self.extra_args = QLineEdit()
        self.extra_args.setText(" ".join(settings.launch.extra_jvm_args or []))
        self.extra_args.setPlaceholderText("-XX:+UseG1GC ...")
        form.addRow("Extra JVM arguments:", self.extra_args)

        self.check_updates = QCheckBox()
        self.check_updates.setChecked(settings.check_for_updates_on_start)
        form.addRow("Check for updates on start:", self.check_updates)

        self.close_on_launch = QCheckBox()
        self.close_on_launch.setChecked(settings.close_launcher_on_game_start)
        form.addRow("Close launcher after launching:", self.close_on_launch)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def result_settings(self) -> AppSettings:
        self._settings.launch.min_memory_mb = self.min_mem.value()
        self._settings.launch.max_memory_mb = max(self.max_mem.value(), self.min_mem.value())
        self._settings.launch.window_width = self.width.value()
        self._settings.launch.window_height = self.height.value()
        self._settings.launch.fullscreen = self.fullscreen.isChecked()
        args_text = self.extra_args.text().strip()
        self._settings.launch.extra_jvm_args = args_text.split() if args_text else None
        self._settings.check_for_updates_on_start = self.check_updates.isChecked()
        self._settings.close_launcher_on_game_start = self.close_on_launch.isChecked()
        return self._settings
