"""Панель — отдельное окно-виджет со своим набором плиток. Плиток и панелей может быть сколько угодно."""
import ctypes
import math
import socket
import tkinter as tk
from collections import deque
from ctypes import wintypes

from PIL import ImageTk

import render
from sensors import sensor_max
from theme import T, round_region, series_colors

HISTORY = 60

_user32 = ctypes.windll.user32
_user32.MonitorFromRect.restype = ctypes.c_void_p
_user32.MonitorFromRect.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD]

# встроенные плитки: key -> (заголовок, иконка, фикс. максимум или None=авто, вид)
TILES = {
    "cpu_card": ("Процессор — подробно", "cpu", None, "card"),
    "gpu_card": ("Видеокарта — подробно", "gpu", None, "card"),
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
}
SERIES_KEY = {"cpu_card": "cpu", "gpu_card": "gpu"}


class Tile:
    def __init__(self, panel, key, spec, history):
        self.panel, self.key = panel, key
        self.title, self.glyph, self.fixed_max, self.kind, self.colors = spec
        self.series = history
        self.bars = None
        self.main, self.sub, self.color = "—", "", None
        self.card = None
        self.label = tk.Label(panel.grid, bd=0, bg=T["bg"])
        self.photo = None

    @property
    def size(self):
        s = self.panel.app.s
        if self.kind == "card":
            cols = self.panel.columns
            w = max(cols * int(206 * s) + (cols - 1) * self.panel.gap * 2, int(420 * s))
            c = self.card or {}
            nrows = math.ceil(min(len(c.get("rows", [])) or 6, 8) / 2)
            body = max(104, nrows * 42)            # высота под кольцо или под таблицу
            bars = 44 if c.get("bars") else 0
            return w, int((14 + 22 + 12 + body + bars + 14) * s)
        return int(206 * s), int(122 * s)

    def update(self, m):
        """m — словарь метрики от приложения или None (нет данных)."""
        if self.kind == "card":
            self.card = m
        elif m is None:
            self.main, self.sub, self.color = "нет данных", "", T["muted"]
        else:
            if m.get("values") is not None:
                for series, v in zip(self.series, m["values"]):
                    series.append(float(v))
            if m.get("bars") is not None:
                self.bars = m["bars"]
            self.main, self.sub, self.color = m.get("main", "—"), m.get("sub", ""), m.get("color")
        self.draw()

    def draw(self):
        s = self.panel.app.s
        w, h = self.size
        th = dict(T)
        if self.kind == "card":
            c = self.card or {"device": "нет данных", "ring": 0, "ring_text": "—", "rows": []}
            rows = [(lbl, val, _temp_color(val) if kind == "temp" else None)
                    for lbl, val, kind in c["rows"]]
            img = render.card_image(th, s, w, h, glyph=self.glyph, title=self.title.split(" —")[0],
                                    device=c["device"], ring=c["ring"], ring_text=c["ring_text"], rows=rows,
                                    accent=self.colors[0], bars=c.get("bars"))
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
                                    value_color=self.color, series=[list(x) for x in self.series],
                                    colors=self.colors, top=top, style=style,
                                    bars=self.bars if self.kind == "bars" else None, ring=ring)
        self.photo = ImageTk.PhotoImage(img)
        self.label.config(image=self.photo)


def _temp_color(text):
    try:
        t = float(text.split()[0])
    except (ValueError, IndexError):
        return None
    return T["hot"] if t >= 85 else T["warn"] if t >= 70 else None


