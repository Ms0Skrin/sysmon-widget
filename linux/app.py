"""SysMon Widget для Linux — полупрозрачные панели-виджеты на рабочем столе (Wayland, layer-shell).

Панелей может быть несколько, у каждой свой набор плиток. ЛКМ + перетаскивание — двигать,
ПКМ — меню панели. Кнопка с точками или команда `sysmon-widget --launcher` — панель приложений.
Значок в трее: ЛКМ — показать/скрыть, ПКМ — меню.

Команды работающему виджету: --launcher, --settings, --toggle, --show, --hide, --quit.
"""
import ctypes
import ctypes.util

# gtk4-layer-shell должна загрузиться раньше libwayland-client, то есть до GTK
try:
    ctypes.CDLL(ctypes.util.find_library("gtk4-layer-shell") or "libgtk4-layer-shell.so.0")
except OSError:
    raise SystemExit("Нужна библиотека gtk4-layer-shell (Arch: pacman -S gtk4-layer-shell, "
                     "Debian/Ubuntu: apt install libgtk4-layer-shell0 gir1.2-gtk4layershell-1.0).")

import copy  # noqa: E402
import glob  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import signal  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402

import gi  # noqa: E402
import psutil  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as LS  # noqa: E402

import updater  # noqa: E402
from sensors import cpu_details, fmt_rate, fmt_value  # noqa: E402
from theme import T, set_theme  # noqa: E402
from weather import Weather  # noqa: E402

from .config import APP_ID, CONFIG_HOME, load_cfg, save_cfg, widget_cmd  # noqa: E402
from .gtkutil import apply_theme_css, ask_string, confirm, message  # noqa: E402
from .launcher import Launcher  # noqa: E402
from .panel import LAST_CARD, SCALES, TILES, Panel  # noqa: E402
from .sensors import Sensors, disk_details, gpu_details  # noqa: E402
from .settings import SettingsWindow  # noqa: E402
from .tray import TrayIcon  # noqa: E402

INTERVAL_MS = 1000
ALERT_AFTER = 5         # сек подряд выше порога — уведомление о перегреве
ALERT_EVERY = 300       # не чаще, сек
WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
          "октября", "ноября", "декабря")


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
                          "columns": cfg.get("columns", 2), "x": None, "y": None, "header": True}]
    ids = [p["id"] for p in cfg["panels"]]
    if cfg.get("menu_panel") not in ids:
        cfg["menu_panel"] = ids[0]
    for p in cfg["panels"]:
        p["tiles"] = [k for k in p.get("tiles", []) if k in TILES or k in cfg["sensor_meta"]]


def find_hotkey():
    """Сочетание клавиш, на которое в композиторе назначена панель приложений (niri, Hyprland, Sway)."""
    pats = ((os.path.join(CONFIG_HOME, "niri", "*.kdl"), r"^\s*([\w+]+)\s*(?:[\w-]+=\S+\s*)*\{[^}\n]*--launcher"),
            (os.path.join(CONFIG_HOME, "hypr", "*.conf"), r"^\s*bind\w*\s*=\s*([^,]*,[^,]*),[^\n]*--launcher"),
            (os.path.join(CONFIG_HOME, "sway", "config"), r"^\s*bindsym\s+(\S+)[^\n]*--launcher"))
    for pattern, rx in pats:
        for path in sorted(glob.glob(pattern)):
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        m = re.match(rx, line)
                        if m and "sysmon" in line and not line.lstrip().startswith(("//", "#")):
                            return re.sub(r"\s*,\s*", "+", m.group(1).strip()).strip("+")
            except OSError:
                pass
    return ""


