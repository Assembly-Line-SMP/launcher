from gettext import gettext as _

from gi.repository import Gtk

from .base import Page


@Gtk.Template(resource_path='/dev/kubabin/launcher/ui/home.ui')
class HomePage(Page):
    __gtype_name__ = 'AssemblylinelauncherHomePage'

    page_id = 'home'
    title = _('Home')
    icon_name = 'user-home-symbolic'
