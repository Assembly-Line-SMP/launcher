from gettext import gettext as _

from gi.repository import Gtk, Gio

from ..launcher.core.instance import InstanceManager, Instance

from .base import Page



@Gtk.Template(resource_path='/dev/kubabin/launcher/ui/home.ui')
class HomePage(Page):
    __gtype_name__ = 'AssemblylinelauncherHomePage'

    page_id = 'home'
    title = _('Home')
    icon_name = 'user-home-symbolic'

    instances_box = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.instance_manager = InstanceManager()
        self.instances_model = Gio.ListStore.new(Instance)
        for instance in self.instance_manager.list_instances():
            self.instances_model.append(instance)
            #self.instances_model.append(instance)
        self.instances_box.bind_model(
            self.instances_model, 
            self.create_instance_widget
        )
        for i in range(self.instances_model.get_n_items()):
            child = self.instances_box.get_child_at_index(i)
            child.set_halign(Gtk.Align.START)
            child.set_valign(Gtk.Align.START)

    def create_instance_widget(self, item: Instance, user_data=None):
        widget = Gtk.Box()
        # Set a reasonable size for the card
        widget.get_style_context().add_class('card')
        widget.set_orientation(Gtk.Orientation.VERTICAL)
        widget.set_hexpand(False)
        widget.set_vexpand(False)
        widget.set_halign(Gtk.Align.START)
        widget.set_valign(Gtk.Align.START)
        #widget.set_size_request(200, 200)
        picture = Gtk.Picture.new_for_file(Gio.File.new_for_uri(item.metadata.icon))
        picture.set_size_request(64, 64)
        picture.set_can_shrink(True)
        picture.set_content_fit(Gtk.ContentFit.SCALE_DOWN)
        picture.set_margin_top(6)
        picture.set_margin_bottom(6)
        picture.set_margin_start(6)
        picture.set_margin_end(6)
        
        widget.append(picture)
        label = Gtk.Label()
        label.set_markup(f"<b>{item.metadata.name}</b>")
        label.set_valign(Gtk.Align.CENTER)
        label.set_justify(Gtk.Justification.CENTER)
        label.set_wrap(True)
        label.set_margin_top(6)
        label.set_margin_bottom(12)
        widget.append(label)
        return widget

    

class InstanceListModel(Gio.ListStore):
    def __init__(self, instances):
        super().__init__(item_type=instances[0].__gtype__ if instances else None)
        self._instances = instances
        print(self._instances)