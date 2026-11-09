"""Панель приложений: вылетает от кнопки/курсора в центр монитора.

Два вида: «Сетка» (плитки с подписями) и «Соты» (круглые иконки в шестиугольной сетке, как
на Apple Watch: у центра крупные, к краям уменьшаются; сетку можно таскать мышью).
Размер окна меняется за любой край/угол, иконки всегда по центру.
"""
import ctypes
import math
import os
import tkinter as tk
from ctypes import wintypes
from tkinter import filedialog, messagebox, simpledialog

from PIL import Image, ImageDraw, ImageTk

from appscan import AppPicker
from icons import file_icon, window_snapshot
from theme import T, blend, font, round_region

user32 = ctypes.windll.user32
winmm = ctypes.windll.winmm
START_MENU = os.path.join(os.environ.get("ProgramData", "C:\\ProgramData"),
                          r"Microsoft\Windows\Start Menu\Programs")
OPEN_MS, CLOSE_MS, FRAME_MS = 300, 190, 7
ICON_MIN, ICON_MAX = 24, 96
COLS_MAX = 12
HEX_LEVELS = (1.0, 0.86, 0.72, 0.6, 0.49, 0.4, 0.32, 0.25)  # масштабы пузырей в «сотах»
HEX_DIRS = ((1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1))


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


user32.MonitorFromPoint.restype = ctypes.c_void_p
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]


def work_area(x, y):
    mon = user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(mon, ctypes.byref(mi))
    r = mi.rcWork
    return r.left, r.top, r.right, r.bottom


def ease_out(t):
    return 1 - (1 - t) ** 5      # мягкое торможение в конце


def ease_in(t):
    return t * t * t


def grid_cols(total):
    cols = math.ceil(math.sqrt(total * 1.6))
    return max(3, min(8, cols, max(total, 3)))


def hex_spiral(n):
    """Осевые координаты шестиугольной сетки по спирали от центра."""
    out = [(0, 0)]
    k = 1
    while len(out) < n:
        q, r = HEX_DIRS[4][0] * k, HEX_DIRS[4][1] * k
        for dq, dr in HEX_DIRS:
            for _ in range(k):
                out.append((q, r))
                q, r = q + dq, r + dr
        k += 1
    return out[:n]


