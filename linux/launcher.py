"""Панель приложений: появляется в центре экрана поверх всех окон.

Показывает закреплённые приложения (если их нет — все установленные). Набор текста — поиск по
всем установленным, Enter — первое найденное, 1–9 — запуск по номеру, ПКМ — закрепить/открепить.
"""
import math
import os
import shutil
import subprocess
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402
from gi.repository import Gtk4LayerShell as LS  # noqa: E402

from .gtkutil import ask_string, clear  # noqa: E402
from .menu import PopMenu  # noqa: E402

ICON_MIN, ICON_MAX = 24, 96
COLS_MAX = 12
MAX_SHOWN = 96  # больше плиток за раз не рисуем — дальше помогает поиск
DIGITS = {getattr(Gdk, f"KEY_{i}"): i for i in range(1, 10)}
DIGITS.update({getattr(Gdk, f"KEY_KP_{i}"): i for i in range(1, 10)})


def grid_cols(total):
    cols = math.ceil(math.sqrt(total * 1.6))
    return max(3, min(8, cols, max(total, 3)))


class Entry:
    """Строка панели: приложение из системы (info) или произвольный путь."""

    def __init__(self, name, path, info=None, pinned=None):
        self.name, self.path, self.info, self.pinned = name, path, info, pinned

    @property
    def icon(self):
        ic = self.info.get_icon() if self.info is not None else None
        return ic or Gio.ThemedIcon.new("application-x-executable")

    def matches(self, q):
        if q in self.name.casefold():
            return True
        if self.info is None:
            return q in self.path.casefold()
        hay = [self.info.get_id() or "", self.info.get_executable() or "",
               self.info.get_generic_name() or "", *self.info.get_keywords()]
        return any(q in h.casefold() for h in hay)


def resolve(path):
    """Путь из настроек → Gio.DesktopAppInfo или None (обычный файл/программа)."""
    try:
        if path.endswith(".desktop"):
            if os.path.isabs(path):
                return Gio.DesktopAppInfo.new_from_filename(path)
            return Gio.DesktopAppInfo.new(path)
    except TypeError:  # файла больше нет
        pass
    return None


def installed_apps():
    """Все приложения из меню системы, по алфавиту: [Entry]."""
    out = []
    for info in Gio.AppInfo.get_all():
        if info.should_show() and info.get_id():
            out.append(Entry(info.get_display_name() or info.get_name(), info.get_id(), info))
    out.sort(key=lambda e: e.name.casefold())
    return out


def _spawn(argv, cwd=None):
    p = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    threading.Thread(target=p.wait, daemon=True).start()  # чтобы не оставалось «зомби»


def launch(entry):
    """Запуск в отдельной области systemd: приложение не закроется вместе с виджетом."""
    scope = (["systemd-run", "--user", "--scope", "--quiet", "--collect", "--"]
             if shutil.which("systemd-run") else [])
    try:
        if entry.info is not None:
            fn = entry.info.get_filename()
            if fn and shutil.which("gio"):
                _spawn(scope + ["gio", "launch", fn])
            else:
                entry.info.launch([], None)
        elif os.path.isfile(entry.path) and os.access(entry.path, os.X_OK):
            _spawn(scope + [entry.path], cwd=os.path.dirname(entry.path) or None)
        else:
            _spawn(scope + ["gio", "open", entry.path])
        return None
    except (OSError, GLib.Error) as ex:
        return str(ex)


