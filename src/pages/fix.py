from gettext import gettext as _

from gi.repository import Gtk

from .base import Page


@Gtk.Template(resource_path='/dev/kubabin/launcher/ui/fix.ui')
class FixPage(Page):
    __gtype_name__ = 'AssemblylinelauncherFixPage'

    page_id = 'fix'
    title = _('Fix')
    icon_name = 'folder-videos-symbolic'