class Widget:
    """Приложение: трей, датчики, настройки — и набор панелей."""

    def __init__(self, gapp, selftest=False):
        self.gapp = gapp
        self.cfg = load_cfg(set(TILES))
        migrate_panels(self.cfg)
        set_theme(self.cfg["theme"], self.cfg["graph_style"])
        apply_theme_css()
        self.s = 1.0
        self.display = Gdk.Display.get_default()
        self._updating = False
        self.hist = {}
        self.panels = []
        self.work = {}                 # монитор (connector) -> размер рабочей области
        self.edit_pid = self.cfg["panels"][0]["id"]  # какую панель правит вкладка «Плитки и датчики»
        self.uptime_text = fmt_uptime(time.time() - psutil.boot_time())
        self._user_hidden = False      # панели скрыты пользователем (в трей)
        self._alerts = {}              # датчик -> [секунд подряд выше порога, время последнего уведомления]
        self._disks = (0.0, None)      # (когда считали, карточка дисков)
        self._ready = False
        self._selftest = selftest

        psutil.cpu_percent(None)
        psutil.cpu_percent(None, percpu=True)
        self.last_t = time.monotonic()
        self.last_disk = psutil.disk_io_counters()
        self.last_net = psutil.net_io_counters()
        self.sensors = Sensors()
        self.sensors.start()
        self.weather = Weather(lambda: self.cfg.get("weather_city", ""),
                               lambda: any("weather" in p["tiles"] for p in self.cfg["panels"]))
        self.weather.data["sub"] = "укажи город: Настройки → Панели"
        self.weather.start()

        self.launcher = Launcher(self)
        self.settings = SettingsWindow(self)
        self._version = updater.version()
        self.tray = TrayIcon("SysMon Widget", self.toggle, self.tray_items)
        self.tray.start()

        self.display.get_monitors().connect("items-changed", lambda *_a: self.probe(self.replace_panels))
        self.probe(self._start)
        GLib.timeout_add(INTERVAL_MS, self.tick)
        if selftest:
            GLib.timeout_add(6000, self.selftest_dump)

    def _start(self):
        self.build_panels()
        self._ready = True
        self.tick(repeat=False)

    # ---------- мониторы ----------
    def monitors(self):
        model = self.display.get_monitors()
        return [model.get_item(i) for i in range(model.get_n_items())]

    def monitor_for(self, connector):
        """Монитор панели; если его отключили или он не задан — самый большой."""
        mons = self.monitors()
        for m in mons:
            if connector and m.get_connector() == connector:
                return m
        return max(mons, key=lambda m: m.get_geometry().width * m.get_geometry().height, default=None)

    def workarea(self, monitor):
        """Размер области монитора, свободной от системных панелей."""
        if monitor is None:
            return 1920, 1080
        geo = monitor.get_geometry()
        return self.work.get(monitor.get_connector(), (geo.width, geo.height))

    def probe(self, then=None):
        """Узнать рабочие области: на миг растягиваем прозрачное окно на весь монитор — композитор
        отдаёт ему место без системных панелей. Другого способа спросить об этом у Wayland нет."""
        probes = []
        for m in self.monitors():
            win = Gtk.Window(application=self.gapp, decorated=False)
            win.add_css_class("sysmon-probe")
            LS.init_for_window(win)
            LS.set_namespace(win, "sysmon-probe")
            LS.set_layer(win, LS.Layer.BACKGROUND)
            for edge in (LS.Edge.LEFT, LS.Edge.RIGHT, LS.Edge.TOP, LS.Edge.BOTTOM):
                LS.set_anchor(win, edge, True)
            LS.set_keyboard_mode(win, LS.KeyboardMode.NONE)
            LS.set_monitor(win, m)
            win.set_can_target(False)
            win.present()
            probes.append((m, win))

        def done():
            for m, win in probes:
                w, h = win.get_width(), win.get_height()
                geo = m.get_geometry()
                if 200 < w <= geo.width and 200 < h <= geo.height:
                    self.work[m.get_connector()] = (w, h)
                win.destroy()
            if then:
                then()
            return False

        GLib.timeout_add(300, done)

    # ---------- панели ----------
    def build_panels(self):
        for p in self.panels:
            p.destroy()
        self.panels = [Panel(self, pc) for pc in self.cfg["panels"]]
        for p in self.panels:
            p.sync_shape()
        if self._user_hidden:
            self.hide()

    def replace_panels(self):
        """Мониторы изменились: панели отключённого монитора переезжают на оставшийся."""
        for p in self.panels:
            mon = self.monitor_for(p.cfg.get("output"))
            if mon != p.monitor and mon is not None:
                p.monitor = mon
                LS.set_monitor(p.win, mon)
            p.place()
        for p in self.panels:
            p.sync_shape()

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
        self.tick(repeat=False)
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
        for p in self.panels:
            p.sync_shape()
        self.save()

    def rename_panel(self, pid, name):
        self.panel_cfg(pid)["name"] = name
        self.relayout(pid)
        self.save()

    def _rebuild(self, *pids):
        for pid in pids:
            p = self.panel(pid)
            if p:
                p.build()
        self.tick(repeat=False)

    def set_menu_panel(self, pid):
        old = self.cfg["menu_panel"]
        self.cfg["menu_panel"] = pid
        self._rebuild(*{old, pid})
        self.save()

    def set_panel_option(self, pid, key, value):
        self.panel_cfg(pid)[key] = value
        self._rebuild(pid)
        self.save()

    def relayout(self, pid=None):
        for p in self.panels:
            if pid is None or p.pid == pid:
                p.layout()
        self.tick(repeat=False)

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
        apply_theme_css()
        self.save()
        self._rebuild(*[p.pid for p in self.panels])

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
        self.build_panels()
        self.tick(repeat=False)
        self.save()

    def ask_save_profile(self, parent=None, done=None):
        def ok(name):
            self.save_profile(name)
            if done:
                done()

        ask_string(parent, "Сохранить раскладку", "Название раскладки (панели, плитки и их места):",
                   f"Раскладка {len(self.cfg['profiles']) + 1}", ok)

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
        for p in data["panels"]:  # места панелей из Windows на другой системе не подходят
            if "output" not in p:
                p["x"] = p["y"] = None
        save_cfg(data)
        GLib.timeout_add(50, self.restart)
        return None

    # ---------- предупреждения о перегреве ----------
    def check_alerts(self, metrics):
        now = time.monotonic()
        for key, name, opt in (("cpu_temp", "Процессор", "alert_cpu"), ("gpu_temp", "Видеокарта", "alert_gpu")):
            limit = self.cfg.get(opt) or 0
            vals = (metrics.get(key) or {}).get("values")
            st = self._alerts.setdefault(key, [0, -1e9])
            if limit and vals and vals[0] >= limit:
                st[0] += 1
                if st[0] >= ALERT_AFTER and now - st[1] > ALERT_EVERY:
                    st[1] = now
                    self.tray.notify("Перегрев", f"{name}: {vals[0]:.0f} °C (порог {limit} °C)")
            else:
                st[0] = 0

    # ---------- общее ----------
    def save(self, *_):
        save_cfg(self.cfg)

    def hotkey_text(self):
        return find_hotkey()

    # ---------- меню панели ----------
    def show_panel_menu(self, panel, pos, page=None):
        """Меню панели (ПКМ). page — страница подменю."""
        if panel not in self.panels:
            return
        pc, pid = panel.cfg, panel.pid
        keep, sub = {"keep": True}, {"keep": True, "sub": True}

        def go(pg=None):  # перерисовать меню на том же месте (другая страница или новые галочки)
            return lambda: self.show_panel_menu(panel, pos, page=pg)

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
        else:
            header = pc.get("header", True)
            items = [
                ("apps", "Приложения", self.launcher.show),
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
            ]
            if len(self.monitors()) > 1:
                items.append(("monitor", "На другой монитор", panel.to_next_monitor))
            items += [("-",), ("add", "Новая панель", self.add_panel)]
            if len(self.panels) > 1:
                items.append(("delete", "Удалить эту панель", lambda: self.confirm_remove(pid)))
            items += [
                ("-",),
                ("", "Закрепить все панели", then(lambda: self.set_locked(not self.cfg["locked"])),
                 check(self.cfg["locked"])),
                ("", "Поверх всех окон", then(lambda: self.set_topmost(not self.cfg["topmost"])),
                 check(self.cfg["topmost"])),
                ("cols", "Раскладки", go("profiles"), sub),
                ("look", "Оформление…", lambda: self.settings.show("look")),
                ("-",),
                ("settings", "Настройки…", lambda: self.settings.show()),
                ("tray", "Скрыть в трей", self.hide),
                ("exit", "Выход", self.quit),
            ]
        panel.menu.show(panel.root, pos[0], pos[1], items, title=title)

    def open_sensors_for(self, pid):
        self.edit_pid = pid
        self.settings.show("sensors")

    def confirm_remove(self, pid):
        name = self.panel_cfg(pid).get("name", "панель")
        confirm(None, "Удалить панель", f"Удалить «{name}»?", lambda: self.remove_panel(pid))

    def ask_new_panel(self, parent=None, done=None):
        def ok(name):
            self.add_panel(name)
            if done:
                done()

        ask_string(parent, "Новая панель", "Название панели:", f"Панель {len(self.cfg['panels']) + 1}", ok)

    def set_locked(self, on):
        self.cfg["locked"] = bool(on)
        for p in self.panels:
            p.render_header()
        self.save()

    def set_topmost(self, on):
        self.cfg["topmost"] = bool(on)
        for p in self.panels:
            p.apply_layer()
        self.save()

    def set_alpha(self, a):
        self.cfg["alpha"] = round(a, 3)
        for p in self.panels:
            p.body.set_opacity(self.cfg["alpha"])
        self.save()

    def set_launcher_alpha(self, a):
        self.cfg["launcher_alpha"] = round(a, 3)
        self.launcher.set_alpha(self.cfg["launcher_alpha"])
        self.save()

    # ---------- трей ----------
    def hidden(self):
        return self._user_hidden

    def tray_items(self):
        return [
            ("Показать панели" if self.hidden() else "Скрыть панели", self.toggle),
            ("Приложения", self.launcher.toggle),
            ("Настройки", lambda: self.settings.show()),
            ("Оформление", lambda: self.settings.show("look")),
            ("-",),
            ("Проверить обновления", self.check_updates),
            (f"SysMon Widget {self._version}", None),
            ("-",),
            ("Выход", self.quit),
        ]

    def toggle(self):
        self.show() if self.hidden() else self.hide()

    def hide(self):
        self._user_hidden = True
        for p in self.panels:
            p.set_visible(False)

    def show(self):
        self._user_hidden = False
        for p in self.panels:
            p.set_visible(True)
        for p in self.panels:
            p.sync_shape()
        self.tick(repeat=False)

    def command(self, args, first):
        """Ключи командной строки — и при запуске, и от второй копии работающему виджету."""
        later = (lambda fn: GLib.timeout_add(900, lambda: (fn(), False)[1])) if first else (lambda fn: fn())
        if "--quit" in args:
            self.quit()
        elif "--launcher" in args:
            later(self.launcher.toggle)
        elif "--settings" in args:
            later(self.settings.show)
        elif "--toggle" in args and not first:
            self.toggle()
        elif "--hide" in args:
            later(self.hide)
        elif not first and "--selftest" not in args:
            self.show()  # запустили ещё раз — показываем панели

    # ---------- обновления ----------
    def check_updates(self):
        if self._updating:
            return
        self._updating = True
        threading.Thread(target=lambda: GLib.idle_add(self.on_update_result, *updater.check()),
                         daemon=True).start()

    def on_update_result(self, count, info):
        self._updating = False
        if count is None:
            message(self.settings.win, "Обновления", info)
        elif count == 0:
            message(self.settings.win, "Обновления", f"У вас последняя версия ({updater.version()}).")
        else:
            def go():
                self._updating = True
                threading.Thread(target=lambda: GLib.idle_add(self.on_update_applied, *updater.apply()),
                                 daemon=True).start()

            confirm(self.settings.win, "Доступно обновление",
                    f"Новых изменений: {count}\n\n{info}\n\nОбновить и перезапустить?", go)
        return False

    def on_update_applied(self, ok, out):
        self._updating = False
        if ok:
            self.restart()
        else:
            message(self.settings.win, "Обновление не удалось", out)
        return False

    def _cleanup(self):
        self.save()
        try:
            self.tray.stop()
        except Exception:
            pass
        self.sensors.stop()

    def restart(self):
        self._cleanup()
        os.execv(sys.executable, widget_cmd())

    def quit(self):
        self._cleanup()
        self.gapp.quit()

    def selftest_dump(self):
        for p in self.panels:
            print(p.cfg.get("name"), p.monitor.get_connector() if p.monitor else None, p.rect(),
                  {k: (t.main if t.kind != "card" else (t.card or {}).get("ring_text"))
                   for k, t in p.tiles.items()})
        lhm, ok = self.sensors.get_lhm()
        print("sensors ok:", ok, "count:", len(lhm), "| uptime:", self.uptime_text)
        print("workareas:", self.work, "| hotkey:", self.hotkey_text() or "—")
        self.quit()
        return False

    # ---------- данные ----------
    def collect(self, keys):
        """Метрики для всех нужных плиток: key -> dict или None."""
        now = time.monotonic()
        dt = max(now - self.last_t, 1e-3)
        self.last_t = now
        sen = self.sensors.get()
        lhm, ok = self.sensors.get_lhm()
        off = "ожидание…"
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
            M["cpu_temp"] = {"main": off if not ok else "нет датчика", "color": T["muted"]}
        used = vm.total - vm.available
        M["ram"] = {"values": [vm.percent], "main": f"{vm.percent:.0f}%",
                    "sub": f"{used / gb:.1f} / {vm.total / gb:.0f} ГБ"}
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

    def tick(self, repeat=True):
        """Раз в секунду: собрать показания и перерисовать плитки. repeat=False — внеочередная
        перерисовка после изменения настроек (без записи точки в графики)."""
        if not self._ready:
            return repeat
        try:
            if repeat or not hasattr(self, "_metrics"):
                keys = {k for p in self.cfg["panels"] for k in p["tiles"]}
                self.uptime_text = fmt_uptime(time.time() - psutil.boot_time())
                self._metrics = self.collect(keys)
                self.check_alerts(self._metrics)
                fresh = True
            else:
                fresh = False
            if not self.hidden():
                for p in self.panels:
                    p.update(self._metrics, push=fresh)
        except Exception:
            import traceback
            traceback.print_exc()
        return repeat


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    GLib.set_prgname(APP_ID)
    GLib.set_application_name("SysMon Widget")
    gapp = Gtk.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
    state = {}

    def on_command_line(app, cl):
        args = cl.get_arguments()[1:]
        first = "w" not in state
        if first:
            if "--quit" in args:
                return 0  # нечего закрывать
            state["w"] = Widget(app, selftest="--selftest" in args)
            app.hold()
        state["w"].command(args, first)
        return 0

    gapp.connect("command-line", on_command_line)
    for sig in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: ("w" in state and state["w"].quit(), False)[1])
    return gapp.run(argv)
