"""Панель — отдельная layer-shell поверхность со своим набором плиток.

Положение панели — отступы от левого верхнего угла рабочей области её монитора (без системной
панели): на Wayland окно не может поставить себя в произвольную точку экрана, а layer-shell может.
"""
import math
import socket
from collections import deque

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as LS  # noqa: E402

import render  # noqa: E402
from sensors import sensor_max  # noqa: E402
from theme import T, series_colors  # noqa: E402

from .gtkutil import clear, picture, texture  # noqa: E402
from .menu import PopMenu  # noqa: E402

HISTORY = 60
SCALES = (0.8, 1.0, 1.25, 1.5)  # размер панели относительно обычного
KEEP = 40                       # столько точек панели всегда остаётся на экране

# встроенные плитки: key -> (заголовок, иконка, фикс. максимум или None=авто, вид)
# вид: card — большая карточка, graph — значение с графиком, bars — столбцы, text — только текст
TILES = {
    "cpu_card":   ("Процессор — подробно", "cpu", None, "card"),
    "gpu_card":   ("Видеокарта — подробно", "gpu", None, "card"),
    "disks_card": ("Диски — подробно", "disk", None, "card"),
    "procs_card": ("Процессы — подробно", "procs", None, "card"),
    "cpu":      ("ЦП",                 "cpu", 100, "graph"),
    "cores":    ("Потоки ЦП",          "cores", 100, "bars"),
    "cpu_temp": ("Температура ЦП",     "temp", 100, "graph"),
    "ram":      ("Память",             "ram", 100, "graph"),
    "gpu":      ("Видеокарта",         "gpu", 100, "graph"),
    "gpu_temp": ("Температура ГП",     "temp", 100, "graph"),
    "vram":     ("Видеопамять",        "vram", 100, "graph"),
    "gpu_pwr":  ("Мощность ГП",        "power", None, "graph"),
    "disk":     ("Диск",               "disk", None, "graph"),
    "net":      ("Сеть",               "net", None, "graph"),
    "battery":  ("Батарея",            "battery", 100, "graph"),
    "clock":    ("Часы и дата",        "time", None, "text"),
    "weather":  ("Погода",             "weather", None, "text"),
}
LAST_CARD = "procs_card"  # после неё в меню — разделитель
SERIES_KEY = {"cpu_card": "cpu", "gpu_card": "gpu", "disks_card": "disk", "procs_card": "ram",
              "battery": "gpu", "clock": "vram", "weather": "gpu_pwr"}


class Tile:
    def __init__(self, panel, key, spec, history):
        self.panel, self.key = panel, key
        self.title, self.glyph, self.fixed_max, self.kind, self.colors = spec
        self.series = history
        self.bars = None
        self.main, self.sub, self.color = "—", "", None
        self.card = None
        self.pic = picture()
        self._drawn = None

    @property
    def size(self):
        s = self.panel.s
        if self.kind == "card":
            cols = self.panel.columns
            w = max(cols * int(206 * s) + (cols - 1) * self.panel.gap * 2, int(420 * s))
            c = self.card or {}
            nrows = math.ceil(min(len(c.get("rows", [])) or 6, 8) / 2)
            body = max(104, nrows * 42)            # высота под кольцо или под таблицу
            bars = 44 if c.get("bars") else 0
            return w, int((14 + 22 + 12 + body + bars + 14) * s)
        return int(206 * s), int(122 * s)

    def update(self, m, push=True):
        """m — словарь метрики от приложения или None (нет данных). push=False — только
        перерисовать с теми же показаниями, не добавляя точку в график."""
        if self.kind == "card":
            self.card = m
        elif m is None:
            self.main, self.sub, self.color = "нет данных", "", T["muted"]
        else:
            if push and m.get("values") is not None:
                for series, v in zip(self.series, m["values"]):
                    series.append(float(v))
            if m.get("bars") is not None:
                self.bars = m["bars"]
            self.main, self.sub, self.color = m.get("main", "—"), m.get("sub", ""), m.get("color")
        self.draw()

    def draw(self):
        s = self.panel.s
        w, h = self.size
        th = dict(T)
        # то же самое уже нарисовано (часы, диски, простой системы) — не тратим процессор
        state = (w, h, T["name"], T["style"], self.main, self.sub, self.color, repr(self.card),
                 tuple(self.bars or ()), tuple(tuple(x) for x in self.series))
        if state == self._drawn:
            return
        self._drawn = state
        if self.kind == "card":
            c = self.card or {"device": "нет данных", "ring": 0, "ring_text": "—", "rows": []}
            rows = [(lbl, val, _row_color(val, kind)) for lbl, val, kind in c["rows"]]
            img = render.card_image(th, s, w, h, glyph=self.glyph, title=self.title.split(" —")[0],
                                    device=c["device"], ring=c["ring"], ring_text=c["ring_text"], rows=rows,
                                    accent=self.colors[0], bars=c.get("bars"),
                                    ring_label=c.get("ring_label", "нагрузка"))
        else:
            style = T["style"]
            ring = None
            if style == "ring":
                if self.fixed_max == 100 and self.kind == "graph":
                    ring = self.series[0][-1] / 100
                else:
                    style = "area"
            top = self.fixed_max or (max(max(x) for x in self.series) * 1.15) or 1
            img = render.tile_image(th, s, w, h, title=self.title, glyph=self.glyph, main=self.main, sub=self.sub,
                                    value_color=self.color,
                                    series=None if self.kind == "text" else [list(x) for x in self.series],
                                    colors=self.colors, top=top, style=style,
                                    bars=self.bars if self.kind == "bars" else None, ring=ring)
        self.pic.set_paintable(texture(img))


