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
import updater
from config import APP_NAME, autostart_enabled, load_cfg, save_cfg, set_autostart
from hotkey import HotkeyThread, hotkey_label
from launcher import Launcher
from sensors import Sensors
from settings import SettingsWindow
from theme import ACCENT, BG, FG, HOT, HOVER, MUTED, PANEL, WARN, blend, round_corners

HISTORY = 60          # точек на графике
INTERVAL_MS = 1000    # обновление UI

# key: (заголовок, цвета линий, фикс. максимум шкалы или None=авто, вид)
TILES = {
    "cpu":      ("ЦП · нагрузка",    ["#4fa3ff"], 100, "graph"),
    "cores":    ("ЦП · потоки",      ["#4fa3ff"], 100, "bars"),
    "cpu_temp": ("ЦП · температура", ["#ff6b6b"], 100, "graph"),
    "ram":      ("Память",           ["#b07cff"], 100, "graph"),
    "gpu":      ("ГП · нагрузка",    ["#76b900"], 100, "graph"),
    "gpu_temp": ("ГП · температура", ["#ff8f5a"], 100, "graph"),
    "vram":     ("Видеопамять",      ["#2bc4c4"], 100, "graph"),
    "gpu_pwr":  ("ГП · мощность",    ["#f2c94c"], None, "graph"),
    "disk":     ("Диск R / W",       ["#ffb347", "#ff7a59"], None, "graph"),
    "net":      ("Сеть ↓ / ↑",       ["#3ddc97", "#f2c94c"], None, "graph"),
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


# ---------- утилиты ----------
def fmt_rate(b):
    units = ("Б/с", "КБ/с", "МБ/с", "ГБ/с")
    i = 0
    while b >= 1024 and i < len(units) - 1:
        b /= 1024
        i += 1
    return f"{b:.0f} {units[i]}" if i == 0 else f"{b:.1f} {units[i]}"


def fmt_uptime(sec):
    d, sec = divmod(int(sec), 86400)
    h, sec = divmod(sec, 3600)
    m = sec // 60
    return f"{d}д {h}ч {m:02d}м" if d else f"{h}ч {m:02d}м"


def temp_color(t):
    return HOT if t >= 85 else WARN if t >= 70 else FG


# ---------- плитка ----------
class Tile:
    def __init__(self, parent, title, colors, fixed_max, kind, s):
        self.colors, self.fixed_max, self.kind, self.s = colors, fixed_max, kind, s
        self.series = [deque([0.0] * HISTORY, maxlen=HISTORY) for _ in colors]
        self.bars = []

        self.frame = tk.Frame(parent, bg=PANEL, padx=int(8 * s), pady=int(6 * s))
        tk.Label(self.frame, text=title.upper(), bg=PANEL, fg=MUTED,
                 font=("Segoe UI Semibold", 8), anchor="w").pack(fill="x")
        self.value = tk.Label(self.frame, text="—", bg=PANEL, fg=FG,
                              font=("Segoe UI Semibold", 13), anchor="w")
        self.value.pack(fill="x")
        self.w, self.h = int(190 * s), int(34 * s)
        self.canvas = tk.Canvas(self.frame, width=self.w, height=self.h, bg=PANEL,
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="x", pady=(int(2 * s), 0))

    def set_text(self, text, color=FG):
        self.value.config(text=text, fg=color)

    def push(self, values, text, color=FG):
        for series, v in zip(self.series, values):
            series.append(float(v))
        self.set_text(text, color)
        if self.frame.winfo_ismapped():
            self.draw()

    def push_bars(self, values, text):
        self.bars = values
        self.set_text(text)
        if self.frame.winfo_ismapped():
            self.draw()

    def draw(self):
        c, w, h = self.canvas, self.w, self.h
        c.delete("all")
        c.create_rectangle(0, 0, w, h, fill=blend(BG, PANEL, 0.5), outline="")
        if self.kind == "bars":
            n = max(len(self.bars), 1)
            gap = max(1, int(1 * self.s))
            bw = (w - gap * (n - 1)) / n
            for i, v in enumerate(self.bars):
                x0 = i * (bw + gap)
                bh = max(1, v / 100 * h)
                col = HOT if v >= 90 else self.colors[0]
                c.create_rectangle(x0, h - bh, x0 + bw, h, fill=col, outline="")
            return
        top = self.fixed_max or (max(max(s) for s in self.series) * 1.15) or 1
        step = w / (HISTORY - 1)
        pad = 2 * self.s
        for idx, (series, color) in enumerate(zip(self.series, self.colors)):
            pts = []
            for i, v in enumerate(series):
                pts += [i * step, h - pad - min(v / top, 1) * (h - 2 * pad)]
            if idx == 0:
                c.create_polygon(0, h, *pts, w, h, fill=blend(color, PANEL, 0.25), outline="")
            c.create_line(*pts, fill=color, width=max(1, 1.5 * self.s))


# ---------- виджет ----------
class Widget:
    def __init__(self, selftest=False):
        self.cfg = load_cfg(TILES)
        self.cmds = queue.Queue()
        self.tray = None
        self._drag = None
        self._press = None
        self._moved = False
        self._updating = False

        self.root = root = tk.Tk()
        root.title(APP_NAME)
        root.overrideredirect(True)
        root.configure(bg=BG)
        root.attributes("-alpha", self.cfg["alpha"])
        root.attributes("-topmost", self.cfg["topmost"])
        s = self.s = root.winfo_fpixels("1i") / 96

        body = tk.Frame(root, bg=BG, padx=int(10 * s), pady=int(8 * s))
        body.pack(fill="both", expand=True)

        head = tk.Frame(body, bg=BG)
        head.pack(fill="x", pady=(0, int(6 * s)))
        self.apps_btn = tk.Label(head, text="⊞", bg=PANEL, fg=ACCENT, cursor="hand2",
                                 font=("Segoe UI Symbol", 11), padx=int(7 * s))
        self.apps_btn.pack(side="left", padx=(0, int(8 * s)))
        self.apps_btn.bind("<Enter>", lambda _e: self.apps_btn.config(bg=HOVER))
        self.apps_btn.bind("<Leave>", lambda _e: self.apps_btn.config(bg=PANEL))
        self.apps_btn.bind("<ButtonRelease-1>", self.on_apps_button, add="+")
        tk.Label(head, text=socket.gethostname(), bg=BG, fg=FG,
                 font=("Segoe UI Semibold", 10)).pack(side="left")
        self.lock_lbl = tk.Label(head, text="", bg=BG, fg=MUTED, font=("Segoe UI Emoji", 9))
        self.lock_lbl.pack(side="left", padx=(int(6 * s), 0))
        self.uptime = tk.Label(head, text="", bg=BG, fg=MUTED, font=("Segoe UI", 8))
        self.uptime.pack(side="right")

        self.grid = tk.Frame(body, bg=BG)
        self.grid.pack(fill="both", expand=True)
        self.tiles = {k: Tile(self.grid, *spec, s) for k, spec in TILES.items()}

        self.launcher = Launcher(root, self.cfg, save_cfg, s, self.hotkey_text)
        self.settings = SettingsWindow(self)
        self.hotkeys = HotkeyThread(lambda: self.cmds.put("launcher"))
        self.hotkeys.start()

        self.build_menu()
        root.bind_all("<ButtonPress-1>", self.drag_start)
        root.bind_all("<B1-Motion>", self.drag_move)
        root.bind_all("<ButtonRelease-1>", self.drag_end)
        self.root.bind("<Button-3>", self.show_menu)
        for w in self._all_children(body):
            w.bind("<Button-3>", self.show_menu)

        self.layout()
        self.update_lock_label()
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
            root.after(5000, self.selftest_dump)

    @staticmethod
    def _all_children(w):
        out = [w]
        for c in w.winfo_children():
            out += Widget._all_children(c)
        return out

    def save(self, *_):
        save_cfg(self.cfg)

    # --- горячая клавиша ---
    def apply_hotkey(self):
        hk = self.cfg["hotkey"]
        return self.hotkeys.set(hk["mods"], hk["vk"])

    def hotkey_text(self):
        hk = self.cfg["hotkey"]
        return hotkey_label(hk["mods"], hk["vk"])

    # --- раскладка плиток ---
    def layout(self):
        for t in self.tiles.values():
            t.frame.grid_forget()
        cols = max(1, int(self.cfg["columns"]))
        gap = int(3 * self.s)
        active = [k for k in TILES if k in self.cfg["tiles"]]  # порядок как в TILES
        for i, key in enumerate(active):
            self.tiles[key].frame.grid(row=i // cols, column=i % cols, padx=gap, pady=gap,
                                       sticky="nsew")
            self.tiles[key].draw()

    # --- окно ---
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

    # --- меню виджета ---
    def build_menu(self):
        self.v_top = tk.BooleanVar(value=self.cfg["topmost"])
        self.v_lock = tk.BooleanVar(value=self.cfg["locked"])
        self.v_auto = tk.BooleanVar(value=autostart_enabled())
        self.v_icons_hidden = tk.BooleanVar(value=not desktop.icons_visible())
        self.v_alpha = tk.DoubleVar(value=self.cfg["alpha"])
        self.v_cols = tk.IntVar(value=self.cfg["columns"])
        self.v_tiles = {k: tk.BooleanVar(value=k in self.cfg["tiles"]) for k in TILES}

        m = self.menu = tk.Menu(self.root, tearoff=0)
        m.add_command(label="Приложения", command=lambda: self.launcher.show(self.root))
        m.add_separator()
        m.add_checkbutton(label="Закрепить на месте", variable=self.v_lock, command=self.toggle_lock)
        m.add_checkbutton(label="Поверх всех окон", variable=self.v_top, command=self.toggle_top)
        m.add_checkbutton(label="Скрыть иконки рабочего стола", variable=self.v_icons_hidden,
                          command=lambda: self.set_desktop_icons_hidden(self.v_icons_hidden.get()))
        m.add_separator()

        tiles = tk.Menu(m, tearoff=0)
        for key, (title, *_rest) in TILES.items():
            tiles.add_checkbutton(label=title, variable=self.v_tiles[key], command=self.toggle_tile)
        m.add_cascade(label="Плитки", menu=tiles)

        cols = tk.Menu(m, tearoff=0)
        for n in (1, 2, 3):
            cols.add_radiobutton(label=str(n), value=n, variable=self.v_cols, command=self.set_cols)
        m.add_cascade(label="Колонки", menu=cols)

        alpha = tk.Menu(m, tearoff=0)
        for a in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5):
            alpha.add_radiobutton(label=f"{int(a * 100)}%", value=a, variable=self.v_alpha,
                                  command=self.set_alpha)
        m.add_cascade(label="Непрозрачность", menu=alpha)
        m.add_separator()
        m.add_command(label="Настройки…", command=self.settings.show)
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

    def toggle_tile(self):
        self.cfg["tiles"] = [k for k in TILES if self.v_tiles[k].get()]
        self.layout()
        self.save()

    def set_cols(self):
        self.cfg["columns"] = self.v_cols.get()
        self.layout()
        self.save()

    def set_alpha(self):
        self.cfg["alpha"] = self.v_alpha.get()
        self.root.attributes("-alpha", self.cfg["alpha"])
        self.save()

    def toggle_autostart(self):
        try:
            set_autostart(self.v_auto.get())
        except OSError:
            self.v_auto.set(autostart_enabled())

    # --- трей ---
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
                elif isinstance(cmd, tuple) and cmd[0] == "update_result":
                    self.on_update_result(*cmd[1:])
                elif cmd == "quit":
                    self.quit()
                    return
        except queue.Empty:
            pass
        self.root.after(50, self.poll_cmds)

    # --- обновления ---
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
                self.quit()
            else:
                messagebox.showerror("Обновление не удалось", out)

    def hide(self):
        self.root.withdraw()

    def show(self):
        self.root.deiconify()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", self.cfg["topmost"])

    def quit(self):
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        for aid in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.after_cancel(aid)
        self.root.destroy()

    def selftest_dump(self):
        print({k: t.value.cget("text") for k, t in self.tiles.items()})
        print("hotkey:", self.hotkey_text(), "registered:", self.hotkey_ok)
        print("desktop icons visible:", desktop.icons_visible())
        self.quit()

    # --- данные ---
    def tick(self):
        now = time.monotonic()
        dt = max(now - self.last_t, 1e-3)
        self.last_t = now
        T = self.tiles
        sen = self.sensors.get()

        cpu = psutil.cpu_percent(None)
        T["cpu"].push([cpu], f"{cpu:.0f}%")

        per = psutil.cpu_percent(None, percpu=True)
        T["cores"].push_bars(per, f"{len(per)} потоков · макс {max(per):.0f}%")

        if "cpu_temp" in sen:
            t = sen["cpu_temp"]
            T["cpu_temp"].push([t], f"{t:.0f} °C", temp_color(t))
        else:
            T["cpu_temp"].set_text("нужен LHM", MUTED)

        vm = psutil.virtual_memory()
        gb = 1024 ** 3
        T["ram"].push([vm.percent], f"{vm.percent:.0f}% · {vm.used / gb:.1f}/{vm.total / gb:.0f} ГБ")

        if "gpu" in sen:
            T["gpu"].push([sen["gpu"]], f"{sen['gpu']:.0f}%")
        else:
            T["gpu"].set_text("нет данных", MUTED)
        if "gpu_temp" in sen:
            t = sen["gpu_temp"]
            T["gpu_temp"].push([t], f"{t:.0f} °C", temp_color(t))
        else:
            T["gpu_temp"].set_text("нет данных", MUTED)
        if "vram_used" in sen and sen.get("vram_total"):
            pct = sen["vram_used"] / sen["vram_total"] * 100
            T["vram"].push([pct], f"{pct:.0f}% · {sen['vram_used'] / 1024:.1f}/{sen['vram_total'] / 1024:.0f} ГБ")
        else:
            T["vram"].set_text("нет данных", MUTED)
        if "gpu_pwr" in sen:
            T["gpu_pwr"].push([sen["gpu_pwr"]], f"{sen['gpu_pwr']:.0f} Вт")
        else:
            T["gpu_pwr"].set_text("нет данных", MUTED)

        dio = psutil.disk_io_counters()
        if dio and self.last_disk:
            rd = (dio.read_bytes - self.last_disk.read_bytes) / dt
            wr = (dio.write_bytes - self.last_disk.write_bytes) / dt
            T["disk"].push([rd, wr], f"{fmt_rate(rd)} / {fmt_rate(wr)}")
        self.last_disk = dio

        nio = psutil.net_io_counters()
        down = (nio.bytes_recv - self.last_net.bytes_recv) / dt
        up = (nio.bytes_sent - self.last_net.bytes_sent) / dt
        T["net"].push([down, up], f"{fmt_rate(down)} / {fmt_rate(up)}")
        self.last_net = nio

        self.uptime.config(text="аптайм " + fmt_uptime(time.time() - psutil.boot_time()))
        self.root.after(INTERVAL_MS, self.tick)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if single_instance() is None:
        sys.exit(0)
    Widget(selftest="--selftest" in sys.argv).run()
