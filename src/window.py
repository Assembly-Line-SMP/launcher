# MIT License
#
# Copyright (c) 2026 Unknown
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
# SPDX-License-Identifier: MIT

from ast import Gt
from gi.repository import Gio

from gi.repository import Adw
from gi.repository import Gtk

from .launcher.auth.manager import AccountManager

from .pages import PAGES

@Gtk.Template(resource_path='/dev/kubabin/launcher/window.ui')
class AssemblylinelauncherWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'AssemblylinelauncherWindow'

    split_view = Gtk.Template.Child()
    sidebar = Gtk.Template.Child()
    sidebar_section = Gtk.Template.Child()
    content_page = Gtk.Template.Child()
    stack = Gtk.Template.Child()
    account_menu = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.pages = []
        for page_cls in PAGES:
            page = page_cls()
            self.pages.append(page)
            self.stack.add_named(page, page.page_id)
            self.sidebar_section.append(
                Adw.SidebarItem(title=page.title, icon_name=page.icon_name))
        self.sidebar.set_selected(0)
        self.show_page(0)

        # Populate account menu
        for account in AccountManager().list_accounts():
            self.account_menu.append(account.username)
        buttons_section = Gio.Menu()
        add_account_item = Gio.MenuItem.new("Add account")
        add_account_item.set_icon(Gio.ThemedIcon(name="list-add"))
        buttons_section.append_item(add_account_item)
        self.account_menu.append_section(None, buttons_section)

    def show_page(self, index):
        page = self.pages[index]
        self.stack.set_visible_child(page)
        self.content_page.set_title(page.title)
        page.on_shown()

    @Gtk.Template.Callback()
    def sidebar_selected_cb(self, sidebar, _pspec):
        index = sidebar.get_selected()
        if 0 <= index < len(self.pages):
            self.show_page(index)
            self.split_view.set_show_content(True)
