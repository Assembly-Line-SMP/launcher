"""Account management dialog: list existing accounts, add a cracked or
Microsoft account, remove accounts, pick the active one.
"""

from __future__ import annotations

import logging
import webbrowser

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from launcher.auth.cracked import InvalidUsernameError
from launcher.auth.manager import AccountManager
from launcher.auth.microsoft import DeviceCodeInfo
from launcher.auth.models import Account, AccountType
from launcher.ui.workers import Worker

logger = logging.getLogger(__name__)


class DeviceCodeDialog(QDialog):
    """Shown while polling for Microsoft device-code sign-in completion."""

    def __init__(self, info: DeviceCodeInfo, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sign in with Microsoft")
        self.setModal(True)
        self._cancelled = False

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("1. Open this page in your browser:"))

        url_label = QLabel(f'<a href="{info.verification_uri}">{info.verification_uri}</a>')
        url_label.setOpenExternalLinks(True)
        layout.addWidget(url_label)

        layout.addWidget(QLabel("2. Enter this code:"))
        code_label = QLabel(info.user_code)
        code_label.setStyleSheet("font-size: 22px; font-weight: bold;")
        code_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(code_label)

        layout.addWidget(QLabel("Waiting for you to finish signing in..."))

        open_button = QPushButton("Open browser")
        open_button.clicked.connect(lambda: webbrowser.open(info.verification_uri))
        layout.addWidget(open_button)

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self._on_cancel)
        layout.addWidget(cancel_button)

    def _on_cancel(self) -> None:
        self._cancelled = True
        self.reject()

    def should_cancel(self) -> bool:
        return self._cancelled


class AccountDialog(QDialog):
    def __init__(self, manager: AccountManager, parent=None) -> None:
        super().__init__(parent)
        self.manager = manager
        self._device_dialog: DeviceCodeDialog | None = None
        self._worker: Worker | None = None

        self.setWindowTitle("Accounts")
        self.resize(420, 380)

        layout = QVBoxLayout(self)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._on_set_active)
        layout.addWidget(self.list_widget)

        button_row = QHBoxLayout()
        add_cracked_btn = QPushButton("Add Cracked Account")
        add_cracked_btn.clicked.connect(self._on_add_cracked)
        add_premium_btn = QPushButton("Add Microsoft Account")
        add_premium_btn.clicked.connect(self._on_add_premium)
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._on_remove)
        set_active_btn = QPushButton("Set Active")
        set_active_btn.clicked.connect(self._on_set_active)

        for b in (add_cracked_btn, add_premium_btn, set_active_btn, remove_btn):
            button_row.addWidget(b)
        layout.addLayout(button_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self._refresh_list()

    def _refresh_list(self) -> None:
        self.list_widget.clear()
        active = self.manager.active_account
        for account in self.manager.list_accounts():
            kind = "Microsoft" if account.type is AccountType.PREMIUM else "Cracked"
            marker = "  \u2605 active" if active and active.id == account.id else ""
            item = QListWidgetItem(f"{account.username}  ({kind}){marker}")
            item.setData(Qt.ItemDataRole.UserRole, account.id)
            self.list_widget.addItem(item)

    def _selected_account_id(self) -> str | None:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    # -- cracked ----------------------------------------------------------

    def _on_add_cracked(self) -> None:
        username, ok = QInputDialog.getText(self, "Add Cracked Account", "Username:")
        if not ok or not username.strip():
            return
        try:
            self.manager.add_cracked_account(username.strip())
        except InvalidUsernameError as exc:
            QMessageBox.warning(self, "Invalid Username", str(exc))
            return
        self._refresh_list()

    # -- premium ------------------------------------------------------

    def _on_add_premium(self) -> None:
        def on_code_ready(info: DeviceCodeInfo) -> None:
            # Called from the worker thread; must hand off to the GUI
            # thread. Qt's queued-signal mechanism handles this for us as
            # long as we only touch widgets from slots -- so we stash the
            # info and show the dialog via a same-thread call scheduled on
            # the event loop.
            self._show_device_dialog_threadsafe(info)

        self._worker = Worker(
            self.manager.add_premium_account,
            on_code_ready,
            should_cancel=self._device_cancelled,
            wants_progress=False,
        )
        self._worker.signals.finished.connect(self._on_premium_added)
        self._worker.signals.error.connect(self._on_premium_error)
        self._worker.start()

    def _device_cancelled(self) -> bool:
        return self._device_dialog is not None and self._device_dialog.should_cancel()

    def _show_device_dialog_threadsafe(self, info: DeviceCodeInfo) -> None:
        from PySide6.QtCore import QMetaObject
        from PySide6.QtCore import Qt as QtCore_Qt

        # Simplest robust approach: use a queued single-shot invocation via
        # a tiny helper QObject method, so the dialog is constructed on the
        # GUI thread even though on_code_ready fires from the worker.
        self._pending_device_info = info
        QMetaObject.invokeMethod(
            self, "_create_device_dialog", QtCore_Qt.ConnectionType.QueuedConnection
        )

    @Slot()
    def _create_device_dialog(self) -> None:
        info = getattr(self, "_pending_device_info", None)
        if info is None:
            return
        self._device_dialog = DeviceCodeDialog(info, self)
        self._device_dialog.show()

    def _on_premium_added(self, account: Account) -> None:
        if self._device_dialog is not None:
            self._device_dialog.accept()
            self._device_dialog = None
        self._refresh_list()

    def _on_premium_error(self, message: str, tb: str) -> None:
        if self._device_dialog is not None:
            self._device_dialog.reject()
            self._device_dialog = None
        logger.error("Premium sign-in failed:\n%s", tb)

        if "MS_CLIENT_ID" in message or "not configured" in message.lower():
            QMessageBox.critical(
                self,
                "Microsoft Sign-In Not Configured",
                "This build of the launcher hasn't had a Microsoft OAuth "
                "client ID set up by its maintainer yet. See "
                "docs/MICROSOFT_AUTH_SETUP.md.",
            )
        else:
            QMessageBox.warning(self, "Sign-In Failed", message)

    # -- shared -------------------------------------------------------

    def _on_remove(self) -> None:
        account_id = self._selected_account_id()
        if account_id is None:
            return
        self.manager.remove(account_id)
        self._refresh_list()

    def _on_set_active(self) -> None:
        account_id = self._selected_account_id()
        if account_id is None:
            return
        self.manager.set_active(account_id)
        self._refresh_list()
