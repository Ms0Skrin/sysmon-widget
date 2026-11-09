"""SysMon Widget — полупрозрачные панели-виджеты для рабочего стола Windows.

Панелей может быть несколько, у каждой свой набор плиток (включая большие «Процессор» и
«Видеокарта»). ЛКМ + перетаскивание — двигать, ПКМ — меню панели.
Кнопка с точками (в выбранной панели) или горячая клавиша — панель приложений.
Иконка в трее (ПКМ): показать/скрыть, приложения, настройки, обновления, выход.
"""
import ctypes
import queue
import sys
import threading
import time
import tkinter as tk
import uuid
from tkinter import messagebox, simpledialog

import psutil

import desktop
import nabyscape
import sensorsvc
import translucenttb
import updater
from config import APP_NAME, autostart_enabled, load_cfg, save_cfg, set_autostart
from hotkey import HotkeyThread, hotkey_label
from launcher import Launcher
from panel import TILES, Panel
from sensors import Sensors, cpu_details, fmt_rate, fmt_value, gpu_details
from settings import SettingsWindow
from theme import T, set_theme
from tray import TrayIcon
from traymenu import TrayMenu

INTERVAL_MS = 1000

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
    return T["hot"] if t >= 85 else T["warn"] if t >= 70 else None


def migrate_panels(cfg):
    """Старый формат (одно окно) → список панелей."""
    if not cfg.get("panels"):
        cfg["panels"] = [{"id": "main", "name": "Основная", "tiles": list(cfg.get("tiles", [])),
                          "columns": cfg.get("columns", 2), "x": cfg.get("x"), "y": cfg.get("y"),
                          "header": True}]
    ids = [p["id"] for p in cfg["panels"]]
    if cfg.get("menu_panel") not in ids:
        cfg["menu_panel"] = ids[0]
    for p in cfg["panels"]:
        p["tiles"] = [k for k in p.get("tiles", []) if k in TILES or k in cfg["sensor_meta"]]


