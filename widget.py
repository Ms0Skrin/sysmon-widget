"""Полупрозрачный виджет-монитор системы для рабочего стола Windows.

ЛКМ + перетаскивание — двигать (если не закреплён), ПКМ — меню.
Кнопка ⊞ или горячая клавиша (по умолчанию Ctrl+Alt+Space) — панель приложений.
Иконка в трее (ПКМ): показать/скрыть, приложения, настройки, обновления, выход.
"""
import ctypes
import queue
import socket
import sys
import threading
import time
import tkinter as tk
from collections import deque
from tkinter import messagebox

import psutil

import desktop
import nabyscape
import sensorsvc
import translucenttb
import updater
from config import APP_NAME, autostart_enabled, load_cfg, save_cfg, set_autostart
from hotkey import HotkeyThread, hotkey_label
from launcher import Launcher
from sensors import Sensors, fmt_rate, fmt_value, sensor_max
from settings import SettingsWindow
from theme import T, draw_series, font, round_corners, series_colors, set_theme

HISTORY = 60          # точек на графике
INTERVAL_MS = 1000    # обновление UI

# встроенные плитки: key -> (заголовок, фикс. максимум шкалы или None=авто, вид)
TILES = {
    "cpu":      ("ЦП · нагрузка",    100, "graph"),
    "cores":    ("ЦП · потоки",      100, "bars"),
    "cpu_temp": ("ЦП · температура", 100, "graph"),
    "ram":      ("Память",           100, "graph"),
    "gpu":      ("ГП · нагрузка",    100, "graph"),
    "gpu_temp": ("ГП · температура", 100, "graph"),
    "vram":     ("Видеопамять",      100, "graph"),
    "gpu_pwr":  ("ГП · мощность",    None, "graph"),
    "disk":     ("Диск R / W",       None, "graph"),
    "net":      ("Сеть ↓ / ↑",       None, "graph"),
}

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass


def single_instance():
    """Не даём запустить вторую копию. При перезапуске после обновления ждём старую."""
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = ctypes.c_void_p
    deadline = time.time() + (10 if "--restart" in sys.argv else 0)
    while True:
        h = k32.CreateMutexW(None, True, "Local\\" + APP_NAME)
        if k32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
            return h
        k32.CloseHandle(ctypes.c_void_p(h))
        if time.time() >= deadline:
            return None
        time.sleep(0.3)


def fmt_uptime(sec):
    d, sec = divmod(int(sec), 86400)
    h, sec = divmod(sec, 3600)
    m = sec // 60
    return f"{d}d {h}h {m:02d}m" if d else f"{h}h {m:02d}m"


def temp_color(t):
    return T["hot"] if t >= 85 else T["warn"] if t >= 70 else T["fg"]


# ---------- плитка ----------
class Tile:
    def __init__(self, parent, title, colors, fixed_max, kind, s, history):
        self.colors, self.fixed_max, self.kind, self.s = colors, fixed_max, kind, s
        self.series = history  # список deque, живёт дольше плитки (переживает смену темы)
        self.bars = []

        self.frame = tk.Frame(parent, bg=T["panel"], padx=int(8 * s), pady=int(6 * s))
        tk.Label(self.frame, text=title.upper(), bg=T["panel"], fg=T["muted"],
                 font=font(8, True), anchor="w").pack(fill="x")
        self.value = tk.Label(self.frame, text="—", bg=T["panel"], fg=T["fg"],
                              font=font(13, True), anchor="w")
        self.value.pack(fill="x")
        self.w, self.h = int(190 * s), int(34 * s)
        self.canvas = tk.Canvas(self.frame, width=self.w, height=self.h, bg=T["graph_bg"],
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="x", pady=(int(2 * s), 0))

    def set_text(self, text, color=None):
        self.value.config(text=text, fg=color or T["fg"])

    def push(self, values, text, color=None):
        for series, v in zip(self.series, values):
            series.append(float(v))
        self.set_text(text, color)
        self.draw()

    def push_bars(self, values, text):
        self.bars = values
        self.set_text(text)
        self.draw()

    def draw(self):
        c, w, h = self.canvas, self.w, self.h
        c.delete("all")
        if self.kind == "bars":
            n = max(len(self.bars), 1)
            gap = max(1, int(1 * self.s))
            bw = (w - gap * (n - 1)) / n
            for i, v in enumerate(self.bars):
                x0 = i * (bw + gap)
                bh = max(1, v / 100 * h)
                col = T["hot"] if v >= 90 else self.colors[0]
                c.create_rectangle(x0, h - bh, x0 + bw, h, fill=col, outline="")
            return
        top = self.fixed_max or (max(max(s) for s in self.series) * 1.15) or 1
        draw_series(c, w, h, self.series, self.colors, top, T["style"],
                    max(1, T["line_w"] * self.s), T["graph_bg"], pad=2 * self.s)


