"""SysMon Widget — полупрозрачные панели-виджеты для рабочего стола Windows.

Панелей может быть несколько, у каждой свой набор плиток (включая большие «Процессор» и
«Видеокарта»). ЛКМ + перетаскивание — двигать, ПКМ — меню панели.
Кнопка с точками (в выбранной панели) или горячая клавиша — панель приложений.
Иконка в трее (ПКМ): показать/скрыть, приложения, настройки, обновления, выход.
"""
import sys

if sys.platform != "win32":  # Linux: тот же виджет на GTK4 + layer-shell (папка linux/)
    from linux.remote import forward
    code = forward(sys.argv)  # уже запущен — просто передаём ему команду
    if code is None:
        from linux.app import main
        code = main()
    sys.exit(code)

import copy
import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import uuid
from tkinter import messagebox, simpledialog

import psutil

import desktop
import deskwatch
import nabyscape
import sensorsvc
import translucenttb
import updater
from config import APP_NAME, autostart_enabled, load_cfg, save_cfg, set_autostart
from hotkey import HotkeyThread, hotkey_label
from launcher import Launcher
from panel import LAST_CARD, SCALES, TILES, Panel
from paths import APP_DIR, widget_cmd
from sensors import Sensors, cpu_details, disk_details, fmt_rate, fmt_value, gpu_details
from settings import SettingsWindow
from theme import T, set_theme
from tray import TrayIcon
from traymenu import TrayMenu
from weather import Weather