class Panel:
    def __init__(self, app, pcfg):
        self.app, self.cfg = app, pcfg
        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)
        self.win.withdraw()
        self.tiles = {}
        self._drag = self._press = None
        self._moved = False
        self.tag = f"SysMonPanel{id(self)}"  # общий тег привязок мыши для всех виджетов панели
        self.win.bind_class(self.tag, "<ButtonPress-1>", self._drag_start)
        self.win.bind_class(self.tag, "<B1-Motion>", self._drag_move)
        self.win.bind_class(self.tag, "<ButtonRelease-1>", self._drag_end)
        self.win.bind_class(self.tag, "<Button-3>", lambda e: self.app.show_panel_menu(self, e))
        self.build()
        self.win.deiconify()
        self.place()

    @property
    def pid(self):
        return self.cfg["id"]

    @property
    def columns(self):
        return max(1, int(self.cfg.get("columns", 2)))

    @property
    def has_menu_button(self):
        return self.app.cfg.get("menu_panel") == self.pid

    # ---------- построение ----------
    def build(self):
        app, s, win = self.app, self.app.s, self.win
        for c in win.winfo_children():
            c.destroy()
        self.tiles = {}
        win.configure(bg=T["bg"])
        win.attributes("-alpha", app.cfg["alpha"])
        win.attributes("-topmost", app.cfg["topmost"])
        self.gap = int(4 * s)
        body = self.body = tk.Frame(win, bg=T["bg"], padx=int(10 * s), pady=int(10 * s))
        body.pack(fill="both", expand=True)

        self.apps_button = None
        self.header = None
        if self.cfg.get("header", True) or self.has_menu_button:
            head = tk.Frame(body, bg=T["bg"])
            head.pack(fill="x", pady=(0, int(6 * s)), padx=self.gap)
            if self.has_menu_button:
                self._btn_imgs = (ImageTk.PhotoImage(render.apps_button_image(T, s)),
                                  ImageTk.PhotoImage(render.apps_button_image(T, s, hover=True)))
                b = self.apps_button = tk.Label(head, image=self._btn_imgs[0], bd=0, bg=T["bg"], cursor="hand2")
                b.pack(side="left", padx=(0, int(8 * s)))
                b.bind("<Enter>", lambda _e: b.config(image=self._btn_imgs[1]))
                b.bind("<Leave>", lambda _e: b.config(image=self._btn_imgs[0]))
                b.bind("<ButtonRelease-1>", self._on_apps_button, add="+")
            if self.cfg.get("header", True):
                self.header = tk.Label(head, bd=0, bg=T["bg"])
                self.header.pack(side="left", fill="x", expand=True)
        win.apps_button = self.apps_button  # для анимации панели приложений: летит от кнопки

        self.grid = tk.Frame(body, bg=T["bg"])
        self.grid.pack(fill="both", expand=True)
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
        for t in self.tiles.values():
            t.label.grid_forget()
        cols, g = self.columns, self.gap
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
                t.label.grid(row=r, column=0, columnspan=cols, padx=g, pady=g, sticky="w")
                r += 1
            else:
                t.label.grid(row=r, column=c, padx=g, pady=g)
                c += 1
                if c >= cols:
                    r, c = r + 1, 0
            t.draw()
        for key in [k for k in self.tiles if k not in self.cfg["tiles"]]:
            self.tiles.pop(key).label.destroy()
        self.render_header()
        self._bind(self.body)
        self.reshape()

    def render_header(self, uptime=""):
        if not self.header:
            return
        self.win.update_idletasks()
        s = self.app.s
        w = max(int(120 * s), self.grid.winfo_reqwidth() - 2 * self.gap -
                ((self.apps_button.winfo_reqwidth() + int(8 * s)) if self.apps_button else 0))
        title = socket.gethostname() if self.pid == "main" else self.cfg.get("name", "Панель")
        if self.app.cfg["locked"]:
            title += "  🔒"
        img = render.header_image(T, s, w, title, uptime or self._uptime_text())
        self._hdr = ImageTk.PhotoImage(img)
        self.header.config(image=self._hdr)

    def _uptime_text(self):
        return getattr(self.app, "uptime_text", "")

    def reshape(self):
        self.win.update_idletasks()
        self._shape_size = (self.win.winfo_reqwidth(), self.win.winfo_reqheight())
        round_region(self.win, int(18 * self.app.s), self.win.winfo_reqwidth(), self.win.winfo_reqheight())

    def update(self, metrics):
        for key, t in self.tiles.items():
            t.update(metrics.get(key))
        if self.header:
            self.render_header()
        self.win.update_idletasks()
        size = (self.win.winfo_reqwidth(), self.win.winfo_reqheight())
        if size != getattr(self, "_shape_size", None):  # карточка выросла — обновляем скругление окна
            self.reshape()

    # ---------- положение ----------
    def place(self):
        win, s = self.win, self.app.s
        win.update_idletasks()
        x, y = self.cfg.get("x"), self.cfg.get("y")
        fresh = x is None or y is None
        if fresh or not self._on_screen(x, y):
            idx = [p["id"] for p in self.app.cfg["panels"]].index(self.pid)
            x = win.winfo_screenwidth() - win.winfo_reqwidth() - int(24 * s) - idx * int(40 * s)
            y = int(48 * s) + idx * int(40 * s)
            if fresh:  # новая панель: место закрепляется сразу и не зависит от соседних панелей
                self.cfg["x"], self.cfg["y"] = x, y
        win.geometry(f"+{x}+{y}")
        self.reshape()

    def _on_screen(self, x, y):
        """Видна ли панель хотя бы частично на каком-нибудь мониторе. Панель, прижатая к краю
        (y = -1) или стоящая на втором мониторе, остаётся на месте; сбрасываем только ту, что
        целиком оказалась за пределами экранов (монитор отключили)."""
        m = int(40 * self.app.s)
        w, h = max(self.win.winfo_reqwidth(), 2 * m + 1), max(self.win.winfo_reqheight(), 2 * m + 1)
        # заметный кусок панели (не меньше m точек по каждой оси) должен попадать на монитор
        rect = wintypes.RECT(int(x) + m, int(y) + m, int(x) + w - m, int(y) + h - m)
        return bool(_user32.MonitorFromRect(ctypes.byref(rect), 0))  # MONITOR_DEFAULTTONULL

    def _bind(self, w):
        if self.tag not in w.bindtags():
            w.bindtags((self.tag,) + w.bindtags())
        for c in w.winfo_children():
            self._bind(c)

    def _drag_start(self, e):
        self._press = (e.x_root, e.y_root)
        self._moved = False
        self._drag = None if self.app.cfg["locked"] else (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def _drag_move(self, e):
        if self._press and abs(e.x_root - self._press[0]) + abs(e.y_root - self._press[1]) > 4:
            self._moved = True
        if self._drag and self._moved:
            self.win.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _drag_end(self, _e):
        if self._drag and self._moved:
            self.cfg["x"], self.cfg["y"] = self.win.winfo_x(), self.win.winfo_y()
            self.app.save()
        self._drag = None

    def _on_apps_button(self, _e):
        if not self._moved:
            self.app.launcher.toggle(anchor=self.win)

    def destroy(self):
        self.win.destroy()
