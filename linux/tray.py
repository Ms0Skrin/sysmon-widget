"""Значок в трее: StatusNotifierItem + меню com.canonical.dbusmenu (чистый D-Bus, без библиотек).

ЛКМ — показать/скрыть панели, ПКМ — меню. Уведомления — через org.freedesktop.Notifications.
"""
import os

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .config import ICON_PATH  # noqa: E402

SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
    <signal name="NewIcon"/>
    <signal name="NewTitle"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg type="s"/></signal>
  </interface>
</node>
"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/>
      <arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" direction="in"/><arg type="as" direction="in"/>
      <arg type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/>
      <arg type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/>
    </method>
    <method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
    <method name="AboutToShowGroup">
      <arg type="ai" direction="in"/><arg type="ai" direction="out"/><arg type="ai" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated"><arg type="a(ia{sv})"/><arg type="a(ias)"/></signal>
    <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
    <signal name="ItemActivationRequested"><arg type="i"/><arg type="u"/></signal>
  </interface>
</node>
"""

ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
WATCHER = "org.kde.StatusNotifierWatcher"


def icon_pixmaps():
    """Значок программы → a(iiay): ARGB32, старший байт первым."""
    out = []
    try:
        from PIL import Image
        src = Image.open(ICON_PATH)
        src.load()
        src = src.convert("RGBA")
        for size in (22, 32, 48):
            im = src.resize((size, size), Image.LANCZOS)
            r, g, b, a = im.split()
            out.append((size, size, Image.merge("RGBA", (a, r, g, b)).tobytes()))
    except Exception:
        pass
    return out


class TrayIcon:
    def __init__(self, tooltip, on_left, get_items):
        """get_items() → список (текст, callback) | ("-",) | (текст, None) — неактивная строка."""
        self.tooltip, self.on_left, self.get_items = tooltip, on_left, get_items
        self.conn = None
        self.items = []
        self.revision = 1
        self.ids = []
        self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self.pixmaps = icon_pixmaps()
        self._own = self._watch = 0

    def start(self):
        try:
            self.conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            return
        sni = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        menu = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self.ids = [self.conn.register_object(ITEM_PATH, sni, self._sni_call, self._sni_prop, None),
                    self.conn.register_object(MENU_PATH, menu, self._menu_call, self._menu_prop, None)]
        self._own = Gio.bus_own_name_on_connection(self.conn, self.name, Gio.BusNameOwnerFlags.NONE, None, None)
        # трей мог запуститься позже виджета или перезапуститься — регистрируемся каждый раз
        self._watch = Gio.bus_watch_name_on_connection(self.conn, WATCHER, Gio.BusNameWatcherFlags.NONE,
                                                       lambda *_a: self._register(), None)

    def _register(self):
        self.conn.call(WATCHER, "/StatusNotifierWatcher", WATCHER, "RegisterStatusNotifierItem",
                       GLib.Variant("(s)", (self.name,)), None, Gio.DBusCallFlags.NONE, -1, None, None)

    def stop(self):
        if self.conn is None:
            return
        for i in self.ids:
            self.conn.unregister_object(i)
        self.ids = []
        if self._watch:
            Gio.bus_unwatch_name(self._watch)
        if self._own:
            Gio.bus_unown_name(self._own)
        self._watch = self._own = 0

    def notify(self, title, text):
        if self.conn is None:
            return
        self.conn.call("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                       "org.freedesktop.Notifications", "Notify",
                       GLib.Variant("(susssasa{sv}i)", ("SysMon Widget", 0, "sysmon-widget", title, text, [],
                                                        {"urgency": GLib.Variant("y", 2)}, 10000)),
                       None, Gio.DBusCallFlags.NONE, -1, None, None)

    # ---------- StatusNotifierItem ----------
    def _sni_call(self, _conn, _sender, _path, _iface, method, _params, inv):
        inv.return_value(None)
        if method == "Activate":
            GLib.idle_add(self._once, self.on_left)

    def _sni_prop(self, _conn, _sender, _path, _iface, prop):
        tip = GLib.Variant("(sa(iiay)ss)", ("", [], self.tooltip, ""))
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "sysmon-widget"),
            "Title": GLib.Variant("s", self.tooltip),
            "Status": GLib.Variant("s", "Active"),
            "WindowId": GLib.Variant("i", 0),
            "IconName": GLib.Variant("s", "" if self.pixmaps else "utilities-system-monitor"),
            "IconPixmap": GLib.Variant("a(iiay)", self.pixmaps),
            "OverlayIconName": GLib.Variant("s", ""),
            "AttentionIconName": GLib.Variant("s", ""),
            "ToolTip": tip,
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", MENU_PATH),
        }
        return values.get(prop)

    @staticmethod
    def _once(fn):
        fn()
        return False

    # ---------- dbusmenu ----------
    def _refresh(self):
        self.items = list(self.get_items())
        self.revision += 1

    def _props(self, i):
        item = self.items[i - 1]
        if item[0] == "-":
            return {"type": GLib.Variant("s", "separator")}
        props = {"label": GLib.Variant("s", item[0].replace("_", "__"))}
        if item[1] is None:
            props["enabled"] = GLib.Variant("b", False)
        return props

    def _layout(self):
        children = [GLib.Variant("(ia{sv}av)", (i, self._props(i), []))
                    for i in range(1, len(self.items) + 1)]
        return (0, {"children-display": GLib.Variant("s", "submenu")}, children)

    def _menu_call(self, _conn, _sender, _path, _iface, method, params, inv):
        args = params.unpack()
        if method == "GetLayout":
            if not self.items:
                self._refresh()
            layout = self._layout() if args[0] == 0 else (args[0], self._safe_props(args[0]), [])
            inv.return_value(GLib.Variant("(u(ia{sv}av))", (self.revision, layout)))
        elif method == "GetGroupProperties":
            ids = args[0] or range(1, len(self.items) + 1)
            inv.return_value(GLib.Variant("(a(ia{sv}))", ([(i, self._safe_props(i)) for i in ids],)))
        elif method == "GetProperty":
            inv.return_value(GLib.Variant("(v)", (self._safe_props(args[0]).get(args[1], GLib.Variant("s", "")),)))
        elif method == "Event":
            inv.return_value(None)
            self._event(args[0], args[1])
        elif method == "EventGroup":
            inv.return_value(GLib.Variant("(ai)", ([],)))
            for i, event, _data, _ts in args[0]:
                self._event(i, event)
        elif method == "AboutToShow":
            changed = args[0] == 0
            if changed:
                self._refresh()
            inv.return_value(GLib.Variant("(b)", (changed,)))
            if changed:
                self.conn.emit_signal(None, MENU_PATH, "com.canonical.dbusmenu", "LayoutUpdated",
                                      GLib.Variant("(ui)", (self.revision, 0)))
        elif method == "AboutToShowGroup":
            inv.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            inv.return_value(None)

    def _safe_props(self, i):
        return self._props(i) if 1 <= i <= len(self.items) else {}

    def _event(self, i, event):
        if event == "clicked" and 1 <= i <= len(self.items):
            item = self.items[i - 1]
            if len(item) > 1 and item[1] is not None:
                GLib.idle_add(self._once, item[1])

    def _menu_prop(self, _conn, _sender, _path, _iface, prop):
        return {"Version": GLib.Variant("u", 3), "TextDirection": GLib.Variant("s", "ltr"),
                "Status": GLib.Variant("s", "normal"), "IconThemePath": GLib.Variant("as", [])}.get(prop)
