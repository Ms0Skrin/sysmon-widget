"""Окно настроек: общие, оформление, плитки и датчики, панели, приложения."""
import random
from collections import defaultdict

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

import render  # noqa: E402
import updater  # noqa: E402
from sensors import TYPE_NAMES, fmt_value  # noqa: E402
from theme import GRAPH_STYLES, THEMES, alpha_to_pct, pct_to_alpha  # noqa: E402

from .config import autostart_enabled, set_autostart  # noqa: E402
from .gtkutil import ask_string, confirm, message, texture  # noqa: E402
from .panel import TILES  # noqa: E402

TABS = (("general", "Общие"), ("look", "Оформление"), ("sensors", "Плитки и датчики"),
        ("panels", "Панели"), ("apps", "Приложения"))


def plural_tiles(n):
    word = "плитка" if n % 10 == 1 and n % 100 != 11 else \
        "плитки" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "плиток"
    return f"{n} {word}"


def short_hw(name):
    for junk in ("NVIDIA GeForce ", "NVIDIA ", "AMD Radeon ", "AMD ", "Intel Core ", "Intel "):
        if name.startswith(junk) and len(name) > len(junk):
            return name[len(junk):]
    return name


def theme_preview(th, style):
    """Мини-превью: две плитки в цветах темы."""
    from PIL import Image
    th = dict(th)
    rnd = random.Random(th["title"])
    img = Image.new("RGB", (232, 84), render.hex_rgb(th["bg"]))
    for i, (title, glyph, main) in enumerate((("ЦП", "cpu", "37%"), ("Память", "ram", "62%"))):
        v, data = 40, []
        for _ in range(30):
            v = max(5, min(95, v + rnd.uniform(-18, 18)))
            data.append(v)
        st = style or th["style"]
        tile = render.tile_image(th, 0.5, 106, 68, title=title, glyph=glyph, main=main,
                                 series=[data], colors=[th["p"][i]], top=100,
                                 style="area" if st == "ring" else st, ring=None)
        img.paste(tile, (8 + i * 110, 8))
    return img