def _row_color(text, kind):
    if kind == "hot":
        return T["hot"]
    if kind != "temp":
        return None
    try:
        t = float(text.split()[0])
    except (ValueError, IndexError):
        return None
    return T["hot"] if t >= 85 else T["warn"] if t >= 70 else None


def flat_corners(rect, others, screens, radius, tol=2):
    """Какие углы панели оставить прямыми: (левый верхний, правый верхний, левый нижний, правый нижний).
    Угол прямой, если одна из его сторон прижата к краю экрана или к ней встык стоит другая
    панель, закрывающая этот угол, — тогда панели выглядят одним целым.
    rect и others — (лево, верх, право, низ); screens — прямоугольники экрана."""
    l, t, r, b = rect
    left = any(l <= s[0] + tol for s in screens)
    top = any(t <= s[1] + tol for s in screens)
    right = any(r >= s[2] - tol for s in screens)
    bottom = any(b >= s[3] - tol for s in screens)

    def side(x_edge, mine_low, y):
        """Стоит ли сосед встык к вертикальной стороне x_edge и закрывает ли он точку y с запасом на скругление."""
        for ol, ot, orr, ob in others:
            near = abs((orr if mine_low else ol) - x_edge) <= tol
            if near and ot - tol <= y[0] and ob + tol >= y[1]:
                return True
        return False

    def cap(y_edge, mine_low, x):
        for ol, ot, orr, ob in others:
            near = abs((ob if mine_low else ot) - y_edge) <= tol
            if near and ol - tol <= x[0] and orr + tol >= x[1]:
                return True
        return False

    up, down = (t, t + radius), (b - radius, b)        # отрезки сторон, занятые скруглением
    lft, rgt = (l, l + radius), (r - radius, r)
    return (left or top or side(l, True, up) or cap(t, True, lft),
            right or top or side(r, False, up) or cap(t, True, rgt),
            left or bottom or side(l, True, down) or cap(b, False, lft),
            right or bottom or side(r, False, down) or cap(b, False, rgt))


