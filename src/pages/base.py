from gi.repository import Adw


class Page(Adw.Bin):
    """Base class for pages shown in the content area."""

    __gtype_name__ = 'AssemblylinelauncherPage'

    page_id = ''
    title = ''
    icon_name = ''

    def on_shown(self):
        """Called each time the page is selected in the sidebar."""
