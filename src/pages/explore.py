from gettext import gettext as _

from gi.repository import Gtk

from .base import Page


@Gtk.Template(resource_path='/dev/kubabin/launcher/ui/explore.ui')
class ExplorePage(Page):
    __gtype_name__ = 'AssemblylinelauncherExplorePage'

    page_id = 'explore'
    title = _('Explore')
    icon_name = 'folder-videos-symbolic'