# ---------- виджет ----------
class Widget:
    TILES = TILES

    def __init__(self, selftest=False):
        self.cfg = load_cfg(set(TILES))
        self.cfg["tiles"] = [k for k in self.cfg["tiles"]
                             if k in TILES or k in self.cfg["sensor_meta"]]
        set_theme(self.cfg["theme"], self.cfg["graph_style"])
        self.cmds = queue.Queue()
        self.tray = None
        self._drag = None
        self._press = None
        self._moved = False
        self._updating = False
        self.hist = {}
        self.tiles = {}
        self.naby_msg = ""
        self.taskbar_msg = ""
        self._naby_busy = False

        self.root = root = tk.Tk()
        root.title(APP_NAME)
        root.overrideredirect(True)
        root.attributes("-alpha", self.cfg["alpha"])
        root.attributes("-topmost", self.cfg["topmost"])
        self.s = root.winfo_fpixels("1i") / 96

        self.make_vars()
        self.launcher = Launcher(root, self.cfg, save_cfg, self.s, self.hotkey_text)
        self.settings = SettingsWindow(self)
        self.hotkeys = HotkeyThread(lambda: self.cmds.put("launcher"))
        self.hotkeys.start()

        self.build_ui()
        self.build_menu()
        root.bind_all("<ButtonPress-1>", self.drag_start)
        root.bind_all("<B1-Motion>", self.drag_move)
        root.bind_all("<ButtonRelease-1>", self.drag_end)
        root.update_idletasks()
        self.place_window()
        round_corners(root)

        psutil.cpu_percent(None)
        psutil.cpu_percent(None, percpu=True)
        self.last_t = time.monotonic()
        self.last_disk = psutil.disk_io_counters()
        self.last_net = psutil.net_io_counters()

        self.sensors = Sensors()
        self.sensors.start()
        self.hotkey_ok = self.apply_hotkey()
        self.start_tray()
        root.after(200, self.tick)
        root.after(50, self.poll_cmds)
        if selftest:
            root.after(6000, self.selftest_dump)
        else:
            root.after(1500, self.first_run)
            if self.cfg["naby_enabled"]:
                root.after(2500, lambda: self.naby_ensure(start=True))
        if self.cfg["taskbar_mode"]:
            root.after(1000, lambda: self.set_taskbar_mode(self.cfg["taskbar_mode"]))

    # ---------- интерфейс ----------
    def build_ui(self):
        root, s = self.root, self.s
        for c in root.winfo_children():
            if not isinstance(c, (tk.Toplevel, tk.Menu)):
                c.destroy()
        root.configure(bg=T["bg"])
        body = self.body = tk.Frame(root, bg=T["bg"], padx=int(10 * s), pady=int(8 * s))
        body.pack(fill="both", expand=True)

        head = tk.Frame(body, bg=T["bg"])
        head.pack(fill="x", pady=(0, int(6 * s)))
        self.apps_btn = tk.Label(head, text="⊞", bg=T["panel"], fg=T["accent"], cursor="hand2",
                                 font=("Segoe UI Symbol", 11), padx=int(7 * s))
        self.apps_btn.pack(side="left", padx=(0, int(8 * s)))
        self.apps_btn.bind("<Enter>", lambda _e: self.apps_btn.config(bg=T["hover"]))
        self.apps_btn.bind("<Leave>", lambda _e: self.apps_btn.config(bg=T["panel"]))
        self.apps_btn.bind("<ButtonRelease-1>", self.on_apps_button, add="+")
        root.apps_button = self.apps_btn
        tk.Label(head, text=socket.gethostname(), bg=T["bg"], fg=T["fg"],
                 font=font(10, True)).pack(side="left")
        self.lock_lbl = tk.Label(head, text="", bg=T["bg"], fg=T["muted"], font=("Segoe UI Emoji", 9))
        self.lock_lbl.pack(side="left", padx=(int(6 * s), 0))
        self.uptime = tk.Label(head, text="", bg=T["bg"], fg=T["muted"], font=font(8))
        self.uptime.pack(side="right")

        self.grid = tk.Frame(body, bg=T["bg"])
        self.grid.pack(fill="both", expand=True)
        self.tiles = {}
        self.layout()
        self.update_lock_label()

    def _bind_menu(self, w):
        w.bind("<Button-3>", self.show_menu)
        for c in w.winfo_children():
            self._bind_menu(c)

    def tile_spec(self, key):
        """→ (title, colors, fixed_max, kind, n_series) или None."""
        if key in TILES:
            title, mx, kind = TILES[key]
            colors = series_colors(key)
            return title, colors, mx, kind, len(colors)
        meta = self.cfg["sensor_meta"].get(key)
        if meta:
            return meta["title"], series_colors(key, meta["type"]), sensor_max(meta["type"]), "graph", 1
        return None

    def get_tile(self, key):
        if key not in self.tiles:
            spec = self.tile_spec(key)
            if not spec:
                return None
            title, colors, mx, kind, n = spec
            hist = self.hist.setdefault(key, [deque([0.0] * HISTORY, maxlen=HISTORY) for _ in range(n)])
            self.tiles[key] = Tile(self.grid, title, colors, mx, kind, self.s, hist)
        return self.tiles[key]

    def layout(self):
        for t in self.tiles.values():
            t.frame.grid_forget()
        cols = max(1, int(self.cfg["columns"]))
        gap = int(3 * self.s)
        for i, key in enumerate(self.cfg["tiles"]):
            t = self.get_tile(key)
            if t:
                t.frame.grid(row=i // cols, column=i % cols, padx=gap, pady=gap, sticky="nsew")
                t.draw()
        self._bind_menu(self.body)

    def tile_enabled(self, key):
        return key in self.cfg["tiles"]

    def set_tile(self, key, on, meta=None):
        if meta:
            self.cfg["sensor_meta"][key] = meta
        tiles = self.cfg["tiles"]
        if on and key not in tiles:
            tiles.append(key)
        elif not on and key in tiles:
            tiles.remove(key)
            if key in self.tiles:
                self.tiles.pop(key).frame.destroy()
        if key in self.v_tiles:
            self.v_tiles[key].set(on)
        self.layout()
        self.save()

    def apply_theme(self, name, style=""):
        self.cfg["theme"], self.cfg["graph_style"] = name, style
        set_theme(name, style)
        self.save()
        self.build_ui()
        self.build_menu()
        round_corners(self.root)
        if self.launcher.visible():
            self.launcher.show(self.root, animate=False)
        if self.cfg["taskbar_mode"] == "tint":
            self.set_taskbar_mode("tint")

    # ---------- общее ----------
    def save(self, *_):
        save_cfg(self.cfg)

    def apply_hotkey(self):
        hk = self.cfg["hotkey"]
        return self.hotkeys.set(hk["mods"], hk["vk"])

    def hotkey_text(self):
        hk = self.cfg["hotkey"]
        return hotkey_label(hk["mods"], hk["vk"])

    # ---------- окно ----------
    def place_window(self):
        r = self.root
        w = r.winfo_reqwidth()
        u = ctypes.windll.user32
        vx, vy = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
        vw, vh = u.GetSystemMetrics(78), u.GetSystemMetrics(79)
        x, y = self.cfg["x"], self.cfg["y"]
        if x is None or y is None or not (vx <= x <= vx + vw - 40 and vy <= y <= vy + vh - 40):
            x = r.winfo_screenwidth() - w - int(24 * self.s)
            y = int(48 * self.s)
        r.geometry(f"+{x}+{y}")

    def _is_main(self, widget):
        try:
            return widget.winfo_toplevel() is self.root
        except Exception:
            return False

    def drag_start(self, e):
        self._drag = None
        if not self._is_main(e.widget):
            return
        self._press = (e.x_root, e.y_root)
        self._moved = False
        if not self.cfg["locked"]:
            self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def drag_move(self, e):
        if self._press and abs(e.x_root - self._press[0]) + abs(e.y_root - self._press[1]) > 4:
            self._moved = True
        if self._drag and self._moved:
            self.root.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def drag_end(self, _e):
        if self._drag and self._moved:
            self.cfg["x"], self.cfg["y"] = self.root.winfo_x(), self.root.winfo_y()
            self.save()
        self._drag = None

    def on_apps_button(self, _e):
        if not self._moved:
            self.launcher.toggle(anchor=self.root)

    # ---------- меню виджета ----------
    def make_vars(self):
        self.v_top = tk.BooleanVar(value=self.cfg["topmost"])
        self.v_lock = tk.BooleanVar(value=self.cfg["locked"])
        self.v_auto = tk.BooleanVar(value=autostart_enabled())
        self.v_icons_hidden = tk.BooleanVar(value=not desktop.icons_visible())
        self.v_alpha = tk.DoubleVar(value=self.cfg["alpha"])
        self.v_cols = tk.IntVar(value=self.cfg["columns"])
        self.v_tiles = {k: tk.BooleanVar(value=k in self.cfg["tiles"]) for k in TILES}

    def build_menu(self):
        m = self.menu = tk.Menu(self.root, tearoff=0)
        m.add_command(label="Приложения", command=lambda: self.launcher.show(self.root))
        m.add_separator()
        m.add_checkbutton(label="Закрепить на месте", variable=self.v_lock, command=self.toggle_lock)
        m.add_checkbutton(label="Поверх всех окон", variable=self.v_top, command=self.toggle_top)
        m.add_checkbutton(label="Скрыть иконки рабочего стола", variable=self.v_icons_hidden,
                          command=lambda: self.set_desktop_icons_hidden(self.v_icons_hidden.get()))
        m.add_separator()
        tiles = tk.Menu(m, tearoff=0)
        for key, (title, *_r) in TILES.items():
            tiles.add_checkbutton(label=title, variable=self.v_tiles[key],
                                  command=lambda k=key: self.set_tile(k, self.v_tiles[k].get()))
        tiles.add_separator()
        tiles.add_command(label="Все датчики…", command=lambda: self.settings.show("sensors"))
        m.add_cascade(label="Плитки", menu=tiles)
        cols = tk.Menu(m, tearoff=0)
        for n in (1, 2, 3, 4):
            cols.add_radiobutton(label=str(n), value=n, variable=self.v_cols, command=self.set_cols)
        m.add_cascade(label="Колонки", menu=cols)
        m.add_command(label="Оформление…", command=lambda: self.settings.show("look"))
        if self.cfg["naby_enabled"]:
            nm = tk.Menu(m, tearoff=0)
            nm.add_command(label="Открыть настройки NabyScape", command=lambda: self.naby_action("settings"))
            nm.add_command(label="Перезапустить", command=lambda: self.naby_action("restart"))
            nm.add_command(label="Остановить", command=lambda: self.naby_action("stop"))
            nm.add_separator()
            nm.add_command(label="Интеграция…", command=lambda: self.settings.show("naby"))
            m.add_cascade(label="NabyScape", menu=nm)
        m.add_separator()
        m.add_command(label="Настройки…", command=lambda: self.settings.show())
        m.add_command(label="Скрыть в трей", command=self.hide)
        m.add_command(label="Выход", command=self.quit)

    def show_menu(self, e):
        self.v_icons_hidden.set(not desktop.icons_visible())
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def toggle_lock(self):
        self.cfg["locked"] = self.v_lock.get()
        self.update_lock_label()
        self.save()

    def update_lock_label(self):
        self.lock_lbl.config(text="🔒" if self.cfg["locked"] else "")

    def toggle_top(self):
        self.cfg["topmost"] = self.v_top.get()
        self.root.attributes("-topmost", self.cfg["topmost"])
        self.save()

    def set_desktop_icons_hidden(self, hidden):
        desktop.set_icons_visible(not hidden)
        self.v_icons_hidden.set(hidden)

    def set_cols(self):
        self.cfg["columns"] = self.v_cols.get()
        self.layout()
        self.save()

    def set_alpha(self, a):
        self.cfg["alpha"] = round(a, 2)
        self.v_alpha.set(self.cfg["alpha"])
        self.root.attributes("-alpha", self.cfg["alpha"])
        self.save()

    def set_launcher_alpha(self, pct):
        self.cfg["launcher_alpha"] = round(pct / 100, 2)
        if self.launcher.visible():
            self.launcher.top.attributes("-alpha", self.cfg["launcher_alpha"])
        self.save()

    def toggle_autostart(self):
        try:
            set_autostart(self.v_auto.get())
        except OSError:
            self.v_auto.set(autostart_enabled())

    # ---------- датчики LHM ----------
    def start_sensors(self, allow_uac=False):
        if self.cfg["lhm_enabled"]:
            threading.Thread(target=sensorsvc.start_host, args=(allow_uac,), daemon=True).start()

    def set_lhm_enabled(self, on):
        self.cfg["lhm_enabled"] = on
        self.save()
        if on:
            self.start_sensors(allow_uac=True)
        else:
            sensorsvc.stop_host()

    def run_setup(self):
        self.cfg["setup_asked"] = True
        self.cfg["lhm_enabled"] = True
        self.save()
        sensorsvc.setup_all()

    def first_run(self):
        if not self.cfg["lhm_enabled"]:
            return
        if sensorsvc.task_exists() or sensorsvc.host_running():
            self.start_sensors()
            return
        if self.cfg["setup_asked"]:
            return
        self.cfg["setup_asked"] = True
        self.save()
        if messagebox.askyesno(
                "Датчики температуры",
                "Чтобы показывать температуру процессора, вентиляторы и другие датчики, "
                "виджету нужны права администратора.\n\n"
                "Сейчас один раз появится запрос Windows (UAC): будет установлен драйвер PawnIO "
                "(если его нет) и задача, чтобы дальше датчики запускались без запросов.\n\n"
                "Настроить сейчас? (Это можно сделать позже: Настройки → Датчики.)"):
            sensorsvc.setup_all()

    # ---------- панель задач (TranslucentTB) ----------
    def set_taskbar_mode(self, mode):
        self.cfg["taskbar_mode"] = mode
        self.save()
        tint = T["bg"]

        def work():
            try:
                translucenttb.apply(mode, tint)
                self.taskbar_msg = ""
            except Exception as ex:
                self.taskbar_msg = f"Панель задач: {ex}"

        threading.Thread(target=work, daemon=True).start()

    # ---------- NabyScape ----------
    def set_naby_enabled(self, on):
        self.cfg["naby_enabled"] = on
        self.save()
        self.build_menu()
        if self.tray:
            self.tray.update_menu()
        if on:
            self.naby_ensure(start=True)
        else:
            self.naby_action("stop")

    def _naby_job(self, fn):
        if self._naby_busy:
            return
        self._naby_busy = True

        def work():
            try:
                fn()
            except Exception as ex:
                self.naby_msg = f"Ошибка: {ex}"
            finally:
                self._naby_busy = False

        threading.Thread(target=work, daemon=True).start()

    def _naby_progress(self, _frac, text):
        self.naby_msg = text

    def naby_ensure(self, start=True):
        def fn():
            if not nabyscape.installed():
                nabyscape.install(self._naby_progress)
            if start and self.cfg["naby_enabled"]:
                nabyscape.start()
                self.naby_msg = "Запущен."
        self._naby_job(fn)

    def naby_update(self):
        def fn():
            self.naby_msg = "Проверяю обновления…"
            newer, tag = nabyscape.update_available()
            if newer:
                nabyscape.install(self._naby_progress)
            else:
                self.naby_msg = f"Установлена последняя версия ({tag})."
        self._naby_job(fn)

    def naby_action(self, act):
        def fn():
            if act == "settings":
                if not nabyscape.installed():
                    nabyscape.install(self._naby_progress)
                nabyscape.open_settings()
                self.naby_msg = ""
            elif act == "restart":
                nabyscape.restart()
                self.naby_msg = "Перезапущен."
            elif act == "stop":
                nabyscape.stop()
                self.naby_msg = "Остановлен."
            elif act == "uninstall":
                nabyscape.uninstall()
                self.naby_msg = "Удалён."
        self._naby_job(fn)

    # ---------- трей ----------
    def start_tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            return
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((2, 2, 62, 62), 12, fill=(22, 24, 29, 255))
        for i, (hgt, col) in enumerate([(22, "#4fa3ff"), (38, "#b07cff"), (30, "#ffb347"), (46, "#3ddc97")]):
            x = 10 + i * 12
            d.rectangle((x, 54 - hgt, x + 8, 54), fill=col)
        cmd = lambda name: (lambda: self.cmds.put(name))
        menu = pystray.Menu(
            pystray.MenuItem("Показать / скрыть виджет", cmd("toggle"), default=True),
            pystray.MenuItem("Приложения", cmd("launcher")),
            pystray.MenuItem("Настройки…", cmd("settings")),
            pystray.MenuItem("NabyScape: настройки", cmd("naby_settings"),
                             visible=lambda _i: self.cfg["naby_enabled"]),
            pystray.MenuItem("NabyScape: перезапустить", cmd("naby_restart"),
                             visible=lambda _i: self.cfg["naby_enabled"]),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Проверить обновления", cmd("update")),
            pystray.MenuItem(f"Версия: {updater.version()}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", cmd("quit")),
        )
        self.tray = pystray.Icon(APP_NAME, img, "Монитор системы", menu)
        self.tray.run_detached()

    def poll_cmds(self):
        try:
            while True:
                cmd = self.cmds.get_nowait()
                if cmd == "toggle":
                    self.show() if self.root.state() == "withdrawn" else self.hide()
                elif cmd == "launcher":
                    self.launcher.toggle()
                elif cmd == "settings":
                    self.settings.show()
                elif cmd == "update":
                    self.check_updates()
                elif cmd == "naby_settings":
                    self.naby_action("settings")
                elif cmd == "naby_restart":
                    self.naby_action("restart")
                elif isinstance(cmd, tuple) and cmd[0] == "update_result":
                    self.on_update_result(*cmd[1:])
                elif cmd == "quit":
                    self.quit()
                    return
        except queue.Empty:
            pass
        self.root.after(50, self.poll_cmds)

    # ---------- обновления ----------
    def check_updates(self):
        if self._updating:
            return
        self._updating = True
        threading.Thread(target=lambda: self.cmds.put(("update_result", *updater.check())),
                         daemon=True).start()

    def on_update_result(self, count, info):
        self._updating = False
        if count is None:
            messagebox.showwarning("Обновления", info)
        elif count == 0:
            messagebox.showinfo("Обновления", f"У вас последняя версия ({updater.version()}).")
        elif messagebox.askyesno("Доступно обновление",
                                 f"Новых изменений: {count}\n\n{info}\n\nОбновить и перезапустить?"):
            ok, out = updater.apply()
            if ok:
                updater.restart()
                self.quit(stop_sensors=False)
            else:
                messagebox.showerror("Обновление не удалось", out)

    def hide(self):
        self.root.withdraw()

    def show(self):
        self.root.deiconify()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", self.cfg["topmost"])

    def quit(self, stop_sensors=True):
        if self.cfg["taskbar_mode"] and stop_sensors:
            threading.Thread(target=translucenttb.stop, daemon=False).start()
        if stop_sensors:
            sensorsvc.stop_host()
            if self.cfg["naby_enabled"]:
                threading.Thread(target=nabyscape.stop, daemon=False).start()
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        for aid in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.after_cancel(aid)
        try:
            self.root.destroy()
        except tk.TclError:
            self.root.quit()

    def selftest_dump(self):
        print({k: t.value.cget("text") for k, t in self.tiles.items()})
        lhm, ok = self.sensors.get_lhm()
        print("lhm ok:", ok, "sensors:", len(lhm), "| uptime:", self.uptime.cget("text"))
        print("hotkey:", self.hotkey_text(), "registered:", self.hotkey_ok)
        self.quit(stop_sensors=False)

    # ---------- данные ----------
    def tick(self):
        now = time.monotonic()
        dt = max(now - self.last_t, 1e-3)
        self.last_t = now
        active = set(self.cfg["tiles"])
        sen = self.sensors.get()

        def tile(key):
            return self.tiles.get(key) if key in active else None

        cpu = psutil.cpu_percent(None)
        per = psutil.cpu_percent(None, percpu=True)
        vm = psutil.virtual_memory()
        dio = psutil.disk_io_counters()
        nio = psutil.net_io_counters()
        rd = wr = 0.0
        if dio and self.last_disk:
            rd = (dio.read_bytes - self.last_disk.read_bytes) / dt
            wr = (dio.write_bytes - self.last_disk.write_bytes) / dt
        self.last_disk = dio
        down = (nio.bytes_recv - self.last_net.bytes_recv) / dt
        up = (nio.bytes_sent - self.last_net.bytes_sent) / dt
        self.last_net = nio

        if t := tile("cpu"):
            t.push([cpu], f"{cpu:.0f}%")
        if t := tile("cores"):
            t.push_bars(per, f"{len(per)} потоков · макс {max(per):.0f}%")
        if t := tile("cpu_temp"):
            if "cpu_temp" in sen:
                v = sen["cpu_temp"]
                t.push([v], f"{v:.0f} °C", temp_color(v))
            else:
                t.set_text("датчики выкл." if not self.cfg["lhm_enabled"] else "ожидание…", T["muted"])
        if t := tile("ram"):
            gb = 1024 ** 3
            t.push([vm.percent], f"{vm.percent:.0f}% · {vm.used / gb:.1f}/{vm.total / gb:.0f} ГБ")
        for key, fmt in (("gpu", lambda v: f"{v:.0f}%"), ("gpu_pwr", lambda v: f"{v:.0f} Вт")):
            if t := tile(key):
                if key in sen:
                    t.push([sen[key]], fmt(sen[key]))
                else:
                    t.set_text("нет данных", T["muted"])
        if t := tile("gpu_temp"):
            if "gpu_temp" in sen:
                v = sen["gpu_temp"]
                t.push([v], f"{v:.0f} °C", temp_color(v))
            else:
                t.set_text("нет данных", T["muted"])
        if t := tile("vram"):
            if "vram_used" in sen and sen.get("vram_total"):
                pct = sen["vram_used"] / sen["vram_total"] * 100
                t.push([pct], f"{pct:.0f}% · {sen['vram_used'] / 1024:.1f}/{sen['vram_total'] / 1024:.0f} ГБ")
            else:
                t.set_text("нет данных", T["muted"])
        if t := tile("disk"):
            t.push([rd, wr], f"{fmt_rate(rd)} / {fmt_rate(wr)}")
        if t := tile("net"):
            t.push([down, up], f"{fmt_rate(down)} / {fmt_rate(up)}")

        lhm_keys = [k for k in self.cfg["tiles"] if k.startswith("lhm:")]
        if lhm_keys:
            lhm, ok = self.sensors.get_lhm()
            by_id = {s_["id"]: s_ for s_ in lhm}
            for key in lhm_keys:
                t = self.tiles.get(key)
                if not t:
                    continue
                s_ = by_id.get(key[4:])
                if s_:
                    v = s_["value"]
                    color = temp_color(v) if s_["type"] == "Temperature" else None
                    t.push([v], fmt_value(s_["type"], v), color)
                else:
                    t.set_text("нет данных" if ok else "датчики выкл.", T["muted"])

        self.uptime.config(text="Uptime " + fmt_uptime(time.time() - psutil.boot_time()))
        self.root.after(INTERVAL_MS, self.tick)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if single_instance() is None:
        sys.exit(0)
    Widget(selftest="--selftest" in sys.argv).run()
