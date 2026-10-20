from gettext import gettext as _
import logging
from gi.repository import Gtk, Adw, GLib


logger = logging.getLogger(__name__)

from .base import Page

from ..launcher.ui.workers import Worker
from ..launcher.auth.manager import AccountManager, AccountType
from ..launcher.auth.microsoft import AuthError
from ..launcher.core.settings import AppSettings, load_settings
from ..launcher.core.modrinth import ModrinthVersion, ModrinthClient
from ..launcher.core.launch import LaunchError, launch_instance
from ..launcher.core import sync as sync_module

@Gtk.Template(resource_path='/dev/kubabin/launcher/ui/launch_test.ui')
class LaunchTestPage(Page):
    __gtype_name__ = 'LaunchTestPage'

    page_id = 'launch-test'
    title = _('Launch Test')
    icon_name = 'folder-pictures-symbolic'

    account_combo = Gtk.Template.Child()
    account_list = Gtk.Template.Child()
    version_combo = Gtk.Template.Child()
    status_label = Gtk.Template.Child()
    play_button = Gtk.Template.Child()
    progress_bar = Gtk.Template.Child()
    status_page = Gtk.Template.Child()
    view_stack = Gtk.Template.Child()
    launch_log = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.account_manager = AccountManager()
        self.settings: AppSettings = load_settings()
        
        self._versions: list[ModrinthVersion] = []
        self._game_process = None

        self.toaster = Adw.ToastOverlay()

        self._refresh_account_combo()
        self._load_project_info_and_versions()

    def _load_project_info_and_versions(self) -> None:
        try:
            self._on_versions_loaded(sync_module.get_available_versions())
        except Exception as e:
            self._on_versions_error(str(e), "")

        try:
            self._on_project_info_loaded(ModrinthClient().get_project())
        except Exception as e:
            self._on_project_info_loaded(None)
        return

    def _on_project_info_loaded(self, project) -> None:
        print(project)
        self.status_page.set_title(project.title)

    def _on_versions_loaded(self, versions: list[ModrinthVersion]) -> None:
        self._versions = versions
        data = []
        for v in self._versions:
            data.append(f"{v.version_number} ({v.name})")
        self.version_combo.set_model(Gtk.StringList.new(data))

    def _on_versions_error(self, message: str, tb: str) -> None:
        logger.error("Failed to load versions:\n%s", tb)
        self.status_page.set_description(f"Failed to load modpack info: {message}")

    def _refresh_account_combo(self) -> None:
        data = []
        active = self.account_manager.active_account
        active_index = -1
        for i, account in enumerate(self.account_manager.list_accounts()):
            kind = "MS" if account.type is AccountType.PREMIUM else "Cracked"
            data.append(f"{account.username} [{kind}]")
            #self.account_combo.addItem(f"{account.username} [{kind}]", account.id)
            if active and account.id == active.id:
                active_index = i
        print(active_index)
        self.account_combo.set_model(Gtk.StringList.new(data))
        if active_index >= 0:
            #self.account_combo.setCurrentIndex(active_index)
            self.account_combo.set_selected(active_index)
        self.play_button.set_sensitive(len(data) > 0)
        if len(data) == 0:
            self.status_label.set_label("Add an account to play.")

    # -- account/settings dialogs -----------------------------------------
    @Gtk.Template.Callback('on_manage_accounts')
    def _on_manage_accounts(self, button) -> None:
        print("Managing accounts")
        self.view_stack.set_visible_child_name("account_page")
        self._refresh_account_combo()

    def _on_account_selected(self, index: int) -> None:
        if index < 0:
            return
        account_id = self.account_list.itemData(index)
        if account_id:
            self.account_manager.set_active(account_id)

    def _on_open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == SettingsDialog.DialogCode.Accepted:
            self.settings = dialog.result_settings()
            save_settings(self.settings)

    # -- play / sync flow ---------------------------------------------

    def _selected_version(self) -> ModrinthVersion | None:
        idx = self.version_combo.get_selected()
        if idx < 0 or idx >= len(self._versions):
            return None
        return self._versions[idx]

    @Gtk.Template.Callback('on_play_clicked')
    def _on_play_clicked(self, button: Gtk.Button) -> None:
        version = self._selected_version()
        if version is None:
            self.set_toast("No modpack version selected.")
            return
        if self.account_manager.active_account is None:
            self.set_toast("Add an account to play.")
            return

        print(f"Selected version: {version.version_number}")

        existing = sync_module.InstanceManager().get(sync_module.DEFAULT_INSTANCE_ID)
        if existing and existing.metadata.modrinth_version_id != version.id:
            print(f"Existing version: {existing.metadata.modrinth_version_number}, Selected version: {version.version_number}")
            dialog = Adw.AlertDialog.new(
                "Update Available",
                f"Update the modpack from {existing.metadata.modrinth_version_number} "
                f"to {version.version_number} before launching?",
            )
            dialog.add_response(Adw.ResponseType.YES)
            dialog.add_response(Adw.ResponseType.NO)
            dialog.set_default_response(Adw.ResponseType.YES)
            dialog.connect("response", self.on_update_dialog_response)
            dialog.present(self)
        else:
            version = self._selected_version()
            #self.play_button.setEnabled(False)
            self.progress_bar.set_visible(True)
            self.progress_bar.pulse()
            self.set_status("Preparing...")
            self._sync_worker = Worker(sync_module.sync_instance, version)
            self._sync_worker.signals.progress.connect(self._on_sync_progress)
            self._sync_worker.signals.finished.connect(self._on_sync_finished)
            self._sync_worker.signals.error.connect(self._on_sync_error)
            self._sync_worker.start()
        return

    def on_update_dialog_response(self, dialog: Adw.AlertDialog, response: Adw.ResponseType) -> None:
        if response != Adw.ResponseType.YES:
            return
        version = self._selected_version()
        self.play_button.setEnabled(False)
        self.progress_bar.set_visible(True)
        self.progress_bar.pulse()
        self.set_status("Preparing...")
        self._sync_worker = Worker(sync_module.sync_instance, version)
        self._sync_worker.signals.progress.connect(self._on_sync_progress)
        self._sync_worker.signals.finished.connect(self._on_sync_finished)
        self._sync_worker.signals.error.connect(self._on_sync_error)
        self._sync_worker.start()

    def _on_sync_progress(self, label: str, done: int, total: int) -> None:
        if total > 0:
            self.progress_bar.set_fraction(done / total)
        self.set_status(label)

    def _on_sync_finished(self, result: sync_module.SyncResult) -> None:
        self.progress_bar.set_visible(False)
        self.set_status("Launching...")
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

        self.set_status(f"Running as {account.username}")
        self.launch_log.set_visible(True)
        #self.play_button.setEnabled(True)
        #self.play_button.setText("Play")

        if self.settings.close_launcher_on_game_start:
            self.close()

    def _on_sync_error(self, message: str, tb: str) -> None:
        logger.error("Sync failed:\n%s", tb)
        self._fail_launch(message)

    def _fail_launch(self, message: str) -> None:
        self.progress_bar.set_visible(False)
        self.set_status("Failed.")
        # Show a GTK/Adwaita toast
        self.set_toast(message)


    def _append_log_threadsafe(self, line: str) -> None:
        GLib.idle_add(self._append_log, line)

    def _append_log(self, line: str) -> bool:
        buf = self.launch_log.get_buffer()
        buf.insert(buf.get_end_iter(), line + "\n")
        mark = buf.create_mark(None, buf.get_end_iter(), False)
        self.launch_log.scroll_to_mark(mark, 0.0, False, 0.0, 0.0)
        buf.delete_mark(mark)
        return GLib.SOURCE_REMOVE

    def set_status(self, message: str) -> None:
        self.status_label.set_label(message)

    def set_progress(self, value: float) -> None:
        self.progress_bar.set_value(value)

    def set_toast(self, message: str) -> None:
        print(f"Toast: {message}")
        self.toaster.add_toast(Adw.Toast.new(message))

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