"""Всплывающая панель приложений (кнопка на виджете / горячая клавиша)."""
import ctypes
import os
import tkinter as tk
from ctypes import wintypes
from tkinter import filedialog, messagebox, simpledialog

from PIL import ImageTk

from icons import file_icon
from theme import ACCENT, BG, FG, HOVER, MUTED, PANEL, round_corners

user32 = ctypes.windll.user32
COLS = 5
START_MENU = os.path.join(os.environ.get("ProgramData", "C:\\ProgramData"),
                          r"Microsoft\Windows\Start Menu\Programs")


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


class Launcher:
    def __init__(self, root, cfg, save, scale, hotkey_text):
        self.root, self.cfg, self.save, self.s = root, cfg, save, scale
        self.hotkey_text = hotkey_text  # функция → строка с текущей горячей клавишей
        self.top = None
        self.busy = False  # открыт диалог/меню — не закрываться по потере фокуса
        self._icons = {}   # path -> PhotoImage
        self._anchor = None

    # --- показ ---
    def visible(self):
        return self.top is not None and self.top.winfo_exists()

    def toggle(self, anchor=None):
        if self.visible():
            self.hide()
        else:
            self.show(anchor)

    def hide(self):
        if self.visible():
            self.top.destroy()
        self.top = None

    def _rebuild(self):
        """Перерисовать панель на том же месте (после правок списка)."""
        pos = (self.top.winfo_x(), self.top.winfo_y()) if self.visible() else None
        self.show(self._anchor, pos)

    def show(self, anchor=None, pos=None):
        """anchor — окно, рядом с которым открыться; None — у курсора."""
        self.hide()
        self._anchor = anchor
        s = self.s
        top = self.top = tk.Toplevel(self.root, bg=BG)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.attributes("-alpha", 0.97)

        body = tk.Frame(top, bg=BG, padx=int(12 * s), pady=int(10 * s))
        body.pack(fill="both", expand=True)
        head = tk.Frame(body, bg=BG)
        head.pack(fill="x", pady=(0, int(8 * s)))
        tk.Label(head, text="Приложения", bg=BG, fg=FG,
                 font=("Segoe UI Semibold", 11)).pack(side="left")
        close = tk.Label(head, text="✕", bg=BG, fg=MUTED, font=("Segoe UI", 10), cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda _e: self.hide())

        grid = tk.Frame(body, bg=BG)
        grid.pack()
        apps = self.cfg["apps"]
        for i, app in enumerate(apps):
            self._tile(grid, i, app).grid(row=i // COLS, column=i % COLS,
                                           padx=int(3 * s), pady=int(3 * s))
        n = len(apps)
        self._add_tile(grid).grid(row=n // COLS, column=n % COLS, padx=int(3 * s), pady=int(3 * s))

        hint = "1–9 — запуск · Esc — закрыть · ПКМ по плитке — изменить"
        tk.Label(body, text=f"{hint}\nГорячая клавиша: {self.hotkey_text()}", bg=BG, fg=MUTED,
                 font=("Segoe UI", 8), justify="left").pack(anchor="w", pady=(int(8 * s), 0))

        top.bind("<Escape>", lambda _e: self.hide())
        top.bind("<KeyPress>", self._on_key)
        top.bind("<FocusOut>", lambda _e: top.after(150, self._check_focus))

        top.update_idletasks()
        if pos:
            top.geometry(f"+{pos[0]}+{pos[1]}")
        else:
            self._position(anchor)
        round_corners(top)
        self._focus()

    def _position(self, anchor):
        top, s = self.top, self.s
        w, h = top.winfo_reqwidth(), top.winfo_reqheight()
        gap = int(8 * s)
        if anchor is not None and anchor.winfo_viewable():
            ax, ay = anchor.winfo_rootx(), anchor.winfo_rooty()
            aw, ah = anchor.winfo_width(), anchor.winfo_height()
            l, t, r, b = work_area(ax + aw / 2, ay + ah / 2)
            x = ax + aw - w
            y = ay + ah + gap if ay + ah + gap + h <= b else ay - h - gap
        else:
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            l, t, r, b = work_area(pt.x, pt.y)
            x, y = pt.x - w // 2, pt.y - h // 2
        x = max(l + gap, min(x, r - w - gap))
        y = max(t + gap, min(y, b - h - gap))
        top.geometry(f"+{int(x)}+{int(y)}")

    def _focus(self):
        try:
            hwnd = user32.GetParent(self.top.winfo_id())
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        self.top.focus_force()

    def _check_focus(self):
        if self.busy or not self.visible():
            return
        try:
            f = self.top.focus_get()
        except Exception:
            f = None
        if f is None or not str(f).startswith(str(self.top)):
            self.hide()

    # --- плитки ---
    def _icon(self, app):
        key = app["path"]
        if key not in self._icons:
            img = file_icon(app["path"], int(36 * self.s), PANEL, app["name"])
            self._icons[key] = ImageTk.PhotoImage(img)
        return self._icons[key]

    def _make_tile(self, parent, image, text, badge=None, image_text=None):
        s = self.s
        f = tk.Frame(parent, bg=PANEL, width=int(86 * s), height=int(82 * s), cursor="hand2")
        f.pack_propagate(False)
        if image is not None:
            ic = tk.Label(f, image=image, bg=PANEL)
        else:
            ic = tk.Label(f, text=image_text, bg=PANEL, fg=ACCENT, font=("Segoe UI", 20))
        ic.pack(pady=(int(10 * s), int(4 * s)))
        name = tk.Label(f, text=text, bg=PANEL, fg=FG, font=("Segoe UI", 8),
                        wraplength=int(78 * s), justify="center")
        name.pack()
        widgets = [f, ic, name]
        if badge:
            b = tk.Label(f, text=badge, bg=PANEL, fg=MUTED, font=("Segoe UI", 7))
            b.place(x=int(4 * s), y=int(2 * s))
            widgets.append(b)

        def hover(on):
            for w in widgets:
                w.config(bg=HOVER if on else PANEL)

        for w in widgets:
            w.bind("<Enter>", lambda _e: hover(True))
            w.bind("<Leave>", lambda _e: hover(False))
        return f, widgets

    def _tile(self, parent, i, app):
        f, widgets = self._make_tile(parent, self._icon(app), app["name"],
                                     badge=str(i + 1) if i < 9 else None)
        for w in widgets:
            w.bind("<ButtonRelease-1>", lambda _e, a=app: self.launch(a))
            w.bind("<Button-3>", lambda e, idx=i: self._tile_menu(e, idx))
        return f

    def _add_tile(self, parent):
        f, widgets = self._make_tile(parent, None, "Добавить", image_text="+")
        for w in widgets:
            w.bind("<ButtonRelease-1>", lambda _e: self.add_app())
        return f

    # --- действия ---
    def _on_key(self, e):
        if e.char and e.char in "123456789":
            idx = int(e.char) - 1
            if idx < len(self.cfg["apps"]):
                self.launch(self.cfg["apps"][idx])

    def launch(self, app):
        path = app["path"]
        self.hide()
        try:
            cwd = os.path.dirname(path) if os.path.isfile(path) else None
            os.startfile(path, cwd=cwd) if cwd else os.startfile(path)
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

    def add_app(self):
        path = self._modal(lambda: filedialog.askopenfilename(
            parent=self.top, title="Добавить приложение",
            initialdir=START_MENU if os.path.isdir(START_MENU) else None,
            filetypes=[("Программы и ярлыки", "*.exe *.lnk *.url *.bat *.cmd"),
                       ("Все файлы", "*.*")]))
        if not path:
            return
        path = os.path.normpath(path)
        name = os.path.splitext(os.path.basename(path))[0]
        self.cfg["apps"].append({"name": name, "path": path})
        self.save(self.cfg)
        self._rebuild()

    def _tile_menu(self, e, idx):
        apps = self.cfg["apps"]
        m = tk.Menu(self.top, tearoff=0)
        m.add_command(label="Переименовать", command=lambda: self._rename(idx))
        m.add_command(label="Открыть расположение",
                      command=lambda: os.startfile(os.path.dirname(apps[idx]["path"])))
        m.add_separator()
        m.add_command(label="← Сдвинуть влево", state="normal" if idx > 0 else "disabled",
                      command=lambda: self._move(idx, -1))
        m.add_command(label="Сдвинуть вправо →",
                      state="normal" if idx < len(apps) - 1 else "disabled",
                      command=lambda: self._move(idx, 1))
        m.add_separator()
        m.add_command(label="Удалить", command=lambda: self._remove(idx))

        def popup():
            try:
                m.tk_popup(e.x_root, e.y_root)
            finally:
                m.grab_release()

        self._modal(popup)

    def _rename(self, idx):
        app = self.cfg["apps"][idx]
        name = self._modal(lambda: simpledialog.askstring(
            "Переименовать", "Название:", initialvalue=app["name"], parent=self.top))
        if name:
            app["name"] = name.strip()
            self.save(self.cfg)
            self._rebuild()

    def _move(self, idx, d):
        apps = self.cfg["apps"]
        apps[idx], apps[idx + d] = apps[idx + d], apps[idx]
        self.save(self.cfg)
        self._rebuild()

    def _remove(self, idx):
        del self.cfg["apps"][idx]
        self.save(self.cfg)
        self._rebuild()
