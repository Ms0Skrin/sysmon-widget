"""Всплывающее меню панели: скруглённые края, значки, галочки, страницы подменю."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

ICONS = {  # Nerd Font (Font Awesome)
    "show": "", "apps": "", "settings": "", "music": "",
    "restart": "", "update": "", "exit": "", "info": "", "look": "",
    "check": "", "back": "", "more": "", "tiles": "", "cols": "",
    "add": "", "delete": "", "tray": "", "sensors": "", "stop": "",
    "scale": "", "monitor": "", "pin": "", "edit": "", "left": "",
    "right": "",
}


class _Row:
    """Строка меню. Виджет живёт, пока на его месте остаётся строка того же вида: если по пункту с
    галочкой щёлкают несколько раз подряд, кнопка под указателем не пересоздаётся и не теряет щелчки."""

    def __init__(self, menu, kind):
        self.menu, self.kind = menu, kind
        self.cb, self.keep = None, False
        if kind == "sep":
            self.widget = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        elif kind in ("title", "info"):
            self.label = Gtk.Label(xalign=0, wrap=kind == "info", max_width_chars=34)
            self.label.add_css_class(f"sm-menu-{kind}")
            self.widget = self.label
        else:
            box = Gtk.Box(spacing=10)
            self.icon = Gtk.Label(width_chars=2)
            self.icon.add_css_class("sm-icon")
            self.label = Gtk.Label(xalign=0, hexpand=True)
            self.more = Gtk.Label(label=ICONS["more"])
            self.more.add_css_class("sm-dim")
            self.more.add_css_class("sm-icon")
            for w in (self.icon, self.label, self.more):
                box.append(w)
            self.widget = Gtk.Button(child=box)
            self.widget.connect("clicked", lambda _b: self.menu._run(self.cb, self.keep))

    def set(self, item):
        if self.kind in ("title", "info"):
            self.label.set_label(item[1])
        elif self.kind == "btn":
            opts = item[3] if len(item) > 3 else {}
            glyph = ICONS.get(item[0], "")
            if "check" in opts:
                glyph = ICONS["check"] if opts["check"] else ""
            self.icon.set_label(glyph)
            self.label.set_label(item[1])
            self.more.set_visible(bool(opts.get("sub")))
            self.cb, self.keep = item[2], bool(opts.get("keep"))


def _kind(item):
    if item[0] == "-":
        return "sep"
    if item[0] == "title":
        return "title"
    return "info" if len(item) == 2 else "btn"


class PopMenu:
    """items: список (icon, text, callback[, opts]) | ("-",) разделитель | ("info", text) неактивная строка.

    opts: {"check": bool} — пункт-галочка (вместо значка — отметка), {"sub": True} — стрелка «дальше»,
    {"keep": True} — меню не закрывается (callback сам перерисует его, напр. страницу подменю).
    """

    def __init__(self):
        self.pop = None
        self.box = None
        self.rows = []

    def visible(self):
        return self.pop is not None and self.pop.get_visible()

    def close(self):
        if self.pop is not None:
            self.pop.popdown()

    def show(self, parent, x, y, items, title=None):
        """Показать меню у точки (x, y) виджета parent. Повторный вызов для того же parent
        перерисовывает открытое меню на месте."""
        if self.pop is None or self.pop.get_parent() is not parent:
            self.destroy()
            self.pop = Gtk.Popover(has_arrow=False, autohide=True, position=Gtk.PositionType.BOTTOM)
            self.pop.add_css_class("sm-menu")
            self.pop.set_halign(Gtk.Align.START)
            self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.pop.set_child(self.box)
            self.pop.set_parent(parent)
        specs = ([("title", title)] if title else []) + list(items)
        for i, item in enumerate(specs):
            kind = _kind(item)
            if i < len(self.rows) and self.rows[i].kind != kind:
                self._drop(i)
            if i >= len(self.rows):
                row = _Row(self, kind)
                self.rows.append(row)
                self.box.append(row.widget)
            self.rows[i].set(item)
        self._drop(len(specs))
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        self.pop.set_pointing_to(rect)
        self.pop.popup()

    def _drop(self, start):
        for row in self.rows[start:]:
            self.box.remove(row.widget)
        del self.rows[start:]

    def _run(self, cb, keep):
        if not keep:
            self.pop.popdown()
        if cb:
            cb()

    def destroy(self):
        if self.pop is not None:
            self.pop.unparent()
            self.pop = None
            self.rows = []