class Panel:
    def __init__(self, app, pcfg):
        self.app, self.cfg = app, pcfg
        self.tiles = {}
        self.menu = PopMenu()
        self.x = self.y = 0
        self.size = (1, 1)
        self.visible = True
        self._drag = False
        self._reorder = None
        self._moved = False
        self._on_apps = False
        self._start = (0, 0)
        self._ghosts = []
        self._shape_now = None
        self._hdr_key = None

        self.win = win = Gtk.Window(application=app.gapp, title="SysMon Widget", decorated=False)
        win.add_css_class("sysmon-panel")
        LS.init_for_window(win)
        LS.set_namespace(win, "sysmon-widget")
        LS.set_anchor(win, LS.Edge.LEFT, True)
        LS.set_anchor(win, LS.Edge.TOP, True)
        LS.set_keyboard_mode(win, LS.KeyboardMode.NONE)
        self.monitor = app.monitor_for(pcfg.get("output"))
        if self.monitor is not None:
            LS.set_monitor(win, self.monitor)
        self.apply_layer()

        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.body.add_css_class("sm-body")
        self.body.set_name(f"smp-{self.pid}")
        self.body.set_halign(Gtk.Align.START)
        self.body.set_valign(Gtk.Align.START)
        self.root = Gtk.Box()  # постоянная обёртка: меню крепится к ней и переживает пересборку панели
        self.root.set_halign(Gtk.Align.START)
        self.root.set_valign(Gtk.Align.START)
        self.root.append(self.body)
        win.set_child(self.root)

        drag = Gtk.GestureDrag(button=1)
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        drag.connect("cancel", lambda *_a: self._drag_cancel())
        self.body.add_controller(drag)
        rclick = Gtk.GestureClick(button=3)
        rclick.connect("pressed", lambda _g, _n, x, y: self.app.show_panel_menu(self, pos=(x, y)))
        self.body.add_controller(rclick)

        self.build()
        self.place()
        win.present()

    @property
    def pid(self):
        return self.cfg["id"]

    @property
    def s(self):
        """Масштаб панели («Размер» в меню панели)."""
        try:
            k = float(self.cfg.get("scale", 1.0))
        except (TypeError, ValueError):
            k = 1.0
        return self.app.s * max(0.5, min(2.0, k))

    @property
    def columns(self):
        return max(1, int(self.cfg.get("columns", 2)))

    @property
    def locked(self):
        return bool(self.app.cfg["locked"] or self.cfg.get("locked"))

    @property
    def has_menu_button(self):
        return self.app.cfg.get("menu_panel") == self.pid

    def apply_layer(self):
        """«Поверх всех окон» — слой top (над окнами, но под полноэкранными), иначе — над обоями."""
        LS.set_layer(self.win, LS.Layer.TOP if self.app.cfg["topmost"] else LS.Layer.BOTTOM)

    # ---------- построение ----------
    def build(self):
        s = self.s
        clear(self.body)
        self.tiles = {}
        self._hdr_key = self._shape_now = None
        self.gap = g = int(4 * s)
        self.body.set_opacity(self.app.cfg["alpha"])

        self.apps_button = None
        self.header = None
        if self.cfg.get("header", True) or self.has_menu_button:
            head = Gtk.Box()
            head.set_margin_start(g)
            head.set_margin_end(g)
            head.set_margin_bottom(int(6 * s))
            if self.has_menu_button:
                imgs = (texture(render.apps_button_image(T, s)),
                        texture(render.apps_button_image(T, s, hover=True)))
                b = self.apps_button = picture()
                b.set_paintable(imgs[0])
                b.set_margin_end(int(8 * s))
                b.set_cursor(Gdk.Cursor.new_from_name("pointer"))
                hover = Gtk.EventControllerMotion()
                hover.connect("enter", lambda *_a: b.set_paintable(imgs[1]))
                hover.connect("leave", lambda *_a: b.set_paintable(imgs[0]))
                b.add_controller(hover)
                head.append(b)
            if self.cfg.get("header", True):
                self.header = picture()
                head.append(self.header)
            self.body.append(head)

        self.grid = Gtk.Grid(row_spacing=2 * g, column_spacing=2 * g)
        for side in ("top", "bottom", "start", "end"):
            getattr(self.grid, f"set_margin_{side}")(g)
        self.body.append(self.grid)
        self.layout()

    def spec(self, key):
        if key in TILES:
            title, glyph, mx, kind = TILES[key]
            colors = series_colors(SERIES_KEY.get(key, key))
            return title, glyph, mx, kind, colors
        meta = self.app.cfg["sensor_meta"].get(key)
        if meta:
            return (meta["title"], render.SENSOR_GLYPH.get(meta["type"], "load"), sensor_max(meta["type"]),
                    "graph", series_colors(key, meta["type"]))
        return None

    def layout(self):
        clear(self.grid)
        cols = self.columns
        r = c = 0
        for key in self.cfg["tiles"]:
            if key not in self.tiles:
                sp = self.spec(key)
                if not sp:
                    continue
                n = len(sp[4]) if sp[3] == "graph" else 1
                hist = self.app.hist.setdefault((self.pid, key),
                                                [deque([0.0] * HISTORY, maxlen=HISTORY) for _ in range(n)])
                self.tiles[key] = Tile(self, key, sp, hist)
            t = self.tiles[key]
            if t.kind == "card":  # карточка — на всю ширину, с новой строки
                if c:
                    r, c = r + 1, 0
                self.grid.attach(t.pic, 0, r, cols, 1)
                r += 1
            else:
                self.grid.attach(t.pic, c, r, 1, 1)
                c += 1
                if c >= cols:
                    r, c = r + 1, 0
            t.draw()
        for key in [k for k in self.tiles if k not in self.cfg["tiles"]]:
            self.tiles.pop(key)
        self._hdr_key = None
        self.render_header()
        self.resize()

    def render_header(self):
        if not self.header:
            return
        s = self.s
        grid_w = self.grid.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
        w = max(int(120 * s), grid_w - ((int(30 * s) + int(8 * s)) if self.apps_button else 0))
        title = socket.gethostname() if self.pid == "main" else self.cfg.get("name", "Панель")
        key = (w, title, self.app.uptime_text, self.locked, T["name"], s)
        if key == self._hdr_key:
            return
        self._hdr_key = key
        self.header.set_paintable(texture(render.header_image(T, s, w, title, self.app.uptime_text,
                                                              locked=self.locked)))

    # ---------- размер и форма ----------
    def resize(self):
        """Окно — ровно по содержимому (карточка могла вырасти или уменьшиться)."""
        w = self.body.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
        h = self.body.measure(Gtk.Orientation.VERTICAL, w)[1]
        if (w, h) != self.size:
            self.size = (w, h)
            self.win.set_default_size(w, h)
        self.sync_shape()

    def rect(self):
        return self.x, self.y, self.x + self.size[0], self.y + self.size[1]

    def _neighbours(self):
        return [p for p in self.app.panels if p is not self and p.visible and p.monitor == self.monitor]

    def sync_shape(self):
        """Скругление: углы у края экрана и между состыкованными панелями — прямые."""
        s = self.s
        radius = int(18 * s)
        w, h = self.app.workarea(self.monitor)
        flat = flat_corners(self.rect(), [p.rect() for p in self._neighbours()], [(0, 0, w, h)], radius)
        shape = (flat, radius, T["name"])
        if shape == self._shape_now:
            return
        self._shape_now = shape
        tl, tr, bl, br = (0 if f else radius for f in flat)
        self.css.load_from_string(
            f"#smp-{self.pid} {{ padding: {int(10 * s)}px; border-radius: {tl}px {tr}px {br}px {bl}px; }}")

    def update(self, metrics, push=True):
        for key, t in self.tiles.items():
            t.update(metrics.get(key), push)
        self.render_header()
        self.resize()

    # ---------- положение ----------
    def place(self):
        """Поставить панель на сохранённое место; новую или «потерявшуюся» — в правый верхний угол."""
        s = self.app.s
        w, h = self.size
        aw, ah = self.app.workarea(self.monitor)
        x, y = self.cfg.get("x"), self.cfg.get("y")
        fresh = x is None or y is None
        if fresh or not (x + KEEP < aw and x + w - KEEP > 0 and y + KEEP < ah and y + h - KEEP > 0):
            idx = [p["id"] for p in self.app.cfg["panels"]].index(self.pid)
            x = max(0, aw - w - int(24 * s) - idx * int(40 * s))
            y = int(24 * s) + idx * int(40 * s)
            if fresh:  # новая панель: место закрепляется сразу и не зависит от соседних панелей
                self.cfg["x"], self.cfg["y"] = x, y
        self.move(x, y)

    def move(self, x, y):
        self.x, self.y = int(x), int(y)
        if self._ghosts:
            return  # идёт перетаскивание: окно встанет на место, когда отпустят кнопку
        LS.set_margin(self.win, LS.Edge.LEFT, self.x)
        LS.set_margin(self.win, LS.Edge.TOP, self.y)
        self.sync_shape()

    def set_monitor(self, monitor):
        self.monitor = monitor
        if monitor is not None:
            self.cfg["output"] = monitor.get_connector()
            LS.set_monitor(self.win, monitor)

    def to_next_monitor(self):
        mons = self.app.monitors()
        if len(mons) < 2:
            return
        i = mons.index(self.monitor) if self.monitor in mons else -1
        self.set_monitor(mons[(i + 1) % len(mons)])
        aw, ah = self.app.workarea(self.monitor)
        self.move(max(0, min(self.x, aw - self.size[0])), max(0, min(self.y, ah - self.size[1])))
        self.cfg["x"], self.cfg["y"] = self.x, self.y
        for p in self.app.panels:
            p.sync_shape()
        self.app.save()

    def _snap(self, x, y):
        """Прилипание при перетаскивании: к краям рабочей области монитора и к другим панелям
        (встык и вровень краями)."""
        w, h = self.size
        t = int(12 * self.app.s)
        aw, ah = self.app.workarea(self.monitor)
        xs, ys = [0, aw - w], [0, ah - h]
        for p in self._neighbours():
            px, py, pr, pb = p.rect()
            pw, ph = pr - px, pb - py
            beside = y < py + ph + t and y + h > py - t   # панели на одной высоте — можно встык по горизонтали
            stacked = x < px + pw + t and x + w > px - t  # одна над другой — встык по вертикали
            if stacked:
                xs += [px, px + pw - w]
                ys += [py + ph, py - h]
            if beside:
                ys += [py, py + ph - h]
                xs += [px + pw, px - w]

        def pick(v, cands):
            best = min(cands, key=lambda c: abs(c - v))
            return best if abs(best - v) <= t else v

        return pick(x, xs), pick(y, ys)

    def _widget_at(self, x, y):
        return self.body.pick(x, y, Gtk.PickFlags.DEFAULT)

    def _tile_at(self, x, y):
        w = self._widget_at(x, y)
        return next((k for k, t in self.tiles.items() if t.pic is w), None)

    def _drag_begin(self, gesture, x, y):
        self._moved = False
        self._drag = False
        self._reorder = None
        self._on_apps = self.apps_button is not None and self._widget_at(x, y) is self.apps_button
        state = gesture.get_current_event_state()
        if state & Gdk.ModifierType.SHIFT_MASK:  # Shift — перетаскиваем плитку внутри панели
            self._reorder = self._tile_at(x, y)
            if self._reorder:
                self.body.set_cursor(Gdk.Cursor.new_from_name("move"))
        elif not self.locked:
            self._drag = True
            self._start = (self.x, self.y)

    def _drag_update(self, gesture, dx, dy):
        if abs(dx) + abs(dy) > 4:
            self._moved = True
        if self._drag and self._moved:
            if not self._ghosts:
                self._ghost_start()
            x, y = self._start[0] + dx, self._start[1] + dy
            if not gesture.get_current_event_state() & Gdk.ModifierType.ALT_MASK:  # Alt — без прилипания
                x, y = self._snap(x, y)
            if (int(x), int(y)) != (self.x, self.y):
                self.x, self.y = int(x), int(y)
                self._ghost_place()

    # Само окно панели во время перетаскивания стоит на месте: композиторы по-разному считают
    # координаты указателя, пока окно под ним едет (одни от старого места, другие от нового), и
    # панель либо убегает, либо дрожит. Поэтому двигаем её «снимок» на прозрачных окнах во весь
    # монитор, а настоящую панель переставляем один раз — когда кнопку отпустили.
    def _ghost_start(self):
        snap = Gtk.WidgetPaintable.new(self.body).get_current_image()
        w, h = self.size
        for m in self.app.monitors():
            win = Gtk.Window(application=self.app.gapp, decorated=False)
            win.add_css_class("sysmon-probe")
            LS.init_for_window(win)
            LS.set_namespace(win, "sysmon-drag")
            LS.set_layer(win, LS.Layer.OVERLAY)
            for edge in (LS.Edge.LEFT, LS.Edge.RIGHT, LS.Edge.TOP, LS.Edge.BOTTOM):
                LS.set_anchor(win, edge, True)
            LS.set_keyboard_mode(win, LS.KeyboardMode.NONE)
            LS.set_monitor(win, m)
            pic = Gtk.Picture(paintable=snap, can_shrink=False)
            pic.set_size_request(w, h)
            fixed = Gtk.Fixed()
            fixed.put(pic, -w - 100, -h - 100)
            over = Gtk.Overlay(child=Gtk.Box())  # Fixed — наложением, чтобы не раздувал окно
            over.add_overlay(fixed)
            over.set_can_target(False)
            win.set_child(over)
            win.present()
            self._ghosts.append((m, win, fixed, pic))
        self._ghost_place()
        # настоящую панель прячем, когда снимок уже на экране, — иначе она мигнёт
        GLib.timeout_add(80, lambda: (self._ghosts and self.body.set_opacity(0.01), False)[1])

    def _ghost_place(self):
        geo = self.monitor.get_geometry() if self.monitor is not None else None
        for m, _win, fixed, pic in self._ghosts:
            g = m.get_geometry()
            dx, dy = (geo.x - g.x, geo.y - g.y) if geo is not None else (0, 0)
            fixed.move(pic, self.x + dx, self.y + dy)

    def _ghost_stop(self):
        ghosts, self._ghosts = self._ghosts, []
        self.body.set_opacity(self.app.cfg["alpha"])
        if ghosts:  # снимок убираем чуть позже: окно панели переезжает не мгновенно
            GLib.timeout_add(140, lambda: ([g[1].destroy() for g in ghosts], False)[1])

    def _drag_cancel(self):
        if self._ghosts:
            self.x, self.y = self._start
            self._ghost_stop()
            self.move(*self._start)
        self._drag = False
        self._reorder = None
        self.body.set_cursor(None)

    def _drag_end(self, gesture, dx, dy):
        self.body.set_cursor(None)
        if self._reorder:
            ok, sx, sy = gesture.get_start_point()
            src, dst = self._reorder, self._tile_at(sx + dx, sy + dy) if ok else None
            order = self.cfg["tiles"]
            if dst and dst != src and src in order and dst in order:
                order.insert(order.index(dst), order.pop(order.index(src)))
                self.layout()
                self.app.save()
        elif self._drag and self._moved:
            self._ghost_stop()
            self._settle()
            self.cfg["x"], self.cfg["y"] = self.x, self.y
            self.app.save()
        elif not self._moved and self._on_apps:
            self.app.launcher.toggle()
        self._drag = False
        self._reorder = None

    def _settle(self):
        """После перетаскивания: панель, утащенная за край, переезжает на соседний монитор;
        иначе возвращается так, чтобы её было видно."""
        w, h = self.size
        if self.monitor is not None:
            geo = self.monitor.get_geometry()
            cx, cy = geo.x + self.x + w / 2, geo.y + self.y + h / 2
            for m in self.app.monitors():
                g = m.get_geometry()
                if m != self.monitor and g.x <= cx < g.x + g.width and g.y <= cy < g.y + g.height:
                    self.set_monitor(m)
                    self.x, self.y = int(cx - w / 2 - g.x), int(cy - h / 2 - g.y)
                    break
        aw, ah = self.app.workarea(self.monitor)
        self.move(max(KEEP - w, min(self.x, aw - KEEP)), max(0, min(self.y, ah - KEEP)))
        for p in self.app.panels:
            p.sync_shape()

    def set_visible(self, on):
        self.visible = on
        self.win.set_visible(on)

    def destroy(self):
        self.menu.destroy()
        Gtk.StyleContext.remove_provider_for_display(Gdk.Display.get_default(), self.css)
        self.win.destroy()
