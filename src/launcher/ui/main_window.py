"""Main launcher window.

Layout: server/modpack header, account selector, a big Play button, a
progress bar + status label for installs, and a collapsible log panel that
shows game output once launched.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from launcher import __app_name__
from launcher.auth.manager import AccountManager
from launcher.auth.microsoft import AuthError
from launcher.auth.models import AccountType
from launcher.core import sync as sync_module
from launcher.core.launch import LaunchError, launch_instance
from launcher.core.modrinth import ModrinthClient, ModrinthVersion
from launcher.core.settings import AppSettings, load_settings, save_settings
from launcher.ui.account_dialog import AccountDialog
from launcher.ui.settings_dialog import SettingsDialog
from launcher.ui.workers import Worker

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(__app_name__)
        self.resize(760, 560)

        self.account_manager = AccountManager()
        self.settings: AppSettings = load_settings()

        self._versions: list[ModrinthVersion] = []
        self._sync_worker: Worker | None = None
        self._versions_worker: Worker | None = None
        self._game_process = None

        self._build_ui()
        self._refresh_account_combo()
        self._load_project_info_and_versions()

    # -- UI construction ------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        header = QVBoxLayout()
        self.title_label = QLabel("Assembly Line SMP")
        self.title_label.setStyleSheet("font-size: 20px; font-weight: bold;")
        header.addWidget(self.title_label)
        self.subtitle_label = QLabel("Loading modpack information...")
        self.subtitle_label.setStyleSheet("color: gray;")
        header.addWidget(self.subtitle_label)
        root.addLayout(header)

        row = QHBoxLayout()

        row.addWidget(QLabel("Account:"))
        self.account_combo = QComboBox()
        self.account_combo.currentIndexChanged.connect(self._on_account_selected)
        row.addWidget(self.account_combo, stretch=1)
        manage_accounts_btn = QPushButton("Manage Accounts")
        manage_accounts_btn.clicked.connect(self._on_manage_accounts)
        row.addWidget(manage_accounts_btn)
        root.addLayout(row)

        version_row = QHBoxLayout()
        version_row.addWidget(QLabel("Version:"))
        self.version_combo = QComboBox()
        version_row.addWidget(self.version_combo, stretch=1)
        settings_btn = QPushButton("Settings")
        settings_btn.clicked.connect(self._on_open_settings)
        version_row.addWidget(settings_btn)
        root.addLayout(version_row)

        self.play_button = QPushButton("Play")
        self.play_button.setMinimumHeight(48)
        self.play_button.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.play_button.clicked.connect(self._on_play_clicked)
        root.addWidget(self.play_button)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: gray;")
        root.addWidget(self.status_label)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setVisible(False)
        root.addWidget(self.log_view, stretch=1)

    # -- data loading -----------------------------------------------------

    def _load_project_info_and_versions(self) -> None:
        self._versions_worker = Worker(
            sync_module.get_available_versions, wants_progress=False
        )
        self._versions_worker.signals.finished.connect(self._on_versions_loaded)
        self._versions_worker.signals.error.connect(self._on_versions_error)
        self._versions_worker.start()

        info_worker = Worker(ModrinthClient().get_project, wants_progress=False)
        info_worker.signals.finished.connect(self._on_project_info_loaded)
        info_worker.signals.error.connect(lambda *_: None)
        info_worker.start()
        self._project_info_worker = info_worker  # keep alive

    def _on_project_info_loaded(self, project) -> None:
        self.title_label.setText(project.title)

    def _on_versions_loaded(self, versions: list[ModrinthVersion]) -> None:
        self._versions = versions[:1]
        self.version_combo.clear()
        for v in self._versions:
            self.version_combo.addItem(f"{v.version_number} ({v.name})", v.id)
        if self._versions:
            self.subtitle_label.setText(f"Latest version: {self._versions[0].version_number}")
        else:
            self.subtitle_label.setText("No installable versions found.")

    def _on_versions_error(self, message: str, tb: str) -> None:
        logger.error("Failed to load versions:\n%s", tb)
        self.subtitle_label.setText(f"Failed to load modpack info: {message}")

    def _refresh_account_combo(self) -> None:
        self.account_combo.blockSignals(True)
        self.account_combo.clear()
        active = self.account_manager.active_account
        active_index = -1
        for i, account in enumerate(self.account_manager.list_accounts()):
            kind = "MS" if account.type is AccountType.PREMIUM else "Cracked"
            self.account_combo.addItem(f"{account.username} [{kind}]", account.id)
            if active and account.id == active.id:
                active_index = i
        if active_index >= 0:
            self.account_combo.setCurrentIndex(active_index)
        self.account_combo.blockSignals(False)
        self.play_button.setEnabled(self.account_combo.count() > 0)
        if self.account_combo.count() == 0:
            self.status_label.setText("Add an account to play.")

    # -- account/settings dialogs -----------------------------------------

    def _on_manage_accounts(self) -> None:
        dialog = AccountDialog(self.account_manager, self)
        dialog.exec()
        self._refresh_account_combo()

    def _on_account_selected(self, index: int) -> None:
        if index < 0:
            return
        account_id = self.account_combo.itemData(index)
        if account_id:
            self.account_manager.set_active(account_id)

    def _on_open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == SettingsDialog.DialogCode.Accepted:
            self.settings = dialog.result_settings()
            save_settings(self.settings)

    # -- play / sync flow ---------------------------------------------

    def _selected_version(self) -> ModrinthVersion | None:
        idx = self.version_combo.currentIndex()
        if idx < 0 or idx >= len(self._versions):
            return None
        return self._versions[idx]

    def _on_play_clicked(self) -> None:
        version = self._selected_version()
        if version is None:
            QMessageBox.warning(self, "No Version Selected", "No modpack version is selected.")
            return
        if self.account_manager.active_account is None:
            QMessageBox.warning(self, "No Account", "Add and select an account first.")
            return

        existing = sync_module.InstanceManager().get(sync_module.DEFAULT_INSTANCE_ID)
        if existing and existing.metadata.modrinth_version_id != version.id:
            answer = QMessageBox.question(
                self,
                "Modpack Update Available",
                f"Update the modpack from {existing.metadata.modrinth_version_number} "
                f"to {version.version_number} before launching?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self.play_button.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # indeterminate until first real progress event
        self.status_label.setText("Preparing...")

        self._sync_worker = Worker(sync_module.sync_instance, version)
        self._sync_worker.signals.progress.connect(self._on_sync_progress)
        self._sync_worker.signals.finished.connect(self._on_sync_finished)
        self._sync_worker.signals.error.connect(self._on_sync_error)
        self._sync_worker.start()

    def _on_sync_progress(self, label: str, done: int, total: int) -> None:
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(done)
        self.status_label.setText(label)

    def _on_sync_finished(self, result: sync_module.SyncResult) -> None:
        self.progress_bar.setVisible(False)
        self.status_label.setText("Launching...")
        try:
            account = self.account_manager.get_launch_ready_account()
        except AuthError as exc:
            self._fail_launch(str(exc))
            return

        try:
            self._game_process = launch_instance(
                result.java_binary,
                result.instance,
                account,
                self.settings.launch,
                on_output=self._append_log_threadsafe,
            )
        except LaunchError as exc:
            self._fail_launch(str(exc))
            return

        self.status_label.setText(f"Running as {account.username}")
        self.log_view.setVisible(True)
        self.play_button.setEnabled(True)
        self.play_button.setText("Play")

        if self.settings.close_launcher_on_game_start:
            self.close()

    def _on_sync_error(self, message: str, tb: str) -> None:
        logger.error("Sync failed:\n%s", tb)
        self._fail_launch(message)

    def _fail_launch(self, message: str) -> None:
        self.progress_bar.setVisible(False)
        self.play_button.setEnabled(True)
        self.status_label.setText("Failed.")
        QMessageBox.critical(self, "Launch Failed", message)

    def _append_log_threadsafe(self, line: str) -> None:
        from PySide6.QtCore import Q_ARG, QMetaObject
        from PySide6.QtCore import Qt as QtCore_Qt

        QMetaObject.invokeMethod(
            self,
            "_append_log",
            QtCore_Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, line),
        )

    @Slot(str)
    def _append_log(self, line: str) -> None:
        self.log_view.appendPlainText(line)

    # -- lifecycle ------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Give any in-flight background workers a chance to finish before
        the window (and their parent QObject tree) is torn down. Without
        this, closing the launcher mid-sync/mid-fetch destroys a running
        QThread out from under itself, which Qt treats as fatal.
        """
        for worker in (
            self._versions_worker,
            getattr(self, "_project_info_worker", None),
            self._sync_worker,
        ):
            if worker is not None and worker.isRunning():
                worker.requestInterruption()
                worker.wait(3000)
        super().closeEvent(event)