class SettingsWindow:
    def __init__(self, app):
        self.app = app
        self.win = None
        self.tab = "general"
        self.sensor_box = None
        self.apps_box = None

    # ---------- окно ----------
    def show(self, tab=None):
        if self.win is None:
            self.win = Gtk.Window(application=self.app.gapp, title="SysMon Widget — настройки",
                                  default_width=680, default_height=720)
            self.win.connect("close-request", self._closed)
        self.rebuild(tab)
        self.win.present()

    def _closed(self, *_a):
        self.win = None
        self.sensor_box = self.apps_box = None
        return False

    def rebuild(self, tab=None):
        """Собрать вкладки заново (после изменений, меняющих состав настроек)."""
        if self.win is None:
            return
        self.tab = tab or self.tab
        nb = Gtk.Notebook()
        for key, title in TABS:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            for side in ("top", "bottom", "start", "end"):
                getattr(box, f"set_margin_{side}")(16)
            getattr(self, f"tab_{key}")(box)
            page = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=box)
            nb.append_page(page, Gtk.Label(label=title))
        nb.set_current_page([k for k, _t in TABS].index(self.tab))
        nb.connect("switch-page", lambda _nb, _page, i: setattr(self, "tab", TABS[i][0]))
        self.win.set_child(nb)

    # ---------- кирпичики ----------
    @staticmethod
    def section(box, text):
        lbl = Gtk.Label(xalign=0, margin_top=10 if box.get_first_child() else 0, margin_bottom=2)
        lbl.set_markup(f"<b>{GLib.markup_escape_text(text)}</b>")
        box.append(lbl)

    @staticmethod
    def note(box, text):
        lbl = Gtk.Label(label=text, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        lbl.add_css_class("dim-label")
        lbl.add_css_class("caption")
        box.append(lbl)
        return lbl

    @staticmethod
    def check(box, text, active, cb):
        c = Gtk.CheckButton(label=text, active=bool(active))
        c.connect("toggled", lambda w: cb(w.get_active()))
        box.append(c)
        return c

    @staticmethod
    def slider(box, text, value, lo, hi, cb, fmt=lambda v: f"{v}%"):
        row = Gtk.Box(spacing=10)
        row.append(Gtk.Label(label=text, xalign=0, width_chars=26))
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
        scale.set_value(value)
        scale.set_hexpand(True)
        scale.set_draw_value(False)
        val = Gtk.Label(label=fmt(int(value)), width_chars=7, xalign=1)

        def changed(s):
            v = int(round(s.get_value()))
            val.set_label(fmt(v))
            cb(v)

        scale.connect("value-changed", changed)
        row.append(scale)
        row.append(val)
        box.append(row)
        return scale

    @staticmethod
    def button(text, cb, accent=False):
        b = Gtk.Button(label=text)
        if accent:
            b.add_css_class("suggested-action")
        b.connect("clicked", lambda _b: cb())
        return b

    # ---------- вкладка «Общие» ----------
    def tab_general(self, p):
        app = self.app
        self.section(p, "Панель приложений")
        hk = app.hotkey_text()
        self.note(p, (f"Горячая клавиша: {hk}. " if hk else "") +
                  "Панель открывается кнопкой с точками на панели, из трея или командой "
                  "«sysmon-widget --launcher» — её можно назначить на любое сочетание клавиш "
                  "в настройках композитора (niri, Hyprland, Sway…).")
        self.slider(p, "Прозрачность панели", alpha_to_pct(app.cfg["launcher_alpha"]), 0, 100,
                    lambda v: app.set_launcher_alpha(pct_to_alpha(v)))
        self.slider(p, "Размер значков", app.launcher.icon_px(), 24, 96, app.launcher.set_icon_size,
                    fmt=lambda v: f"{v} px")
        self.slider(p, "Колонок", int(app.cfg.get("launcher_cols", 0)), 0, 12, app.launcher.set_cols,
                    fmt=lambda v: "авто" if v == 0 else str(v))

        self.section(p, "Панели")
        self.slider(p, "Прозрачность панелей", alpha_to_pct(app.cfg["alpha"]), 0, 100,
                    lambda v: app.set_alpha(pct_to_alpha(v)))
        self.check(p, "Закрепить все панели на месте", app.cfg["locked"], app.set_locked)
        self.check(p, "Поверх всех окон", app.cfg["topmost"], app.set_topmost)
        self.note(p, "Обычно панели лежат на рабочем столе под окнами. «Поверх всех окон» поднимает их "
                     "над окнами; полноэкранные игры и видео их всё равно закрывают.")

        self.section(p, "Запуск")
        self.check(p, "Запускать виджет при входе в систему", autostart_enabled(), self._autostart)

        self.section(p, "Обновления")
        row = Gtk.Box(spacing=10)
        row.append(Gtk.Label(label=f"Версия: {updater.version()}", xalign=0, hexpand=True))
        row.append(self.button("Проверить обновления", app.check_updates))
        p.append(row)

    def _autostart(self, on):
        try:
            set_autostart(on)
        except OSError as ex:
            message(self.win, "Автозапуск", str(ex))

    # ---------- вкладка «Оформление» ----------
    def tab_look(self, p):
        app = self.app
        self.section(p, "Тема")
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=2,
                           min_children_per_line=2, column_spacing=6, row_spacing=6, homogeneous=True)
        group = None
        for key, th in THEMES.items():
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            pic = Gtk.Picture(can_shrink=False, halign=Gtk.Align.CENTER)
            pic.set_paintable(texture(theme_preview(th, app.cfg["graph_style"])))
            box.append(pic)
            box.append(Gtk.Label(label=th["title"]))
            btn = Gtk.ToggleButton(child=box, active=key == app.cfg["theme"])
            if group is None:
                group = btn
            else:
                btn.set_group(group)
            btn.connect("toggled", lambda b, k=key: b.get_active() and app.apply_theme(k, app.cfg["graph_style"]))
            flow.append(btn)
        p.append(flow)

        self.section(p, "Стиль графиков")
        row = Gtk.Box(spacing=12)
        group = None
        for val, title in [("", "Как в теме")] + list(GRAPH_STYLES.items()):
            rb = Gtk.CheckButton(label=title, active=val == app.cfg["graph_style"])
            if group is None:
                group = rb
            else:
                rb.set_group(group)
            rb.connect("toggled", lambda b, v=val: b.get_active() and self._style(v))
            row.append(rb)
        p.append(row)
        self.note(p, "Тема «Системная» берёт цвета рабочего стола (палитру обоев), если оболочка их отдаёт.")

    def _style(self, style):
        self.app.apply_theme(self.app.cfg["theme"], style)
        self.rebuild("look")  # превью тем — в новом стиле

    # ---------- вкладка «Плитки и датчики» ----------
    def tab_sensors(self, p):
        app = self.app
        self.section(p, "Плитки")
        ids = [pc["id"] for pc in app.cfg["panels"]]
        if app.edit_pid not in ids:
            app.edit_pid = ids[0]
        row = Gtk.Box(spacing=10)
        row.append(Gtk.Label(label="Добавлять в панель:", xalign=0))
        dd = Gtk.DropDown.new_from_strings([pc.get("name", pc["id"]) for pc in app.cfg["panels"]])
        dd.set_selected(ids.index(app.edit_pid))

        def picked(d, _p):
            app.edit_pid = ids[d.get_selected()]
            self.fill_sensor_list()

        dd.connect("notify::selected", picked)
        row.append(dd)
        self.sq = Gtk.SearchEntry(placeholder_text="Поиск датчика…", hexpand=True)
        self.sq.connect("search-changed", lambda *_a: self.fill_sensor_list())
        row.append(self.sq)
        p.append(row)
        self.note(p, "Датчики читаются прямо из системы (hwmon, nvidia-smi) — права администратора не нужны. "
                     "Если не хватает датчиков материнской платы, поставь lm_sensors и выполни sensors-detect.")
        self.sensor_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        p.append(self.sensor_box)
        self.fill_sensor_list()

    def fill_sensor_list(self):
        app, box = self.app, self.sensor_box
        if box is None:
            return
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt
        q = self.sq.get_text().strip().lower()

        def group(title):
            lbl = Gtk.Label(xalign=0, margin_top=8)
            lbl.set_markup(f"<b>{GLib.markup_escape_text(title)}</b>")
            lbl.add_css_class("accent")
            box.append(lbl)

        builtin = [(k, spec[0]) for k, spec in TILES.items() if not q or q in spec[0].lower()]
        if builtin:
            group("Основные")
            for key, title in builtin:
                self.check(box, title, app.tile_enabled(key), lambda on, k=key: app.set_tile(k, on))

        lhm, ok = app.sensors.get_lhm()
        if not ok:
            self.note(box, "Датчики ещё не прочитаны — открой вкладку через пару секунд.")
            return
        groups = defaultdict(list)
        for sen in lhm:
            label = f"{sen['hw']} · {TYPE_NAMES.get(sen['type'], sen['type'])} · {sen['name']}"
            if q and q not in label.lower():
                continue
            groups[sen["group"]].append(sen)
        for gname, items in groups.items():
            group(gname)
            items.sort(key=lambda x: x["type"])
            for sen in items:
                key = "lhm:" + sen["id"]
                tname = TYPE_NAMES.get(sen["type"], sen["type"])
                text = f"{tname}: {sen['name']}   —   {fmt_value(sen['type'], sen['value'])}"
                meta = {"title": f"{short_hw(sen['hw'])} · {sen['name']}", "type": sen["type"]}
                self.check(box, text, app.tile_enabled(key),
                           lambda on, k=key, m=meta: app.set_tile(k, on, m))

    # ---------- вкладка «Панели» ----------
    def tab_panels(self, p):
        app = self.app
        top = Gtk.Box(spacing=10)
        top.append(self.button("+ Новая панель", lambda: app.ask_new_panel(self.win, lambda: self.rebuild("panels")),
                               accent=True))
        p.append(top)
        self.note(p, "Каждая панель — отдельный виджет со своими плитками. Двигай мышью, ПКМ по панели — "
                     "её меню. Кнопка приложений — только на одной панели.")
        group = None
        for pc in app.cfg["panels"]:
            pid = pc["id"]
            frame = Gtk.Frame(margin_top=4)
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            for side in ("top", "bottom", "start", "end"):
                getattr(card, f"set_margin_{side}")(10)
            head = Gtk.Box(spacing=8)
            name = Gtk.Label(xalign=0)
            name.set_markup(f"<b>{GLib.markup_escape_text(pc.get('name', pid))}</b>")
            head.append(name)
            cnt = Gtk.Label(label=plural_tiles(len(pc["tiles"])), xalign=0, hexpand=True)
            cnt.add_css_class("dim-label")
            head.append(cnt)
            head.append(self.button("Плитки…", lambda x=pid: (setattr(app, "edit_pid", x), self.rebuild("sensors"))))
            head.append(self.button("Переименовать", lambda x=pid: self.panel_op("rename", x)))
            rm = self.button("Удалить", lambda x=pid: self.panel_op("remove", x))
            rm.set_sensitive(len(app.cfg["panels"]) > 1)
            head.append(rm)
            card.append(head)

            row = Gtk.Box(spacing=14)
            rb = Gtk.CheckButton(label="Кнопка приложений", active=app.cfg["menu_panel"] == pid)
            if group is None:
                group = rb
            else:
                rb.set_group(group)
            rb.connect("toggled", lambda b, x=pid: b.get_active() and app.set_menu_panel(x))
            row.append(rb)
            ch = Gtk.CheckButton(label="Заголовок", active=pc.get("header", True))
            ch.connect("toggled", lambda b, x=pid: app.set_panel_option(x, "header", b.get_active()))
            row.append(ch)
            row.append(Gtk.Label(label="Колонки:"))
            spin = Gtk.SpinButton.new_with_range(1, 4, 1)
            spin.set_value(pc.get("columns", 2))
            spin.connect("value-changed", lambda sp, x=pid: app.set_panel_option(x, "columns", sp.get_value_as_int()))
            row.append(spin)
            card.append(row)
            frame.set_child(card)
            p.append(frame)
        self.panels_extra(p)

    def panels_extra(self, p):
        """Низ вкладки «Панели»: раскладки, поведение, погода, перенос настроек."""
        app = self.app
        self.section(p, "Раскладки")
        self.note(p, "Раскладка запоминает панели, их плитки и места. Переключать можно и из меню панели.")
        for name in list(app.cfg["profiles"]):
            r = Gtk.Box(spacing=8)
            r.append(Gtk.Label(label=name, xalign=0, hexpand=True))
            r.append(self.button("Применить", lambda n=name: (app.apply_profile(n), self.rebuild("panels"))))
            r.append(self.button("Удалить", lambda n=name: (app.delete_profile(n), self.rebuild("panels"))))
            p.append(r)
        r = Gtk.Box()
        r.append(self.button("Сохранить текущую раскладку…",
                             lambda: app.ask_save_profile(self.win, lambda: self.rebuild("panels"))))
        p.append(r)

        self.section(p, "Поведение")
        self.note(p, "Панели липнут к краям экрана и друг к другу (Alt — без прилипания). Shift + перетаскивание "
                     "переставляет плитки внутри панели. Панель, утащенная за край, переезжает на соседний монитор.")
        for opt, text in (("alert_cpu", "Уведомлять: ЦП горячее"), ("alert_gpu", "Уведомлять: ГП горячее")):
            self.slider(p, text, app.cfg.get(opt) or 50, 50, 105,
                        lambda v, k=opt: self.set_opt(k, 0 if v <= 50 else v),
                        fmt=lambda v: "выкл" if v <= 50 else f"{v} °C")

        self.section(p, "Погода")
        r = Gtk.Box(spacing=8)
        r.append(Gtk.Label(label="Город:"))
        city = Gtk.Entry(text=app.cfg.get("weather_city", ""), hexpand=True)
        save = lambda *_a: self.set_opt("weather_city", city.get_text().strip())  # noqa: E731
        city.connect("activate", save)
        r.append(city)
        r.append(self.button("Сохранить", save))
        p.append(r)
        self.note(p, "Для плитки «Погода» (данные open-meteo.com; отправляется только название города).")

        self.section(p, "Перенос настроек")
        r = Gtk.Box(spacing=8)
        r.append(self.button("Экспорт в файл…", self.export_settings))
        r.append(self.button("Импорт из файла…", self.import_settings))
        p.append(r)

    def set_opt(self, key, value):
        self.app.cfg[key] = value
        self.app.save()

    def _json_filter(self):
        f = Gtk.FileFilter()
        f.set_name("JSON")
        f.add_pattern("*.json")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(f)
        return store

    def export_settings(self):
        dlg = Gtk.FileDialog(title="Экспорт настроек", initial_name="sysmon-widget.json",
                             filters=self._json_filter())

        def done(d, res):
            try:
                path = d.save_finish(res).get_path()
            except GLib.Error:
                return
            try:
                self.app.export_settings(path)
            except OSError as ex:
                message(self.win, "Экспорт настроек", str(ex))

        dlg.save(self.win, None, done)

    def import_settings(self):
        dlg = Gtk.FileDialog(title="Импорт настроек", filters=self._json_filter())

        def done(d, res):
            try:
                path = d.open_finish(res).get_path()
            except GLib.Error:
                return

            def go():
                err = self.app.import_settings(path)
                if err:
                    message(self.win, "Импорт настроек", err)

            confirm(self.win, "Импорт настроек",
                    "Текущие настройки будут заменены, виджет перезапустится. Продолжить?", go)

        dlg.open(self.win, None, done)

    def panel_op(self, op, pid):
        app = self.app
        pc = app.panel_cfg(pid)
        if pc is None:
            return
        if op == "rename":
            ask_string(self.win, "Переименовать панель", "Название панели:", pc.get("name", ""),
                       lambda name: (app.rename_panel(pid, name), self.rebuild("panels")))
        elif op == "remove":
            confirm(self.win, "Удалить панель", f"Удалить «{pc.get('name', 'панель')}»?",
                    lambda: (app.remove_panel(pid), self.rebuild("panels")))

    # ---------- вкладка «Приложения» ----------
    def tab_apps(self, p):
        L = self.app.launcher
        self.section(p, "Закреплённые в панели приложений")
        pins = L.pinned()
        if not pins:
            self.note(p, "Пока ничего не закреплено — панель показывает все установленные приложения. "
                         "Закрепи нужные ниже (или ПКМ по значку в самой панели), и останутся только они; "
                         "остальные по-прежнему находятся поиском.")
        for e in pins:
            i = e.pinned
            r = Gtk.Box(spacing=8)
            img = Gtk.Image.new_from_gicon(e.icon)
            img.set_pixel_size(28)
            r.append(img)
            r.append(Gtk.Label(label=e.name, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
            for text, cb in (("↑", lambda i=i: (L.move(i, -1), self.rebuild("apps"))),
                             ("↓", lambda i=i: (L.move(i, 1), self.rebuild("apps"))),
                             ("Имя…", lambda i=i: L.rename(i, self.win, lambda: self.rebuild("apps"))),
                             ("Открепить", lambda i=i: (L.remove(i), self.rebuild("apps")))):
                r.append(self.button(text, cb))
            p.append(r)

        self.section(p, "Установленные приложения")
        self.aq = Gtk.SearchEntry(placeholder_text="Поиск приложения…")
        self.aq.connect("search-changed", lambda *_a: self.fill_apps())
        p.append(self.aq)
        self.apps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        p.append(self.apps_box)
        self.fill_apps()

    def fill_apps(self):
        box, L = self.apps_box, self.app.launcher
        if box is None:
            return
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt
        q = self.aq.get_text().strip().casefold()
        for e in L.all_apps():
            if L.is_pinned(e.path) or (q and not e.matches(q)):
                continue
            r = Gtk.Box(spacing=8)
            img = Gtk.Image.new_from_gicon(e.icon)
            img.set_pixel_size(24)
            r.append(img)
            r.append(Gtk.Label(label=e.name, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
            r.append(self.button("Закрепить", lambda e=e: (L.pin(e), self.rebuild("apps"))))
            box.append(r)
