from gettext import gettext as _

from gi.repository import Gtk

from .base import Page


@Gtk.Template(resource_path='/dev/kubabin/launcher/ui/assembly_line.ui')
class AssemblyLinePage(Page):
    __gtype_name__ = 'AssemblylinelauncherAssemblyLinePage'

    page_id = 'assembly-line'
    title = _('Assembly Line')
    icon_name = 'folder-pictures-symbolic'