class Launcher:
    def __init__(self, root, cfg, save, scale, hotkey_text):
        self.root, self.cfg, self.save, self.s = root, cfg, save, scale
        self.hotkey_text = hotkey_text
        self.top = self.ghost = None
        self.busy = False
        self._icons = {}       # (path, bg, size) -> PhotoImage
        self._bubbles = {}     # (path, bg, size, level) -> PhotoImage
        self._base = {}        # (path, bg) -> PIL.Image исходник для пузырей
        self._anim = None
        self._origin = None
        self._closing = False
        self._save_job = None
        self._reflow_job = None
        self.query = ""
        self.radius = int(14 * scale)
        self._resize = None
        self._rs_geo = self._rs_job = self._rs_settle = None
        self._cursor_saved = {}

    # ---------- настройки вида ----------
    @property
    def icon_px(self):
        return int(max(ICON_MIN, min(ICON_MAX, self.cfg.get("launcher_icon", 40))) * self.s)

    @property
    def layout(self):
        return self.cfg.get("launcher_layout", "grid")

    def tile_size(self):
        ic = self.icon_px
        return max(int(76 * self.s), ic + int(50 * self.s)), ic + int(52 * self.s)

    def cols_for(self, total):
        fixed = int(self.cfg.get("launcher_cols", 0) or 0)
        return max(1, min(COLS_MAX, fixed)) if fixed else grid_cols(max(total, 1))

    def set_icon_size(self, px, persist=True):
        self.cfg["launcher_icon"] = int(max(ICON_MIN, min(ICON_MAX, px)))
        self._bubbles.clear()
        self._rebuild()
        if persist:
            self._save_later()

    def set_cols(self, n, persist=True):
        self.cfg["launcher_cols"] = int(max(0, min(COLS_MAX, n)))
        if persist:
            self._save_later()

    def set_layout(self, mode):
        self.cfg["launcher_layout"] = mode
        self._save_later()
        if self.visible() and not self._closing:
            self._build_content()

    def reset_size(self):
        self.cfg["launcher_w"] = self.cfg["launcher_h"] = 0
        self._save_later()

    def _save_later(self):
        if self._save_job:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(400, lambda: self.save(self.cfg))

    # ---------- показ / скрытие ----------
    def visible(self):
        return self.top is not None and self.top.winfo_exists()

    def toggle(self, anchor=None):
        if self.visible() and not self._closing:
            self.hide()
        else:
            self.show(anchor)

    def _origin_point(self, anchor):
        if anchor is not None and anchor.winfo_viewable():
            btn = getattr(anchor, "apps_button", None)
            w = btn if btn is not None and btn.winfo_viewable() else anchor
            return w.winfo_rootx() + w.winfo_width() // 2, w.winfo_rooty() + w.winfo_height() // 2
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def show(self, anchor=None, animate=True):
        self._destroy()
        self.query = ""
        self._origin = self._origin_point(anchor)
        top = self.top = tk.Toplevel(self.root, bg=T["bg"])
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.withdraw()  # покажем, когда долетит «тень» (позиция скрытого окна не применяется)
        self._build()
        top.bind("<Escape>", lambda _e: self.hide())
        top.bind("<KeyPress>", self._on_key)
        top.bind("<Control-MouseWheel>", self._on_ctrl_wheel)
        top.bind("<FocusOut>", lambda _e: top.after(150, self._check_focus))
        self._rect = self._target()
        if animate:
            self._fly(opening=True)
        else:
            self._reveal()

    def _reveal(self):
        top = self.top
        x, y, w, h = self._rect
        top.attributes("-alpha", self.cfg["launcher_alpha"])
        top.deiconify()
        top.geometry(f"{w}x{h}+{x}+{y}")
        round_region(top, self.radius, w, h)
        top.update_idletasks()
        self._reflow(force=True)
        self._focus()

    def hide(self):
        if not self.visible() or self._closing:
            return
        self._closing = True
        x, y = self.top.winfo_x(), self.top.winfo_y()
        self._rect = (x, y, self.top.winfo_width(), self.top.winfo_height())
        self.top.withdraw()
        self._fly(opening=False)

    def _destroy(self):
        if self._anim:
            try:
                self.root.after_cancel(self._anim)
            except Exception:
                pass
            self._anim = None
        for w in (self.ghost, self.top):
            if w is not None and w.winfo_exists():
                w.destroy()
        self.ghost = self.top = None
        self._closing = False

    # ---------- анимация: летит лёгкая «тень», потом проявляется панель ----------
    def _fly(self, opening):
        x1, y1, w1, h1 = self._rect
        ox, oy = self._origin
        w0, h0 = max(36, int(w1 * 0.12)), max(28, int(h1 * 0.12))
        x0, y0 = ox - w0 / 2, oy - h0 / 2
        alpha = self.cfg["launcher_alpha"]
        dur = OPEN_MS if opening else CLOSE_MS

        g = self.ghost = tk.Toplevel(self.root, bg=T["bg"])
        g.overrideredirect(True)
        g.attributes("-topmost", True)
        g.attributes("-alpha", 0.0)
        tk.Frame(g, bg=blend(T["accent"], T["bg"], 0.10)).place(relx=0, rely=0, relwidth=1,
                                                                height=max(2, int(3 * self.s)))
        start = [None]
        winmm.timeBeginPeriod(1)  # точный таймер на время анимации: кадры ~7 мс вместо 15.6 мс

        def frame():
            if g is not self.ghost or not g.winfo_exists():
                winmm.timeEndPeriod(1)
                return
            now = self.root.tk.call("clock", "milliseconds")
            if start[0] is None:
                start[0] = now
            t = min(1.0, (now - start[0]) / dur)
            k = ease_out(t) if opening else 1 - ease_in(t)
            x, y = x0 + (x1 - x0) * k, y0 + (y1 - y0) * k
            w, h = w0 + (w1 - w0) * k, h0 + (h1 - h0) * k
            g.geometry(f"{int(w)}x{int(h)}+{int(x)}+{int(y)}")
            round_region(g, self.radius * (0.6 + 0.4 * k), w, h)
            g.attributes("-alpha", alpha * min(1.0, 0.25 + 0.75 * k))
            if t < 1:
                self._anim = self.root.after(FRAME_MS, frame)
                return
            self._anim = None
            winmm.timeEndPeriod(1)
            if opening:
                self._reveal()
                self.root.after(30, lambda: (g.destroy() if g.winfo_exists() else None))
                self.ghost = None
            else:
                self._destroy()

        frame()

    def prewarm(self, i=0):
        """Заранее готовим иконки в фоне, чтобы первое открытие было мгновенным."""
        apps = self.cfg["apps"]
        if i < len(apps):
            try:
                self._icon(apps[i])
            except Exception:
                pass
            self.root.after(40, lambda: self.prewarm(i + 1))

    # ---------- построение ----------
    def _build(self):
        s, top = self.s, self.top
        for c in top.winfo_children():
            c.destroy()
        pad = self.pad = int(16 * s)
        body = self.body = tk.Frame(top, bg=T["bg"])
        body.place(relx=0, rely=0, relwidth=1, relheight=1)

        head = tk.Frame(body, bg=T["bg"])
        head.pack(fill="x", padx=pad, pady=(pad, int(8 * s)))
        tk.Label(head, text="Приложения", bg=T["bg"], fg=T["fg"], font=font(13, True)).pack(side="left")
        self.search_lbl = tk.Label(head, text="", bg=T["bg"], fg=T["accent"], font=font(10))
        self.search_lbl.pack(side="left", padx=int(12 * s))
        close = tk.Label(head, text="✕", bg=T["bg"], fg=T["muted"], font=font(11), cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda _e: self.hide())
        mode = tk.Label(head, text="⬡ Соты" if self.layout == "grid" else "▦ Сетка", bg=T["panel"], fg=T["fg"],
                        font=font(9), padx=int(8 * s), pady=int(2 * s), cursor="hand2")
        mode.pack(side="right", padx=int(10 * s))
        mode.bind("<Button-1>", lambda _e: self.set_layout("honey" if self.layout == "grid" else "grid"))
        self.mode_btn = mode

        self.footer = tk.Label(body, bg=T["bg"], fg=T["muted"], font=font(8), anchor="w")
        self.footer.pack(side="bottom", fill="x", padx=pad, pady=(int(6 * s), pad))
        self.cv = tk.Canvas(body, bg=T["bg"], highlightthickness=0, bd=0)
        self.cv.pack(fill="both", expand=True, padx=pad)
        self.cv.bind("<Configure>", lambda _e: self._schedule_reflow())

        # уголок изменения размера (тянуть можно и за любой край)
        gs = int(16 * s)
        grip = tk.Canvas(body, width=gs, height=gs, bg=T["bg"], highlightthickness=0, cursor="size_nw_se")
        for i in range(3):
            for j in range(i + 1):
                cx, cy = gs - 4 * s - (i - j) * 4 * s, gs - 4 * s - j * 4 * s
                r = 1.1 * s
                grip.create_oval(cx - r, cy - r, cx + r, cy + r, fill=T["muted"], outline="")
        grip.place(relx=1.0, rely=1.0, anchor="se", x=-int(5 * s), y=-int(5 * s))
        self.grip = grip
        self._build_content()

    def _install_resize(self, w):
        tag = "LauncherResize"
        if tag not in w.bindtags():
            w.bindtags((tag,) + w.bindtags())
        for c in w.winfo_children():
            self._install_resize(c)
        self.top.bind_class(tag, "<Motion>", self._rs_motion)
        self.top.bind_class(tag, "<ButtonPress-1>", self._rs_press)
        self.top.bind_class(tag, "<B1-Motion>", self._rs_drag)
        self.top.bind_class(tag, "<ButtonRelease-1>", self._rs_release)

    def _build_content(self):
        self._snap_item = None
        self.cv.delete("all")
        for c in self.cv.winfo_children():
            c.destroy()
        self._cols = None
        self.mode_btn.config(text="⬡ Соты" if self.layout == "grid" else "▦ Сетка")
        if self.layout == "honey":
            self._honey_build()
            hint = "тяни соты мышью · колесо — прокрутка · печатай для поиска · Ctrl+колесо — размер"
        else:
            self._grid_build()
            hint = "1–9 — запуск · печатай для поиска · Ctrl+колесо — размер значков · тяни край — размер окна"
        self.footer.config(text=f"{hint} · {self.hotkey_text()}")
        self.search_lbl.config(text=f"🔍 {self.query}" if self.query else "")
        self._install_resize(self.top)
        self._reflow(force=True)

    def _visible_apps(self):
        q = self.query.lower()
        return [(i, a) for i, a in enumerate(self.cfg["apps"]) if not q or q in a["name"].lower()]

    def _icon(self, app):
        size = self.icon_px
        key = (app["path"], T["panel"], size)
        if key not in self._icons:
            img = file_icon(app["path"], size, T["panel"], app["name"], T["accent"])
            self._icons[key] = ImageTk.PhotoImage(img)
        return self._icons[key]

    # ---------- «Сетка» ----------
    def _grid_build(self):
        s = self.s
        self.grid = tk.Frame(self.cv, bg=T["bg"])
        self._grid_item = self.cv.create_window(0, 0, window=self.grid, anchor="nw")
        self._tiles = []
        items = self._visible_apps()
        for n, (i, app) in enumerate(items):
            self._tiles.append(self._tile(i, app, n))
        if not self.query:
            self._tiles.append(self._add_tile())
        elif not items:
            self._tiles.append(tk.Label(self.grid, text="Ничего не найдено", bg=T["bg"], fg=T["muted"],
                                        font=font(10), pady=int(20 * s)))
        self.cv.bind("<MouseWheel>", lambda e: self.cv.yview_scroll(int(-e.delta / 120), "units"))
        self.cv.configure(yscrollincrement=int(20 * s))

    def _grid_reflow(self, force):
        cw, ch = self.cv.winfo_width(), self.cv.winfo_height()
        tw, th = self.tile_size()
        g = self.gap = int(4 * self.s)
        cols = max(1, cw // (tw + 2 * g))
        if force or cols != self._cols:  # перекладываем только если поменялось число колонок
            self._cols = cols
            for n, t in enumerate(self._tiles):
                t.grid(row=n // cols, column=n % cols, padx=g, pady=g)
            self.grid.update_idletasks()
        gw, gh = self.grid.winfo_reqwidth(), self.grid.winfo_reqheight()
        x, y = max(0, (cw - gw) // 2), max(0, (ch - gh) // 2)
        self.cv.coords(self._grid_item, x, y)
        self.cv.configure(scrollregion=(0, 0, cw, max(ch, gh)))

    def _make_tile(self, image, text, badge=None, image_text=None):
        s = self.s
        tw, th = self.tile_size()
        f = tk.Frame(self.grid, bg=T["panel"], width=tw, height=th, cursor="hand2")
        f.pack_propagate(False)
        if image is not None:
            ic = tk.Label(f, image=image, bg=T["panel"])
        else:
            ic = tk.Label(f, text=image_text, bg=T["panel"], fg=T["accent"],
                          font=font(max(14, int(self.icon_px / s * 0.55))))
        ic.pack(pady=(int(10 * s), int(4 * s)))
        name = tk.Label(f, text=text, bg=T["panel"], fg=T["fg"], font=font(9 if self.icon_px / s >= 56 else 8),
                        wraplength=tw - int(8 * s), justify="center")
        name.pack()
        widgets = [f, ic, name]
        if badge:
            bl = tk.Label(f, text=badge, bg=T["panel"], fg=T["muted"], font=font(7))
            bl.place(x=int(5 * s), y=int(3 * s))
            widgets.append(bl)

        def hover(on):
            for w in widgets:
                w.config(bg=T["hover"] if on else T["panel"])

        for w in widgets:
            w.bind("<Enter>", lambda _e: hover(True))
            w.bind("<Leave>", lambda _e: hover(False))
            w.bind("<MouseWheel>", lambda e: self.cv.yview_scroll(int(-e.delta / 120), "units"))
        return f, widgets

    def _tile(self, i, app, n):
        f, widgets = self._make_tile(self._icon(app), app["name"], badge=str(n + 1) if n < 9 else None)
        for w in widgets:
            w.bind("<ButtonRelease-1>", lambda _e, a=app: self.launch(a))
            w.bind("<Button-3>", lambda e, idx=i: self._tile_menu(e, idx))
        return f

    def _add_tile(self):
        f, widgets = self._make_tile(None, "Добавить", image_text="+")
        for w in widgets:
            w.bind("<ButtonRelease-1>", self._add_menu)
        return f

    # ---------- «Соты» ----------
    def _bubble(self, app, level):
        """Круглая иконка-пузырь нужного уровня масштаба (сглаженная, на фоне панели)."""
        base = int(self.icon_px * 1.5)
        size = max(8, int(base * HEX_LEVELS[level]))
        key = (app["path"] if app else "+", T["bg"], base, level)
        if key in self._bubbles:
            return self._bubbles[key]
        SS = 3
        S = size * SS
        img = Image.new("RGB", (S, S), T["bg"])
        d = ImageDraw.Draw(img)
        d.ellipse((0, 0, S - 1, S - 1), fill=T["hover"] if app else T["panel"])
        if app:
            bkey = (app["path"], T["hover"])
            if bkey not in self._base:  # исходник иконки один, уровни — уменьшением
                self._base[bkey] = file_icon(app["path"], int(base * 0.6) * 2, T["hover"], app["name"], T["accent"])
            ic = self._base[bkey].resize((int(S * 0.6), int(S * 0.6)), Image.LANCZOS)
            img.paste(ic, ((S - ic.width) // 2, (S - ic.height) // 2))
        else:
            w = max(2, S // 14)
            c = S // 2
            d.rounded_rectangle((c - S * 0.18, c - w / 2, c + S * 0.18, c + w / 2), w // 2, fill=T["accent"])
            d.rounded_rectangle((c - w / 2, c - S * 0.18, c + w / 2, c + S * 0.18), w // 2, fill=T["accent"])
        img = img.resize((size, size), Image.LANCZOS)
        photo = self._bubbles[key] = ImageTk.PhotoImage(img)
        return photo

    def _honey_build(self):
        items = self._visible_apps()
        entries = [(i, a) for i, a in items] + ([] if self.query else [(None, None)])
        coords = hex_spiral(len(entries))
        D = self.icon_px * 1.5 * 1.08
        self._honey = []
        for (i, app), (q, r) in zip(entries, coords):
            x, y = D * (q + r / 2), D * (r * math.sqrt(3) / 2)
            item = self.cv.create_image(0, 0, image=self._bubble(app, 0))
            self._honey.append({"i": i, "app": app, "x": x, "y": y, "item": item, "level": 0})
            if app is not None:
                self.cv.tag_bind(item, "<Enter>", lambda _e, a=app: self._honey_name(a["name"]))
                self.cv.tag_bind(item, "<Button-3>", lambda e, idx=i: self._tile_menu(e, idx))
            else:
                self.cv.tag_bind(item, "<Enter>", lambda _e: self._honey_name("Добавить"))
            self.cv.tag_bind(item, "<Leave>", lambda _e: self._honey_name(""))
        if not entries:
            self.cv.create_text(0, 0, text="Ничего не найдено", fill=T["muted"], font=font(10), tags="empty")
        self._name = self.cv.create_text(0, 0, text="", fill=T["fg"], font=font(11, True), anchor="s")
        self._pan = [0.0, 0.0]
        self._vel = [0.0, 0.0]
        self._press = None
        self._extent = max([math.hypot(h["x"], h["y"]) for h in self._honey] + [1])
        self.cv.bind("<ButtonPress-1>", self._honey_press)
        self.cv.bind("<B1-Motion>", self._honey_drag)
        self.cv.bind("<ButtonRelease-1>", self._honey_release)
        self.cv.bind("<MouseWheel>", lambda e: self._honey_pan(0, e.delta / 120 * self.icon_px))
        self.cv.bind("<Shift-MouseWheel>", lambda e: self._honey_pan(e.delta / 120 * self.icon_px, 0))

    def _honey_name(self, text):
        self.cv.itemconfig(self._name, text=text)

    def _honey_draw(self):
        cw, ch = self.cv.winfo_width(), self.cv.winfo_height()
        cx, cy = cw / 2, ch / 2
        R = max(60.0, min(cw, ch) / 2)
        px, py = self._pan
        bubble = self.icon_px * 1.5
        for h in self._honey:
            x, y = h["x"] + px, h["y"] + py
            dist = math.hypot(x, y) / R
            scale = 1.0 if dist < 0.55 else max(HEX_LEVELS[-1], 1.0 - (dist - 0.55) * 0.8)
            pull = 0.55 + 0.45 * scale        # к краям пузыри не только мельче, но и ближе друг к другу
            sx, sy = cx + x * pull, cy + y * pull
            visible = -bubble < sx < cw + bubble and -bubble < sy < ch + bubble
            self.cv.itemconfig(h["item"], state="normal" if visible else "hidden")
            if not visible:
                continue
            level = min(range(len(HEX_LEVELS)), key=lambda k: abs(HEX_LEVELS[k] - scale))
            if level != h["level"]:
                h["level"] = level
                self.cv.itemconfig(h["item"], image=self._bubble(h["app"], level))
            self.cv.coords(h["item"], sx, sy)
        self.cv.coords(self._name, cx, ch - 2)
        if self.cv.find_withtag("empty"):
            self.cv.coords("empty", cx, cy)

    def _honey_pan(self, dx, dy):
        # дальше края сот не уводим: в центре всегда остаются иконки
        cw, ch = self.cv.winfo_width(), self.cv.winfo_height()
        lim = max(0.0, self._extent - 0.35 * min(cw, ch))
        self._pan[0] = max(-lim, min(lim, self._pan[0] + dx))
        self._pan[1] = max(-lim, min(lim, self._pan[1] + dy))
        self._honey_draw()

    def _honey_press(self, e):
        self._press = (e.x, e.y, e.x, e.y, self.root.tk.call("clock", "milliseconds"))
        self._dragging = False
        self._vel = [0.0, 0.0]
        if self._anim_honey():
            self.root.after_cancel(self._inertia)
            self._inertia = None

    def _anim_honey(self):
        return getattr(self, "_inertia", None) is not None

    def _honey_drag(self, e):
        if not self._press:
            return
        x0, y0, lx, ly, lt = self._press
        if abs(e.x - x0) + abs(e.y - y0) > 5:
            self._dragging = True
        if self._dragging:
            now = self.root.tk.call("clock", "milliseconds")
            dt = max(1, now - lt)
            self._vel = [(e.x - lx) / dt * 16, (e.y - ly) / dt * 16]
            self._honey_pan(e.x - lx, e.y - ly)
            self._press = (x0, y0, e.x, e.y, now)

    def _honey_release(self, e):
        if not self._press:
            return
        if not self._dragging:  # клик — по какому пузырю?
            hit = self.cv.find_overlapping(e.x - 1, e.y - 1, e.x + 1, e.y + 1)
            for h in self._honey:
                if h["item"] in hit:
                    if h["app"] is None:
                        self._add_menu(e)
                    else:
                        self.launch(h["app"])
                    break
        elif math.hypot(*self._vel) > 1.5:
            self._coast()
        self._press = None

    def _coast(self):
        """Инерция после броска сот."""
        self._vel = [v * 0.93 for v in self._vel]
        if math.hypot(*self._vel) < 0.4 or not self.visible():
            self._inertia = None
            return
        self._honey_pan(*self._vel)
        self._inertia = self.root.after(8, self._coast)

    # ---------- геометрия и изменение размера ----------
    def _natural_size(self):
        s = self.s
        chrome_w = 2 * self.pad
        chrome_h = int(16 * s) + int(30 * s) + int(30 * s) + int(16 * s)
        total = len(self._visible_apps()) + (0 if self.query else 1)
        if self.layout == "honey":
            rings = 0
            while 1 + 3 * rings * (rings + 1) < total:
                rings += 1
            d = self.icon_px * 1.5 * 1.08 * (2 * rings + 1) + int(20 * s)
            return int(max(d, 320 * s) + chrome_w), int(max(d * 0.95, 260 * s) + chrome_h)
        tw, th = self.tile_size()
        g = int(4 * s)
        cols = self.cols_for(total)
        rows = max(1, math.ceil(total / cols))
        return cols * (tw + 2 * g) + chrome_w, rows * (th + 2 * g) + chrome_h

    def _target(self):
        """Прямоугольник панели в центре монитора, где источник (виджет или курсор)."""
        l, t, r, b = work_area(*self._origin)
        w, h = int(self.cfg.get("launcher_w") or 0), int(self.cfg.get("launcher_h") or 0)
        if not (w and h):
            w, h = self._natural_size()
        w, h = min(w, r - l - 16), min(h, b - t - 16)
        return (l + r) // 2 - w // 2, (t + b) // 2 - h // 2, w, h

    def _schedule_reflow(self):
        if self._reflow_job is None and self.visible():
            self._reflow_job = self.top.after_idle(self._reflow)

    def _reflow(self, force=False):
        self._reflow_job = None
        if not self.visible():
            return
        if self.layout == "honey":
            self._honey_draw()
        elif getattr(self, "_snap_item", None):
            self.cv.coords(self._snap_item, self.cv.winfo_width() / 2, self.cv.winfo_height() / 2)
        else:
            self._grid_reflow(force)

    def _relayout(self):
        """После поиска/правок: если размер не задан вручную — подогнать под содержимое."""
        if not self.visible() or self._closing:
            return
        if not (self.cfg.get("launcher_w") and self.cfg.get("launcher_h")):
            x, y, w, h = self._rect = self._target()
            self.top.geometry(f"{w}x{h}+{x}+{y}")
            round_region(self.top, self.radius, w, h)
        self._reflow(force=True)

    def _zone(self, e):
        top = self.top
        x, y = e.x_root - top.winfo_rootx(), e.y_root - top.winfo_rooty()
        w, h = top.winfo_width(), top.winfo_height()
        m = int(8 * self.s)
        if e.widget is self.grip:
            return "se"
        z = ("n" if y < m else "s" if y > h - m else "") + ("w" if x < m else "e" if x > w - m else "")
        return z

    CURSORS = {"n": "size_ns", "s": "size_ns", "e": "size_we", "w": "size_we",
               "nw": "size_nw_se", "se": "size_nw_se", "ne": "size_ne_sw", "sw": "size_ne_sw"}

    def _rs_motion(self, e):
        if self._resize:
            return
        z = self._zone(e)
        w = e.widget
        if z:
            if w not in self._cursor_saved:
                self._cursor_saved[w] = w.cget("cursor")
            w.config(cursor=self.CURSORS[z])
        elif w in self._cursor_saved:
            w.config(cursor=self._cursor_saved.pop(w))

    def _rs_press(self, e):
        z = self._zone(e)
        if not z:
            return None
        t = self.top
        self._resize = (z, e.x_root, e.y_root, t.winfo_x(), t.winfo_y(), t.winfo_width(), t.winfo_height())
        self._rs_geo = self._rs_job = self._rs_settle = None
        # на время растягивания убираем скруглённую форму окна — её пересчёт дорогой
        user32.SetWindowRgn(ctypes.c_void_p(user32.GetParent(t.winfo_id())), None, True)
        return "break"

    def _rs_drag(self, e):
        if not self._resize:
            return None
        z, x0, y0, X, Y, W, H = self._resize
        dx, dy = e.x_root - x0, e.y_root - y0
        mw, mh = int(300 * self.s), int(230 * self.s)
        x, y, w, h = X, Y, W, H
        if "e" in z:
            w = max(mw, W + dx)
        if "s" in z:
            h = max(mh, H + dy)
        if "w" in z:
            w = max(mw, W - dx)
            x = X + W - w
        if "n" in z:
            h = max(mh, H - dy)
            y = Y + H - h
        self._rs_geo = (w, h, x, y)
        if self._rs_job is None:  # склеиваем события мыши: не чаще одного раза за кадр
            self._rs_job = self.top.after(8, self._rs_apply)
        return "break"

    def _snap_begin(self):
        """Плитки → одна картинка-снимок: окно тянется без перерисовки десятков виджетов."""
        if self.layout != "grid" or getattr(self, "_snap_item", None):
            return
        img = window_snapshot(self.top)
        if img is None:
            return
        cx = self.cv.winfo_rootx() - self.top.winfo_rootx()
        cy = self.cv.winfo_rooty() - self.top.winfo_rooty()
        cw, ch = self.cv.winfo_width(), self.cv.winfo_height()
        self._snap_photo = ImageTk.PhotoImage(img.crop((cx, cy, cx + cw, cy + ch)))
        self.cv.itemconfig(self._grid_item, state="hidden")
        self._snap_item = self.cv.create_image(cw / 2, ch / 2, image=self._snap_photo)

    def _snap_end(self):
        if getattr(self, "_snap_item", None):
            self.cv.delete(self._snap_item)
            self._snap_item = None
            self.cv.itemconfig(self._grid_item, state="normal")

    def _settle(self):
        self._rs_settle = None
        self._snap_end()
        self._reflow()

    def _rs_apply(self):
        self._rs_job = None
        if not self.visible() or not self._rs_geo:
            return
        self._snap_begin()
        w, h, x, y = self._rs_geo
        self.top.geometry(f"{w}x{h}+{x}+{y}")
        if self.layout == "honey":
            self._reflow()  # соты дешёвые — двигаем сразу
        else:
            if self._rs_settle:
                self.top.after_cancel(self._rs_settle)
            self._rs_settle = self.top.after(90, self._settle)  # раскладка — когда рука замерла

    def _rs_release(self, _e):
        if not self._resize:
            return None
        self._resize = None
        if self._rs_job:
            self.top.after_cancel(self._rs_job)
            self._rs_apply()
        if self._rs_settle:
            self.top.after_cancel(self._rs_settle)
            self._rs_settle = None
        self._snap_end()
        self.top.update_idletasks()
        w, h = self.top.winfo_width(), self.top.winfo_height()
        round_region(self.top, self.radius, w, h)
        self._reflow()
        self.cfg["launcher_w"], self.cfg["launcher_h"] = w, h
        self._rect = (self.top.winfo_x(), self.top.winfo_y(), w, h)
        self._save_later()
        return "break"

    def _on_ctrl_wheel(self, e):
        step = 4 if e.delta > 0 else -4
        self.set_icon_size(self.cfg.get("launcher_icon", 40) + step)
        return "break"

    def _focus(self):
        try:
            user32.SetForegroundWindow(user32.GetParent(self.top.winfo_id()))
        except Exception:
            pass
        self.top.focus_force()

    def _check_focus(self):
        if self.busy or not self.visible() or self._closing:
            return
        try:
            f = self.top.focus_get()
        except Exception:
            f = None
        if f is None or not str(f).startswith(str(self.top)):
            self.hide()

    # ---------- клавиатура ----------
    def _on_key(self, e):
        if e.keysym == "BackSpace":
            self.query = self.query[:-1]
        elif e.keysym in ("Return", "KP_Enter"):
            items = self._visible_apps()
            if items:
                self.launch(items[0][1])
            return
        elif e.char and e.char.isdigit() and e.char != "0" and not self.query:
            items = self._visible_apps()
            idx = int(e.char) - 1
            if idx < len(items):
                self.launch(items[idx][1])
            return
        elif e.char and e.char.isprintable() and not (e.state & 0x4):
            self.query += e.char
        else:
            return
        self._build_content()
        self._relayout()

    # ---------- действия ----------
    def launch(self, app):
        path = app["path"]
        self.hide()
        try:
            if os.path.isfile(path):
                os.startfile(path, cwd=os.path.dirname(path))
            else:
                os.startfile(path)
        except OSError as ex:
            messagebox.showerror("Не удалось запустить", f"{app['name']}\n{path}\n\n{ex}")

    def _modal(self, fn):
        self.busy = True
        try:
            return fn()
        finally:
            self.busy = False
            if self.visible():
                self._focus()

    def _rebuild(self):
        if self.visible() and not self._closing:
            self._build_content()
            self._relayout()

    def _add_menu(self, e):
        m = tk.Menu(self.top, tearoff=0)
        m.add_command(label="Найти установленные…", command=self.pick_installed)
        m.add_command(label="Выбрать файл (exe, ярлык)…", command=self.add_file)
        self._modal(lambda: (m.tk_popup(e.x_root, e.y_root), m.grab_release()))

    def add_apps(self, apps):
        known = {a["path"].lower() for a in self.cfg["apps"]}
        self.cfg["apps"].extend(a for a in apps if a["path"].lower() not in known)
        self.save(self.cfg)
        self.root.after(200, self.prewarm)
        self._rebuild()

    def pick_installed(self, master=None):
        self.busy = True
        picker = AppPicker(master or self.top or self.root, self.s,
                           [a["path"] for a in self.cfg["apps"]], self.add_apps)
        picker.win.bind("<Destroy>", lambda _e: setattr(self, "busy", False), add="+")
        return picker

    def add_file(self, master=None):
        path = self._modal(lambda: filedialog.askopenfilename(
            parent=master or self.top or self.root, title="Добавить приложение",
            initialdir=START_MENU if os.path.isdir(START_MENU) else None,
            filetypes=[("Программы и ярлыки", "*.exe *.lnk *.url *.bat *.cmd"),
                       ("Все файлы", "*.*")]))
        if path:
            path = os.path.normpath(path)
            self.add_apps([{"name": os.path.splitext(os.path.basename(path))[0], "path": path}])

    def _tile_menu(self, e, idx):
        apps = self.cfg["apps"]
        m = tk.Menu(self.top, tearoff=0)
        m.add_command(label="Переименовать", command=lambda: self.rename(idx))
        if os.path.exists(apps[idx]["path"]):
            m.add_command(label="Открыть расположение",
                          command=lambda: os.startfile(os.path.dirname(apps[idx]["path"])))
        m.add_separator()
        m.add_command(label="← Сдвинуть влево", state="normal" if idx > 0 else "disabled",
                      command=lambda: self.move(idx, -1))
        m.add_command(label="Сдвинуть вправо →",
                      state="normal" if idx < len(apps) - 1 else "disabled",
                      command=lambda: self.move(idx, 1))
        m.add_separator()
        m.add_command(label="Удалить", command=lambda: self.remove(idx))
        self._modal(lambda: (m.tk_popup(e.x_root, e.y_root), m.grab_release()))

    def rename(self, idx, master=None):
        app = self.cfg["apps"][idx]
        name = self._modal(lambda: simpledialog.askstring(
            "Переименовать", "Название:", initialvalue=app["name"], parent=master or self.top))
        if name and name.strip():
            app["name"] = name.strip()
            self.save(self.cfg)
            self._rebuild()

    def move(self, idx, d):
        apps = self.cfg["apps"]
        apps[idx], apps[idx + d] = apps[idx + d], apps[idx]
        self.save(self.cfg)
        self._rebuild()

    def remove(self, idx):
        del self.cfg["apps"][idx]
        self.save(self.cfg)
        self._rebuild()