class Widget:
    """Приложение: трей, датчики, настройки, горячая клавиша — и набор панелей."""
    TILES = {k: (v[0],) for k, v in TILES.items()}

    def __init__(self, selftest=False):
        self.cfg = load_cfg(set(TILES))
        migrate_panels(self.cfg)
        set_theme(self.cfg["theme"], self.cfg["graph_style"])
        self.cmds = queue.Queue()
        self.tray = None
        self._updating = False
        self.hist = {}
        self.panels = []
        self.edit_pid = self.cfg["panels"][0]["id"]  # какую панель правит вкладка «Датчики»
        self.uptime_text = ""
        self.naby_msg = ""
        self.taskbar_msg = ""
        self._naby_busy = False

        self.root = root = tk.Tk()
        root.title(APP_NAME)
        root.withdraw()  # корневое окно скрыто; панели — отдельные окна
        self.s = root.winfo_fpixels("1i") / 96

        self.make_vars()
        self.launcher = Launcher(root, self.cfg, save_cfg, self.s, self.hotkey_text)
        self.settings = SettingsWindow(self)
        self.hotkeys = HotkeyThread(lambda: self.cmds.put("launcher"))
        self.hotkeys.start()

        psutil.cpu_percent(None)
        psutil.cpu_percent(None, percpu=True)
        self.last_t = time.monotonic()
        self.last_disk = psutil.disk_io_counters()
        self.last_net = psutil.net_io_counters()
        self.sensors = Sensors()
        self.sensors.start()

        self.build_panels()
        self.hotkey_ok = self.apply_hotkey()
        self.start_tray()
        root.after(200, self.tick)
        root.after(1200, self.launcher.prewarm)
        root.after(50, self.poll_cmds)
        if selftest:
            root.after(6000, self.selftest_dump)
        else:
            root.after(1500, self.first_run)
            if self.cfg["naby_enabled"]:
                root.after(2500, lambda: self.naby_ensure(start=True))
        if self.cfg["taskbar_mode"]:
            root.after(1000, lambda: self.set_taskbar_mode(self.cfg["taskbar_mode"]))

    # ---------- панели ----------
    def build_panels(self):
        for p in self.panels:
            p.destroy()
        self.panels = [Panel(self, pc) for pc in self.cfg["panels"]]

    def panel(self, pid):
        return next((p for p in self.panels if p.pid == pid), None)

    def panel_cfg(self, pid):
        return next((p for p in self.cfg["panels"] if p["id"] == pid), None)

    @property
    def menu_panel(self):
        return self.panel(self.cfg["menu_panel"]) or (self.panels[0] if self.panels else None)

    def add_panel(self, name=None, tiles=None):
        n = len(self.cfg["panels"]) + 1
        pc = {"id": uuid.uuid4().hex[:8], "name": name or f"Панель {n}",
              "tiles": tiles if tiles is not None else ["gpu_card"], "columns": 2, "x": None, "y": None,
              "header": True}
        self.cfg["panels"].append(pc)
        self.panels.append(Panel(self, pc))
        self.save()
        return pc

    def remove_panel(self, pid):
        if len(self.cfg["panels"]) <= 1:
            return
        self.cfg["panels"] = [p for p in self.cfg["panels"] if p["id"] != pid]
        p = self.panel(pid)
        if p:
            p.destroy()
            self.panels.remove(p)
        if self.cfg["menu_panel"] == pid:
            self.set_menu_panel(self.cfg["panels"][0]["id"])
        if self.edit_pid == pid:
            self.edit_pid = self.cfg["panels"][0]["id"]
        self.save()

    def rename_panel(self, pid, name):
        self.panel_cfg(pid)["name"] = name
        self.relayout(pid)
        self.save()

    def set_menu_panel(self, pid):
        old = self.cfg["menu_panel"]
        self.cfg["menu_panel"] = pid
        for x in {old, pid}:
            p = self.panel(x)
            if p:
                p.build()
                p.reshape()
        self.save()

    def set_panel_option(self, pid, key, value):
        self.panel_cfg(pid)[key] = value
        p = self.panel(pid)
        if p:
            p.build()
            p.reshape()
        self.save()

    def relayout(self, pid=None):
        for p in self.panels:
            if pid is None or p.pid == pid:
                p.layout()

    # совместимость с настройками: плитки в «редактируемой» панели
    def tile_enabled(self, key, pid=None):
        pc = self.panel_cfg(pid or self.edit_pid)
        return bool(pc) and key in pc["tiles"]

    def set_tile(self, key, on, meta=None, pid=None):
        if meta:
            self.cfg["sensor_meta"][key] = meta
        pc = self.panel_cfg(pid or self.edit_pid)
        if pc is None:
            return
        if on and key not in pc["tiles"]:
            pc["tiles"].append(key)
        elif not on and key in pc["tiles"]:
            pc["tiles"].remove(key)
        self.relayout(pc["id"])
        self.save()

    def apply_theme(self, name, style=""):
        self.cfg["theme"], self.cfg["graph_style"] = name, style
        set_theme(name, style)
        self.save()
        for p in self.panels:
            p.build()
            p.reshape()
        if self.launcher.visible():
            self.launcher.show(self.menu_panel.win if self.menu_panel else None, animate=False)
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

    # ---------- меню панели ----------
    def make_vars(self):
        self.v_top = tk.BooleanVar(value=self.cfg["topmost"])
        self.v_lock = tk.BooleanVar(value=self.cfg["locked"])
        self.v_auto = tk.BooleanVar(value=autostart_enabled())
        self.v_icons_hidden = tk.BooleanVar(value=not desktop.icons_visible())
        self.v_alpha = tk.DoubleVar(value=self.cfg["alpha"])

    def show_panel_menu(self, panel, e):
        self.v_icons_hidden.set(not desktop.icons_visible())
        pc, pid = panel.cfg, panel.pid
        m = tk.Menu(self.root, tearoff=0)
        self._menu_vars = []

        def var(value, cls=tk.BooleanVar):
            v = cls(value=value)
            self._menu_vars.append(v)
            return v

        m.add_command(label="Приложения", command=lambda: self.launcher.show(self.menu_panel.win))
        m.add_separator()
        tiles = tk.Menu(m, tearoff=0)
        for key, (title, *_r) in TILES.items():
            v = var(key in pc["tiles"])
            tiles.add_checkbutton(label=title, variable=v,
                                  command=lambda k=key, v=v: self.set_tile(k, v.get(), pid=pid))
            if key == "gpu_card":
                tiles.add_separator()
        tiles.add_separator()
        tiles.add_command(label="Все датчики…", command=lambda: self.open_sensors_for(pid))
        m.add_cascade(label="Плитки этой панели", menu=tiles)
        cols = tk.Menu(m, tearoff=0)
        vc = var(panel.columns, tk.IntVar)
        for n in (1, 2, 3, 4):
            cols.add_radiobutton(label=str(n), value=n, variable=vc,
                                 command=lambda: self.set_panel_option(pid, "columns", vc.get()))
        m.add_cascade(label="Колонки", menu=cols)
        vh = var(pc.get("header", True))
        m.add_checkbutton(label="Заголовок", variable=vh,
                          command=lambda: self.set_panel_option(pid, "header", vh.get()))
        vm = var(self.cfg["menu_panel"] == pid)
        m.add_checkbutton(label="Кнопка приложений на этой панели", variable=vm,
                          command=lambda: self.set_menu_panel(pid))
        m.add_separator()
        m.add_command(label="Новая панель", command=self.add_panel)
        if len(self.panels) > 1:
            m.add_command(label="Удалить эту панель", command=lambda: self.confirm_remove(pid))
        m.add_separator()
        m.add_checkbutton(label="Закрепить все панели", variable=self.v_lock, command=self.toggle_lock)
        m.add_checkbutton(label="Поверх всех окон", variable=self.v_top, command=self.toggle_top)
        m.add_checkbutton(label="Скрыть иконки рабочего стола", variable=self.v_icons_hidden,
                          command=lambda: self.set_desktop_icons_hidden(self.v_icons_hidden.get()))
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
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def open_sensors_for(self, pid):
        self.edit_pid = pid
        self.settings.show("sensors")

    def confirm_remove(self, pid):
        name = self.panel_cfg(pid).get("name", "панель")
        if messagebox.askyesno("Удалить панель", f"Удалить «{name}»?"):
            self.remove_panel(pid)

    def ask_new_panel(self, parent=None):
        name = simpledialog.askstring("Новая панель", "Название панели:", parent=parent,
                                      initialvalue=f"Панель {len(self.cfg['panels']) + 1}")
        if name:
            return self.add_panel(name.strip())
        return None

    def toggle_lock(self):
        self.cfg["locked"] = self.v_lock.get()
        for p in self.panels:
            p.render_header()
        self.save()

    def toggle_top(self):
        self.cfg["topmost"] = self.v_top.get()
        for p in self.panels:
            p.win.attributes("-topmost", self.cfg["topmost"])
        self.save()

    def set_desktop_icons_hidden(self, hidden):
        desktop.set_icons_visible(not hidden)
        self.v_icons_hidden.set(hidden)

    def set_alpha(self, a):
        self.cfg["alpha"] = round(a, 3)
        self.v_alpha.set(self.cfg["alpha"])
        for p in self.panels:
            p.win.attributes("-alpha", self.cfg["alpha"])
        self.save()

    def set_launcher_alpha(self, a):
        self.cfg["launcher_alpha"] = round(a, 3)
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
        self.tray_menu = TrayMenu(self.root, self.s)
        self._version = updater.version()
        self.tray = TrayIcon("SysMon Widget", lambda: self.cmds.put("toggle"),
                             lambda x, y: self.cmds.put(("tray_menu", x, y)))
        self.tray.start()

    def hidden(self):
        return not self.panels or self.panels[0].win.state() == "withdrawn"

    def show_tray_menu(self, x, y):
        go = lambda name: (lambda: self.cmds.put(name))
        items = [
            ("show", "Показать панели" if self.hidden() else "Скрыть панели", go("toggle")),
            ("apps", "Приложения", go("launcher")),
            ("settings", "Настройки", go("settings")),
            ("look", "Оформление", lambda: self.settings.show("look")),
        ]
        if self.cfg["naby_enabled"]:
            items += [("-",), ("music", "NabyScape: настройки", go("naby_settings")),
                      ("restart", "NabyScape: перезапустить", go("naby_restart"))]
        items += [("-",), ("update", "Проверить обновления", go("update")),
                  ("info", f"SysMon Widget {self._version}"), ("-",),
                  ("exit", "Выход", go("quit"))]
        self.tray_menu.show(x, y, items)

    def poll_cmds(self):
        try:
            while True:
                cmd = self.cmds.get_nowait()
                if cmd == "toggle":
                    self.show() if self.hidden() else self.hide()
                elif cmd == "launcher":
                    self.launcher.toggle()
                elif cmd == "settings":
                    self.settings.show()
                elif isinstance(cmd, tuple) and cmd[0] == "tray_menu":
                    self.show_tray_menu(cmd[1], cmd[2])
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
        for p in self.panels:
            p.win.withdraw()

    def show(self):
        for p in self.panels:
            p.win.deiconify()
            p.win.attributes("-topmost", self.cfg["topmost"])
            p.reshape()

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
        for p in self.panels:
            print(p.cfg.get("name"), {k: (t.main if t.kind != "card" else (t.card or {}).get("ring_text"))
                                      for k, t in p.tiles.items()})
        lhm, ok = self.sensors.get_lhm()
        print("lhm ok:", ok, "sensors:", len(lhm), "| uptime:", self.uptime_text)
        print("hotkey:", self.hotkey_text(), "registered:", self.hotkey_ok)
        self.quit(stop_sensors=False)

    # ---------- данные ----------
    def collect(self, keys):
        """Метрики для всех нужных плиток: key -> dict или None."""
        now = time.monotonic()
        dt = max(now - self.last_t, 1e-3)
        self.last_t = now
        sen = self.sensors.get()
        lhm, ok = self.sensors.get_lhm()
        off = "датчики выкл." if not self.cfg["lhm_enabled"] else "ожидание…"
        M = {}

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
        gb = 1024 ** 3

        M["cpu"] = {"values": [cpu], "main": f"{cpu:.0f}%", "sub": f"{len(per)} потоков"}
        M["cores"] = {"bars": per, "main": f"макс {max(per):.0f}%", "sub": f"{len(per)} потоков"}
        if "cpu_temp" in sen:
            v = sen["cpu_temp"]
            M["cpu_temp"] = {"values": [v], "main": f"{v:.0f} °C", "color": temp_color(v)}
        else:
            M["cpu_temp"] = {"main": off, "color": T["muted"]}
        M["ram"] = {"values": [vm.percent], "main": f"{vm.percent:.0f}%",
                    "sub": f"{vm.used / gb:.1f} / {vm.total / gb:.0f} ГБ"}
        if "gpu" in sen:
            M["gpu"] = {"values": [sen["gpu"]], "main": f"{sen['gpu']:.0f}%", "sub": sen.get("gpu_name", "")}
        if "gpu_temp" in sen:
            v = sen["gpu_temp"]
            M["gpu_temp"] = {"values": [v], "main": f"{v:.0f} °C", "color": temp_color(v)}
        if "vram_used" in sen and sen.get("vram_total"):
            pct = sen["vram_used"] / sen["vram_total"] * 100
            M["vram"] = {"values": [pct], "main": f"{pct:.0f}%",
                         "sub": f"{sen['vram_used'] / 1024:.1f} / {sen['vram_total'] / 1024:.0f} ГБ"}
        if "gpu_pwr" in sen:
            M["gpu_pwr"] = {"values": [sen["gpu_pwr"]], "main": f"{sen['gpu_pwr']:.0f} Вт"}
        M["disk"] = {"values": [rd, wr], "main": f"↓ {fmt_rate(rd)}", "sub": f"запись {fmt_rate(wr)}"}
        M["net"] = {"values": [down, up], "main": f"↓ {fmt_rate(down)}", "sub": f"↑ {fmt_rate(up)}"}
        if "cpu_card" in keys:
            M["cpu_card"] = cpu_details(lhm if ok else [], per, cpu)
        if "gpu_card" in keys:
            M["gpu_card"] = gpu_details(lhm if ok else [], sen)

        by_id = {x["id"]: x for x in lhm} if ok else {}
        for key in keys:
            if key.startswith("lhm:"):
                x = by_id.get(key[4:])
                if x:
                    v = x["value"]
                    M[key] = {"values": [v], "main": fmt_value(x["type"], v),
                              "color": temp_color(v) if x["type"] == "Temperature" else None}
                else:
                    M[key] = {"main": "нет данных" if ok else off, "color": T["muted"]}
        return M

    def tick(self):
        keys = {k for p in self.cfg["panels"] for k in p["tiles"]}
        self.uptime_text = fmt_uptime(time.time() - psutil.boot_time())
        try:
            metrics = self.collect(keys)
            if not self.hidden():
                for p in self.panels:
                    p.update(metrics)
        finally:
            self.root.after(INTERVAL_MS, self.tick)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if single_instance() is None:
        sys.exit(0)
    Widget(selftest="--selftest" in sys.argv).run()
