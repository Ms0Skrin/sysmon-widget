"""Всплывающая панель приложений: вылетает от кнопки/курсора в центр экрана."""
import ctypes
import math
import os
import tkinter as tk
from ctypes import wintypes
from tkinter import filedialog, messagebox, simpledialog

from PIL import ImageTk

from appscan import AppPicker
from icons import file_icon
from theme import T, blend, font, round_region

user32 = ctypes.windll.user32
winmm = ctypes.windll.winmm
START_MENU = os.path.join(os.environ.get("ProgramData", "C:\\ProgramData"),
                          r"Microsoft\Windows\Start Menu\Programs")
OPEN_MS, CLOSE_MS, FRAME_MS = 300, 190, 7


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


ICON_MIN, ICON_MAX = 24, 96
COLS_MAX = 12


class Launcher:
    def __init__(self, root, cfg, save, scale, hotkey_text):
        self.root, self.cfg, self.save, self.s = root, cfg, save, scale
        self.hotkey_text = hotkey_text
        self.top = None
        self.ghost = None
        self.busy = False
        self._icons = {}      # (path, bg, size) -> PhotoImage
        self._anim = None
        self._origin = None
        self._closing = False
        self._save_job = None
        self.query = ""
        self.radius = int(14 * scale)

    # ---------- размеры (меняются пользователем) ----------
    @property
    def icon_px(self):
        return int(max(ICON_MIN, min(ICON_MAX, self.cfg.get("launcher_icon", 40))) * self.s)

    def tile_size(self):
        ic = self.icon_px
        return max(int(76 * self.s), ic + int(50 * self.s)), ic + int(52 * self.s)

    def cols_for(self, total):
        fixed = int(self.cfg.get("launcher_cols", 0) or 0)
        return max(1, min(COLS_MAX, fixed)) if fixed else grid_cols(max(total, 1))

    def set_icon_size(self, px, persist=True):
        self.cfg["launcher_icon"] = int(max(ICON_MIN, min(ICON_MAX, px)))
        self._rebuild()
        if persist:
            self._save_later()

    def set_cols(self, n, persist=True):
        self.cfg["launcher_cols"] = int(max(0, min(COLS_MAX, n)))
        self._rebuild()
        if persist:
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
        self.body = tk.Frame(top, bg=T["bg"])
        self._build()
        top.bind("<Escape>", lambda _e: self.hide())
        top.bind("<KeyPress>", self._on_key)
        top.bind("<Control-MouseWheel>", self._on_ctrl_wheel)
        top.bind("<FocusOut>", lambda _e: top.after(150, self._check_focus))
        x, y, w, h = self._rect = self._target()
        self.body.place(relx=0.5, rely=0.5, anchor="center", width=w, height=h)
        if animate:
            self._fly(opening=True)
        else:
            self._reveal()

    def _reveal(self):
        """Показать настоящую панель на её месте."""
        top = self.top
        x, y, w, h = self._rect
        top.attributes("-alpha", self.cfg["launcher_alpha"])
        top.deiconify()
        top.geometry(f"{w}x{h}+{x}+{y}")
        round_region(top, self.radius, w, h)
        top.update_idletasks()
        self._focus()

    def hide(self):
        if not self.visible() or self._closing:
            return
        self._closing = True
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
        x1, y1, w1, h1 = self._rect   # окно ещё не показано — winfo_x/y тут вернули бы 0
        ox, oy = self._origin
        w0, h0 = max(36, int(w1 * 0.12)), max(28, int(h1 * 0.12))
        x0, y0 = ox - w0 / 2, oy - h0 / 2
        alpha = self.cfg["launcher_alpha"]
        dur = OPEN_MS if opening else CLOSE_MS

        g = self.ghost = tk.Toplevel(self.root, bg=T["bg"])
        g.overrideredirect(True)
        g.attributes("-topmost", True)
        g.attributes("-alpha", 0.0)
        tk.Frame(g, bg=blend(T["accent"], T["bg"], 0.10)).place(relx=0, rely=0, relwidth=1, height=max(2, int(3 * self.s)))
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
            if opening:  # тень на месте — показываем настоящую панель и убираем тень
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
        s, b = self.s, self.body
        for c in b.winfo_children():
            c.destroy()
        pad = self.pad = int(16 * s)
        head = tk.Frame(b, bg=T["bg"])
        head.pack(fill="x", padx=pad, pady=(pad, int(10 * s)))
        tk.Label(head, text="Приложения", bg=T["bg"], fg=T["fg"], font=font(13, True)).pack(side="left")
        self.search_lbl = tk.Label(head, text="", bg=T["bg"], fg=T["accent"], font=font(10))
        self.search_lbl.pack(side="left", padx=int(12 * s))
        close = tk.Label(head, text="✕", bg=T["bg"], fg=T["muted"], font=font(11), cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda _e: self.hide())

        self.grid = tk.Frame(b, bg=T["bg"])
        self.grid.pack(padx=pad)
        self._fill_grid()

        hint = (f"1–9 — запуск · печатай для поиска · Ctrl+колесо — размер значков · "
                f"тяни уголок — колонки · {self.hotkey_text()}")
        tk.Label(b, text=hint, bg=T["bg"], fg=T["muted"], font=font(8)).pack(
            anchor="w", padx=pad, pady=(int(10 * s), pad))

        # уголок для изменения размера (число колонок)
        gs = int(16 * s)
        grip = tk.Canvas(b, width=gs, height=gs, bg=T["bg"], highlightthickness=0, cursor="size_nw_se")
        for i in range(3):
            for j in range(i + 1):
                cx, cy = gs - 4 * s - (i - j) * 4 * s, gs - 4 * s - j * 4 * s
                r = 1.1 * s
                grip.create_oval(cx - r, cy - r, cx + r, cy + r, fill=T["muted"], outline="")
        grip.place(relx=1.0, rely=1.0, anchor="se", x=-int(5 * s), y=-int(5 * s))
        grip.bind("<ButtonPress-1>", self._grip_start)
        grip.bind("<B1-Motion>", self._grip_move)
        grip.bind("<ButtonRelease-1>", lambda _e: self._save_later())

    def _visible_apps(self):
        q = self.query.lower()
        return [(i, a) for i, a in enumerate(self.cfg["apps"]) if not q or q in a["name"].lower()]

    def _fill_grid(self):
        s = self.s
        for c in self.grid.winfo_children():
            c.destroy()
        items = self._visible_apps()
        total = len(items) + (0 if self.query else 1)
        cols = self.cols = self.cols_for(total)
        g = self.gap = int(4 * s)
        for n, (i, app) in enumerate(items):
            self._tile(i, app, n).grid(row=n // cols, column=n % cols, padx=g, pady=g)
        if not self.query:
            n = len(items)
            self._add_tile().grid(row=n // cols, column=n % cols, padx=g, pady=g)
        elif not items:
            tk.Label(self.grid, text="Ничего не найдено", bg=T["bg"], fg=T["muted"],
                     font=font(10)).grid(row=0, column=0, pady=int(20 * s))
        self.search_lbl.config(text=f"🔍 {self.query}" if self.query else "")

    def _icon(self, app):
        size = self.icon_px
        key = (app["path"], T["panel"], size)
        if key not in self._icons:
            img = file_icon(app["path"], size, T["panel"], app["name"], T["accent"])
            self._icons[key] = ImageTk.PhotoImage(img)
        return self._icons[key]

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
        return f, widgets

    def _tile(self, i, app, n):
        f, widgets = self._make_tile(self._icon(app), app["name"],
                                     badge=str(n + 1) if n < 9 else None)
        for w in widgets:
            w.bind("<ButtonRelease-1>", lambda _e, a=app: self.launch(a))
            w.bind("<Button-3>", lambda e, idx=i: self._tile_menu(e, idx))
        return f

    def _add_tile(self):
        f, widgets = self._make_tile(None, "Добавить", image_text="+")
        for w in widgets:
            w.bind("<ButtonRelease-1>", self._add_menu)
        return f

    # ---------- размер мышью ----------
    def _on_ctrl_wheel(self, e):
        step = 4 if e.delta > 0 else -4
        self.set_icon_size(self.cfg.get("launcher_icon", 40) + step)
        return "break"

    def _grip_start(self, e):
        self._grip = (e.x_root, self.top.winfo_width(), self.cols)

    def _grip_move(self, e):
        x0, w0, cols0 = self._grip
        tw, _ = self.tile_size()
        cell = tw + 2 * self.gap
        # панель по центру — тянем вправо на dx, значит ширина растёт на 2*dx
        cols = round((w0 + 2 * (e.x_root - x0) - 2 * self.pad) / cell)
        cols = max(2, min(COLS_MAX, cols))
        if cols != self.cols:
            self.cfg["launcher_cols"] = cols
            self._fill_grid()
            self._relayout()

    # ---------- геометрия ----------
    def _target(self):
        """Центр монитора, на котором источник (виджет или курсор)."""
        self.body.update_idletasks()
        w, h = self.body.winfo_reqwidth(), self.body.winfo_reqheight()
        l, t, r, b = work_area(*self._origin)
        w, h = min(w, r - l - 16), min(h, b - t - 16)
        return (l + r) // 2 - w // 2, (t + b) // 2 - h // 2, w, h

    def _relayout(self):
        """После поиска/правок — подогнать размер без анимации, оставаясь по центру."""
        if not self.visible() or self._closing:
            return
        x, y, w, h = self._rect = self._target()
        self.body.place_configure(width=w, height=h)
        self.top.geometry(f"{w}x{h}+{x}+{y}")
        round_region(self.top, self.radius, w, h)


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
        self._fill_grid()
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
            self._fill_grid()
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