INTERVAL_MS = 1000
WATCH_MS = 300          # опрос рабочего стола: Win+D, полноэкранные приложения
ALERT_AFTER = 5         # сек подряд выше порога — уведомление о перегреве
ALERT_EVERY = 300       # не чаще, сек
WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
          "октября", "ноября", "декабря")

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
        self._lhm_watch = False        # следить ли за процессом датчиков (он уже запускался без UAC)
        self._lhm_retry = time.monotonic()
        self._user_hidden = False      # панели скрыты пользователем (в трей)
        self._fs_hidden = set()        # id панелей, спрятанных на время полноэкранного приложения
        self._desk_raised = False      # панели подняты над рабочим столом после Win+D
        self._alerts = {}              # датчик -> [секунд подряд выше порога, время последнего уведомления]
        self._disks = (0.0, None)      # (когда считали, карточка дисков)

        self.root = root = tk.Tk()
        # Tcl уже загружен; не передаём пути своей сборки запускаемым программам
        for var in ("TCL_LIBRARY", "TK_LIBRARY"):
            os.environ.pop(var, None)
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
        self.weather = Weather(lambda: self.cfg.get("weather_city", ""),
                               lambda: any("weather" in p["tiles"] for p in self.cfg["panels"]))
        self.weather.start()

        self.build_panels()
        self.hotkey_ok = self.apply_hotkey()
        self.start_tray()
        root.after(200, self.tick)
        root.after(1200, self.launcher.prewarm)
        root.after(50, self.poll_cmds)
        root.after(1500, self.watch_desktop)
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

    # ---------- раскладки (профили) и перенос настроек ----------
    def save_profile(self, name):
        self.cfg["profiles"][name] = {"panels": copy.deepcopy(self.cfg["panels"]),
                                      "menu_panel": self.cfg["menu_panel"]}
        self.save()

    def delete_profile(self, name):
        self.cfg["profiles"].pop(name, None)
        self.save()

    def apply_profile(self, name):
        pr = self.cfg["profiles"].get(name)
        if not pr or not pr.get("panels"):
            return
        self.cfg["panels"] = copy.deepcopy(pr["panels"])
        self.cfg["menu_panel"] = pr.get("menu_panel")
        migrate_panels(self.cfg)
        self.edit_pid = self.cfg["panels"][0]["id"]
        self._fs_hidden.clear()
        self.build_panels()
        if self._user_hidden:
            self.hide()
        self.save()

    def ask_save_profile(self, parent=None):
        name = simpledialog.askstring("Сохранить раскладку", "Название раскладки (панели, плитки и их места):",
                                      parent=parent, initialvalue=f"Раскладка {len(self.cfg['profiles']) + 1}")
        if name and name.strip():
            self.save_profile(name.strip())

    def export_settings(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, indent=2, ensure_ascii=False)

    def import_settings(self, path):
        """Заменить настройки файлом и перезапустить виджет. → текст ошибки или None."""
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, ValueError) as ex:
            return f"Не удалось прочитать файл: {ex}"
        if not isinstance(data, dict) or not isinstance(data.get("panels"), list) or not data["panels"]:
            return "Это не файл настроек SysMon Widget."
        save_cfg(data)
        subprocess.Popen(widget_cmd("--restart"), cwd=APP_DIR, creationflags=0x08000000)
        self.root.after(50, lambda: self.quit(stop_sensors=False))
        return None

    # ---------- рабочий стол: Win+D и полноэкранные приложения ----------
    def watch_desktop(self):
        try:
            if not self._user_hidden and self.panels:
                self._hide_for_fullscreen()
                self._stay_on_desktop()
        except Exception:
            pass
        self.root.after(WATCH_MS, self.watch_desktop)

    def _stay_on_desktop(self):
        """«Показать рабочий стол» (Win+D) поднимает окно рабочего стола над панелями — возвращаем их
        наверх. Когда пользователь уходит с рабочего стола, убираем панели под активное окно."""
        if self.cfg["topmost"]:
            return
        wins = [deskwatch.top_hwnd(p.win) for p in self.panels if p.pid not in self._fs_hidden]
        desk = deskwatch.desktop_window()
        if not wins or not desk:
            return
        if deskwatch.is_above(desk, wins[0]):
            for h in wins:
                deskwatch.raise_quietly(h)
            self._desk_raised = True
        elif self._desk_raised:
            fg = deskwatch.foreground_app()
            if fg:
                for h in wins:
                    deskwatch.put_below(h, fg)
                self._desk_raised = False

    def _hide_for_fullscreen(self):
        """Панели «поверх всех окон» не должны лежать на полноэкранной игре: прячем те, что на её мониторе."""
        mon = None
        if self.cfg["topmost"] and self.cfg.get("hide_fullscreen", True):
            mon = deskwatch.fullscreen_monitor()
        for p in self.panels:
            hide = mon is not None and deskwatch.monitor_of(deskwatch.top_hwnd(p.win)) == mon
            if hide and p.pid not in self._fs_hidden:
                self._fs_hidden.add(p.pid)
                p.win.withdraw()
            elif not hide and p.pid in self._fs_hidden:
                self._fs_hidden.discard(p.pid)
                p.win.deiconify()
                p.win.attributes("-topmost", self.cfg["topmost"])
                p.reshape()

    # ---------- предупреждения о перегреве ----------
    def check_alerts(self, metrics):
        now = time.monotonic()
        for key, name, opt in (("cpu_temp", "Процессор", "alert_cpu"), ("gpu_temp", "Видеокарта", "alert_gpu")):
            limit = self.cfg.get(opt) or 0
            vals = (metrics.get(key) or {}).get("values")
            st = self._alerts.setdefault(key, [0, -1e9])
            if limit and vals and vals[0] >= limit:
                st[0] += 1
                if st[0] >= ALERT_AFTER and now - st[1] > ALERT_EVERY and self.tray:
                    st[1] = now
                    self.tray.notify("Перегрев", f"{name}: {vals[0]:.0f} °C (порог {limit} °C)")
            else:
                st[0] = 0

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

    def show_panel_menu(self, panel, e=None, page=None, pos=None):
        """Меню панели (ПКМ) — в том же стиле, что и меню значка в трее. page — страница подменю."""
        if panel not in self.panels:
            return
        pc, pid = panel.cfg, panel.pid
        pos = pos or (e.x_root, e.y_root)
        keep, sub = {"keep": True}, {"keep": True, "sub": True}

        def go(pg=None):  # перерисовать меню на том же месте (другая страница или новые галочки)
            return lambda: self.show_panel_menu(panel, page=pg, pos=pos)

        def then(fn, pg=None):  # выполнить и оставить меню открытым
            return lambda: (fn(), go(pg)())

        def check(on):
            return {"keep": True, "check": bool(on)}

        back = ("back", "Назад", go(), keep)
        title = pc.get("name", "Панель")
        if page == "tiles":
            title = f"Плитки · {title}"
            items = [back, ("-",)]
            for key, (name, *_r) in TILES.items():
                on = key in pc["tiles"]
                items.append(("", name, then(lambda k=key, on=on: self.set_tile(k, not on, pid=pid), "tiles"),
                              check(on)))
                if key == LAST_CARD:
                    items.append(("-",))
            items += [("-",), ("sensors", "Все датчики…", lambda: self.open_sensors_for(pid)),
                      ("info", "Shift + перетаскивание — переставить плитки")]
        elif page == "cols":
            title = f"Колонки · {title}"
            items = [back, ("-",)]
            for n in (1, 2, 3, 4):
                items.append(("", str(n), then(lambda n=n: self.set_panel_option(pid, "columns", n), "cols"),
                              check(panel.columns == n)))
        elif page == "scale":
            title = f"Размер · {title}"
            items = [back, ("-",)]
            for k in SCALES:
                items.append(("", f"{k:.0%}", then(lambda k=k: self.set_panel_option(pid, "scale", k), "scale"),
                              check(abs(float(pc.get("scale", 1.0)) - k) < 0.01)))
        elif page == "profiles":
            title = "Раскладки"
            items = [back, ("-",)]
            for name in self.cfg["profiles"]:
                items.append(("cols", name, lambda n=name: self.apply_profile(n)))
            if not self.cfg["profiles"]:
                items.append(("info", "Пока нет сохранённых раскладок"))
            items += [("-",), ("add", "Сохранить текущую…", self.ask_save_profile),
                      ("info", "Удаление, экспорт и импорт — Настройки → Панели")]
        elif page == "naby":
            title = "NabyScape"
            items = [back, ("-",),
                     ("settings", "Открыть настройки", lambda: self.naby_action("settings")),
                     ("restart", "Перезапустить", lambda: self.naby_action("restart")),
                     ("stop", "Остановить", lambda: self.naby_action("stop")),
                     ("-",), ("music", "Интеграция…", lambda: self.settings.show("naby"))]
        else:
            header = pc.get("header", True)
            icons_hidden = not desktop.icons_visible()
            items = [
                ("apps", "Приложения", lambda: self.launcher.show(self.menu_panel.win)),
                ("-",),
                ("tiles", "Плитки этой панели", go("tiles"), sub),
                ("cols", f"Колонки: {panel.columns}", go("cols"), sub),
                ("", "Заголовок", then(lambda: self.set_panel_option(pid, "header", not header)), check(header)),
                ("", "Кнопка приложений на этой панели", then(lambda: self.set_menu_panel(pid)),
                 check(self.cfg["menu_panel"] == pid)),
                ("scale", f"Размер: {float(pc.get('scale', 1.0)):.0%}", go("scale"), sub),
                ("", "Закрепить эту панель",
                 then(lambda: self.set_panel_option(pid, "locked", not pc.get("locked"))),
                 check(pc.get("locked"))),
                ("-",),
                ("add", "Новая панель", self.add_panel),
            ]
            if len(self.panels) > 1:
                items.append(("delete", "Удалить эту панель", lambda: self.confirm_remove(pid)))
            items += [
                ("-",),
                ("", "Закрепить все панели",
                 then(lambda: (self.v_lock.set(not self.cfg["locked"]), self.toggle_lock())),
                 check(self.cfg["locked"])),
                ("", "Поверх всех окон",
                 then(lambda: (self.v_top.set(not self.cfg["topmost"]), self.toggle_top())),
                 check(self.cfg["topmost"])),
                ("", "Скрыть иконки рабочего стола",
                 then(lambda: self.set_desktop_icons_hidden(not icons_hidden)), check(icons_hidden)),
                ("cols", "Раскладки", go("profiles"), sub),
                ("look", "Оформление…", lambda: self.settings.show("look")),
            ]
            if self.cfg["naby_enabled"]:
                items.append(("music", "NabyScape", go("naby"), sub))
            items += [("-",),
                      ("settings", "Настройки…", lambda: self.settings.show()),
                      ("tray", "Скрыть в трей", self.hide),
                      ("exit", "Выход", self.quit)]
        self.tray_menu.show(pos[0], pos[1], items, title=title, at_cursor=True, animate=e is not None)

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

    def watch_sensors(self):
        """Процесс датчиков пропал (не успел остановиться при быстром перезапуске виджета, упал) —
        поднимаем его снова, без запроса UAC."""
        if self.sensors.get_lhm()[1]:
            self._lhm_watch = True
            self._lhm_retry = time.monotonic()
        elif self.cfg["lhm_enabled"] and self._lhm_watch and time.monotonic() - self._lhm_retry > 20:
            self._lhm_retry = time.monotonic()
            self.start_sensors()

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
            self._lhm_watch = True
            self._lhm_retry = time.monotonic()
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
            if newer and nabyscape.is_russian_build():
                self.naby_msg = (f"Вышел NabyScape {tag} (на английском). Русская версия обновится "
                                 f"вместе со следующим обновлением SysMon Widget.")
            elif newer:
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
        return self._user_hidden

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
                elif isinstance(cmd, tuple) and cmd[0] == "update_applied":
                    if self.on_update_applied(*cmd[1:]):
                        return
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
            # установщик качается в фоне, иначе панели замирают на всё время загрузки
            self._updating = True
            threading.Thread(target=lambda: self.cmds.put(("update_applied", *updater.apply())),
                             daemon=True).start()

    def on_update_applied(self, ok, out):
        self._updating = False
        if ok:
            updater.restart()
            self.quit(stop_sensors=False)
            return True  # приложение закрыто — опрос команд прекращаем
        messagebox.showerror("Обновление не удалось", out)
        return False

    def hide(self):
        self._user_hidden = True
        for p in self.panels:
            p.win.withdraw()

    def show(self):
        self._user_hidden = False
        self._fs_hidden.clear()
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
        if "disks_card" in keys:
            if now - self._disks[0] > 15 or self._disks[1] is None:  # объём дисков меняется медленно
                self._disks = (now, disk_details(lhm if ok else []))
            M["disks_card"] = self._disks[1]
        self.sensors.want_procs = "procs_card" in keys
        if "procs_card" in keys:
            pr = self.sensors.get_procs()
            M["procs_card"] = {"device": f"процессов: {pr['count']}" if pr else "считаю…", "ring": cpu / 100,
                               "ring_text": f"{cpu:.0f}%", "rows": pr["rows"] if pr else []}
        if "clock" in keys:
            t = time.localtime()
            M["clock"] = {"main": time.strftime("%H:%M", t),
                          "sub": f"{WEEKDAYS[t.tm_wday]}, {t.tm_mday} {MONTHS[t.tm_mon - 1]}"}
        if "weather" in keys:
            M["weather"] = self.weather.get()
        if "battery" in keys:
            bat = psutil.sensors_battery()
            if bat is None:
                M["battery"] = {"main": "нет батареи", "color": T["muted"]}
            else:
                left = bat.secsleft
                sub = ("заряжается" if bat.percent < 100 else "заряжена") if bat.power_plugged else (
                    f"осталось {left // 3600} ч {left % 3600 // 60:02d} мин" if left and left > 0 else "от батареи")
                M["battery"] = {"values": [bat.percent], "main": f"{bat.percent:.0f}%", "sub": sub,
                                "color": T["hot"] if bat.percent <= 15 and not bat.power_plugged else None}

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
            self.watch_sensors()
            self.check_alerts(metrics)
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