class Launcher:
    def __init__(self, app):
        self.app, self.cfg = app, app.cfg
        self.win = None
        self.shown = []
        self.menu = PopMenu()
        self._all = None
        self._monitor = Gio.AppInfoMonitor.get()  # поставили/удалили программу — список перечитается
        self._monitor.connect("changed", lambda *_a: setattr(self, "_all", None))

    # ---------- данные ----------
    def all_apps(self):
        if self._all is None:
            self._all = installed_apps()
        return self._all

    def pinned(self):
        out = []
        for i, a in enumerate(self.cfg["apps"]):
            info = resolve(a["path"])
            if info is None and a["path"].endswith(".desktop") and not os.path.isabs(a["path"]):
                continue  # приложение удалили из системы
            out.append(Entry(a.get("name") or (info.get_display_name() if info else a["path"]),
                             a["path"], info, pinned=i))
        return out

    def entries(self, query=""):
        pins = self.pinned()
        q = query.strip().casefold()
        if not q:
            return pins or self.all_apps()
        have = {e.path for e in pins}
        found = [e for e in pins if e.matches(q)] + [e for e in self.all_apps()
                                                     if e.path not in have and e.matches(q)]
        # сначала те, у кого название начинается с запроса
        found.sort(key=lambda e: not e.name.casefold().startswith(q))
        return found

    # ---------- настройки вида ----------
    def icon_px(self):
        return max(ICON_MIN, min(ICON_MAX, int(self.cfg.get("launcher_icon", 48))))

    def set_icon_size(self, px):
        self.cfg["launcher_icon"] = max(ICON_MIN, min(ICON_MAX, int(px)))
        self.app.save()
        self.refresh()

    def set_cols(self, n):
        self.cfg["launcher_cols"] = max(0, min(COLS_MAX, int(n)))
        self.app.save()
        self.refresh()

    def set_alpha(self, a):
        if self.win is not None:
            self.card.set_opacity(a)

    # ---------- окно ----------
    def visible(self):
        return self.win is not None and self.win.get_visible()

    def toggle(self):
        self.hide() if self.visible() else self.show()

    def _build(self):
        self.win = win = Gtk.Window(application=self.app.gapp, title="SysMon Widget — приложения",
                                    decorated=False)
        win.add_css_class("sysmon-launcher")
        LS.init_for_window(win)
        LS.set_namespace(win, "sysmon-launcher")
        LS.set_layer(win, LS.Layer.OVERLAY)
        for edge in (LS.Edge.LEFT, LS.Edge.RIGHT, LS.Edge.TOP, LS.Edge.BOTTOM):
            LS.set_anchor(win, edge, True)
        LS.set_exclusive_zone(win, -1)
        LS.set_keyboard_mode(win, LS.KeyboardMode.EXCLUSIVE)

        self.backdrop = Gtk.Box()
        self.backdrop.add_css_class("sm-backdrop")
        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, hexpand=True, vexpand=True)
        self.card.add_css_class("sm-card")
        self.backdrop.append(self.card)
        win.set_child(self.backdrop)

        self.entry = Gtk.Entry(placeholder_text="Поиск приложений…")
        self.entry.connect("changed", lambda *_a: self.refresh())
        self.card.append(self.entry)
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                column_spacing=4, row_spacing=4, valign=Gtk.Align.START)
        self.scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                         propagate_natural_height=True, propagate_natural_width=True,
                                         max_content_height=600)
        self.scroll.set_child(self.flow)
        self.card.append(self.scroll)
        self.empty = Gtk.Label(label="Ничего не найдено")
        self.empty.add_css_class("sm-dim")
        self.card.append(self.empty)

        foot = Gtk.Box(spacing=12)
        self.hint = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.hint.add_css_class("sm-dim")
        foot.append(self.hint)
        manage = Gtk.Button(label="Настроить…")
        manage.add_css_class("sm-app")
        manage.connect("clicked", lambda *_a: (self.hide(), self.app.settings.show("apps")))
        foot.append(manage)
        self.card.append(foot)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        win.add_controller(keys)
        click = Gtk.GestureClick(button=0)
        click.connect("pressed", self._on_backdrop)
        self.backdrop.add_controller(click)

    def show(self):
        if self.win is None:
            self._build()
        self.card.set_opacity(self.cfg["launcher_alpha"])
        self.entry.set_text("")
        self.refresh()
        self.win.present()
        self.entry.grab_focus()

    def hide(self):
        if self.win is not None:
            self.menu.close()
            self.win.set_visible(False)

    def _on_backdrop(self, gesture, _n, x, y):
        if self.backdrop.pick(x, y, Gtk.PickFlags.DEFAULT) is self.backdrop:
            self.hide()

    def refresh(self):
        if self.win is None:
            return
        query = self.entry.get_text()
        self.shown = self.entries(query)[:MAX_SHOWN]
        clear(self.flow)
        px = self.icon_px()
        cols = int(self.cfg.get("launcher_cols", 0)) or grid_cols(len(self.shown))
        self.flow.set_min_children_per_line(cols)
        self.flow.set_max_children_per_line(cols)
        for i, e in enumerate(self.shown):
            self.flow.append(self._tile(i, e, px))
        child = self.flow.get_first_child()
        while child is not None:  # фокус — сразу на кнопке, а не на её обёртке
            child.set_focusable(False)
            child = child.get_next_sibling()
        self.scroll.set_visible(bool(self.shown))
        self.empty.set_visible(not self.shown)
        pinned = bool(self.cfg["apps"])
        self.hint.set_label("Enter — первое · 1–9 — по номеру · ПКМ — " +
                            ("открепить, переименовать" if pinned and not query.strip() else "закрепить") +
                            " · Esc — закрыть")

    def _tile(self, i, e, px):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        img = Gtk.Image.new_from_gicon(e.icon)
        img.set_pixel_size(px)
        box.append(img)
        lbl = Gtk.Label(label=e.name, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, lines=2,
                        ellipsize=Pango.EllipsizeMode.END, justify=Gtk.Justification.CENTER,
                        max_width_chars=12, width_chars=12, valign=Gtk.Align.START)
        box.append(lbl)
        over = Gtk.Overlay(child=box)
        if i < 9:
            badge = Gtk.Label(label=str(i + 1), halign=Gtk.Align.START, valign=Gtk.Align.START)
            badge.add_css_class("sm-badge")
            over.add_overlay(badge)
        btn = Gtk.Button(child=over, tooltip_text=e.name)
        btn.add_css_class("sm-app")
        btn.set_size_request(px + 60, -1)
        btn.connect("clicked", lambda _b: self.launch(e))
        rclick = Gtk.GestureClick(button=3)
        rclick.connect("pressed", lambda _g, _n, x, y: self._tile_menu(btn, e, x, y))
        btn.add_controller(rclick)
        return btn

    def _on_key(self, _ctl, keyval, _code, state):
        if keyval == Gdk.KEY_Escape:
            if self.menu.visible():
                self.menu.close()
            else:
                self.hide()
            return True
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK):
            return False
        in_search = isinstance(self.win.get_focus(), (Gtk.Text, Gtk.Entry)) or self.win.get_focus() is None
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and in_search:
            if self.shown:
                self.launch(self.shown[0])
            return True
        if keyval in DIGITS and not self.entry.get_text():
            if DIGITS[keyval] <= len(self.shown):
                self.launch(self.shown[DIGITS[keyval] - 1])
            return True
        if keyval == Gdk.KEY_Down and in_search and self.shown:
            first = self.flow.get_first_child()
            if first is not None and first.get_child() is not None:
                first.get_child().grab_focus()
            return True
        return False

    def launch(self, e):
        self.hide()
        err = launch(e)
        if err:
            self.app.tray.notify("Не удалось запустить", f"{e.name}: {err}")

    # ---------- закреплённые ----------
    def _tile_menu(self, btn, e, x, y):
        if e.pinned is None:
            items = [("pin", "Закрепить в панели", lambda: self.pin(e))]
        else:
            i = e.pinned
            items = [("left", "Сдвинуть влево", lambda: self.move(i, -1)),
                     ("right", "Сдвинуть вправо", lambda: self.move(i, 1)),
                     ("edit", "Переименовать…", lambda: self.rename(i)),
                     ("-",),
                     ("delete", "Открепить", lambda: self.remove(i))]
        self.menu.show(btn, x, y, items, title=e.name)

    def is_pinned(self, path):
        return any(a["path"] == path for a in self.cfg["apps"])

    def pin(self, e):
        if not self.is_pinned(e.path):
            self.cfg["apps"].append({"name": e.name, "path": e.path})
            self.app.save()
        self.refresh()

    def remove(self, i):
        if 0 <= i < len(self.cfg["apps"]):
            self.cfg["apps"].pop(i)
            self.app.save()
        self.refresh()

    def move(self, i, d):
        apps = self.cfg["apps"]
        j = i + d
        if 0 <= i < len(apps) and 0 <= j < len(apps):
            apps[i], apps[j] = apps[j], apps[i]
            self.app.save()
        self.refresh()

    def rename(self, i, parent=None, done=None):
        apps = self.cfg["apps"]
        if not 0 <= i < len(apps):
            return
        self.hide()  # панель держит клавиатуру — окно ввода под ней было бы недоступно

        def ok(text):
            apps[i]["name"] = text
            self.app.save()
            if done:
                done()

        ask_string(parent, "Переименовать", "Название в панели приложений:", apps[i].get("name", ""), ok)
