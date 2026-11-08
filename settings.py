"""Окно настроек: Основное · Оформление · Датчики · Приложения."""
import os
import random
import threading
import tkinter as tk
from collections import defaultdict

from PIL import ImageTk

import desktop
import nabyscape
import sensorsvc
import translucenttb
import winstyle
from config import autostart_enabled
from hotkey import MODIFIER_VKS, current_mods, hotkey_label
from icons import file_icon
from sensors import TYPE_NAMES, fmt_value
from theme import (GRAPH_STYLES, THEMES, T, alpha_to_pct, dark_titlebar, draw_series, font, is_dark,
                   pct_to_alpha)

TABS = [("general", "Основное"), ("look", "Оформление"), ("sensors", "Датчики"),
        ("apps", "Приложения"), ("naby", "NabyScape")]


class ScrollFrame(tk.Frame):
    """Прокручиваемая область (колесо мыши работает, пока курсор над ней)."""

    def __init__(self, master, **kw):
        super().__init__(master, bg=T["bg"], **kw)
        self.canvas = tk.Canvas(self, bg=T["bg"], highlightthickness=0)
        sb = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=T["bg"])
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw", tags="inner")
        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure("inner", width=e.width))
        wheel = lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units")
        self.bind("<Enter>", lambda _e: self.bind_all("<MouseWheel>", wheel))
        self.bind("<Leave>", lambda _e: self.unbind_all("<MouseWheel>"))


class SettingsWindow:
    def __init__(self, app):
        self.app = app
        self.win = None
        self.tab = "general"
        self.capturing = False
        self._imgs = []
        self._status_job = None

    # ---------- окно ----------
    def show(self, tab=None):
        if tab:
            self.tab = tab
        if self.win is not None and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
            self.switch(self.tab)
            return
        s = self.app.s
        w = self.win = tk.Toplevel(self.app.root, bg=T["bg"])
        w.title("Настройки виджета")
        w.attributes("-topmost", True)
        w.geometry(f"{int(600 * s)}x{int(640 * s)}")
        w.minsize(int(520 * s), int(480 * s))
        dark_titlebar(w, is_dark())
        w.protocol("WM_DELETE_WINDOW", self.close)
        w.bind("<KeyPress>", self.on_key)

        bar = tk.Frame(w, bg=T["bg"])
        bar.pack(fill="x", padx=int(14 * s), pady=(int(12 * s), 0))
        self.tab_btns = {}
        for key, title in TABS:
            b = tk.Label(bar, text=title, bg=T["bg"], fg=T["muted"], font=font(10, True),
                         padx=int(12 * s), pady=int(6 * s), cursor="hand2")
            b.pack(side="left")
            b.bind("<Button-1>", lambda _e, k=key: self.switch(k))
            self.tab_btns[key] = b
        tk.Frame(w, bg=T["panel"], height=max(1, int(1 * s))).pack(fill="x", padx=int(14 * s))

        self.content = tk.Frame(w, bg=T["bg"])
        self.content.pack(fill="both", expand=True, padx=int(18 * s), pady=int(10 * s))
        self.switch(self.tab)
        w.update_idletasks()
        x = (w.winfo_screenwidth() - w.winfo_width()) // 2
        y = (w.winfo_screenheight() - w.winfo_height()) // 3
        w.geometry(f"+{x}+{y}")
        w.focus_force()

    def close(self):
        if self.capturing:
            self.stop_capture(restore=True)
        self._cancel_status()
        if self.win is not None:
            self.win.destroy()
        self.win = None

    def reopen(self):
        """После смены темы — пересобрать окно в новых цветах на том же месте."""
        if self.win is None or not self.win.winfo_exists():
            return
        geo = self.win.geometry()
        self.close()
        self.show()
        self.win.geometry(geo)

    def switch(self, key):
        self.tab = key
        self._cancel_status()
        self.capturing = False
        for k, b in self.tab_btns.items():
            b.config(fg=T["accent"] if k == key else T["muted"],
                     bg=T["panel"] if k == key else T["bg"])
        for c in self.content.winfo_children():
            c.destroy()
        self._imgs = []
        getattr(self, f"tab_{key}")(self.content)

    # ---------- элементы ----------
    def section(self, parent, text, top=12):
        tk.Label(parent, text=text.upper(), bg=T["bg"], fg=T["muted"], font=font(8, True),
                 anchor="w").pack(fill="x", pady=(int(top * self.app.s), int(4 * self.app.s)))

    def button(self, parent, text, cmd, accent=False):
        s = self.app.s
        bg, fg = (T["accent"], T["bg"]) if accent else (T["panel"], T["fg"])
        b = tk.Label(parent, text=text, bg=bg, fg=fg, font=font(9, True),
                     padx=int(12 * s), pady=int(4 * s), cursor="hand2")
        b.bind("<Button-1>", lambda _e: cmd())
        if not accent:
            b.bind("<Enter>", lambda _e: b.config(bg=T["hover"]))
            b.bind("<Leave>", lambda _e: b.config(bg=T["panel"]))
        return b

    def check(self, parent, text, var, cmd, fg=None):
        cb = tk.Checkbutton(parent, text=text, variable=var, command=cmd, bg=T["bg"],
                            fg=fg or T["fg"], selectcolor=T["panel"], activebackground=T["bg"],
                            activeforeground=T["fg"], font=font(10), anchor="w", bd=0,
                            highlightthickness=0)
        cb.pack(fill="x", pady=int(1 * self.app.s))
        return cb

    def slider(self, parent, text, value, lo, hi, cmd, fmt=lambda v: f"{v}%"):
        s = self.app.s
        row = tk.Frame(parent, bg=T["bg"])
        row.pack(fill="x", pady=int(2 * s))
        tk.Label(row, text=text, bg=T["bg"], fg=T["fg"], font=font(10), width=22,
                 anchor="w").pack(side="left")
        val = tk.Label(row, text=fmt(value), bg=T["bg"], fg=T["accent"], font=font(10, True), width=6)
        val.pack(side="right")
        sc = tk.Scale(row, from_=lo, to=hi, orient="horizontal", showvalue=False, resolution=1,
                      bg=T["bg"], fg=T["fg"], troughcolor=T["panel"], activebackground=T["accent"],
                      highlightthickness=0, bd=0, sliderrelief="flat", sliderlength=int(18 * s),
                      width=int(10 * s))
        sc.set(value)
        sc.pack(side="left", fill="x", expand=True, padx=int(8 * s))

        def changed(v):
            val.config(text=fmt(int(float(v))))
            cmd(int(float(v)))

        sc.config(command=changed)

    # ---------- вкладка «Основное» ----------
    def tab_general(self, p):
        app, s = self.app, self.app.s
        self.section(p, "Меню приложений", top=4)
        row = tk.Frame(p, bg=T["bg"])
        row.pack(fill="x")
        tk.Label(row, text="Горячая клавиша:", bg=T["bg"], fg=T["fg"], font=font(10)).pack(side="left")
        self.hk_lbl = tk.Label(row, text="", bg=T["bg"], fg=T["accent"], font=font(10, True))
        self.hk_lbl.pack(side="left", padx=int(8 * s))
        self.button(row, "Отключить", self.clear_hotkey).pack(side="right")
        self.button(row, "Изменить", self.start_capture).pack(side="right", padx=int(6 * s))
        self.hk_msg = tk.Label(p, text="", bg=T["bg"], fg=T["muted"], font=font(8), anchor="w")
        self.hk_msg.pack(fill="x", pady=(int(3 * s), 0))
        self.refresh_hotkey()
        self.slider(p, "Прозрачность панели", alpha_to_pct(app.cfg["launcher_alpha"]), 0, 100,
                    lambda v: app.set_launcher_alpha(pct_to_alpha(v)))
        self.slider(p, "Размер значков", int(app.cfg.get("launcher_icon", 40)), 24, 96,
                    app.launcher.set_icon_size, fmt=lambda v: f"{v} px")
        self.slider(p, "Колонок в панели", int(app.cfg.get("launcher_cols", 0)), 0, 12,
                    app.launcher.set_cols, fmt=lambda v: "авто" if v == 0 else str(v))

        self.section(p, "Рабочий стол")
        app.v_icons_hidden.set(not desktop.icons_visible())
        self.check(p, "Скрыть иконки рабочего стола", app.v_icons_hidden,
                   lambda: app.set_desktop_icons_hidden(app.v_icons_hidden.get()))

        self.section(p, "Виджет")
        self.slider(p, "Прозрачность виджета", alpha_to_pct(app.cfg["alpha"]), 0, 100,
                    lambda v: app.set_alpha(pct_to_alpha(v)))
        cols = tk.Frame(p, bg=T["bg"])
        cols.pack(fill="x", pady=int(2 * s))
        tk.Label(cols, text="Колонок плиток:", bg=T["bg"], fg=T["fg"], font=font(10)).pack(side="left")
        for n in (1, 2, 3, 4):
            tk.Radiobutton(cols, text=str(n), value=n, variable=app.v_cols, command=app.set_cols,
                           bg=T["bg"], fg=T["fg"], selectcolor=T["panel"], activebackground=T["bg"],
                           activeforeground=T["fg"], font=font(10), bd=0,
                           highlightthickness=0).pack(side="left", padx=int(6 * s))
        self.check(p, "Закрепить на месте", app.v_lock, app.toggle_lock)
        self.check(p, "Поверх всех окон", app.v_top, app.toggle_top)

        self.section(p, "Запуск")
        app.v_auto.set(autostart_enabled())
        self.check(p, "Запускать виджет вместе с Windows", app.v_auto, app.toggle_autostart)
        tk.Label(p, text="Права администратора для датчиков — на вкладке «Датчики».", bg=T["bg"],
                 fg=T["muted"], font=font(8), anchor="w").pack(fill="x")

    # ---------- вкладка «Оформление» ----------
    def tab_look(self, p):
        s = self.app.s
        self.section(p, "Тема", top=4)
        grid = tk.Frame(p, bg=T["bg"])
        grid.pack(fill="x")
        current = self.app.cfg["theme"]
        for i, (key, th) in enumerate(THEMES.items()):
            card = tk.Frame(grid, bg=T["accent"] if key == current else T["bg"],
                            padx=int(2 * s), pady=int(2 * s), cursor="hand2")
            card.grid(row=i // 3, column=i % 3, padx=int(5 * s), pady=int(5 * s))
            cv = self.preview(card, th, int(160 * s), int(104 * s))
            cv.pack()
            lbl = tk.Label(card, text=th["title"], bg=th["bg"], fg=th["fg"], font=font(9, True, th))
            lbl.pack(fill="x")
            for wdg in (card, cv, lbl):
                wdg.bind("<Button-1>", lambda _e, k=key: self.pick_theme(k))

        self.section(p, "Стиль графиков")
        self.v_style = tk.StringVar(value=self.app.cfg["graph_style"])
        row = tk.Frame(p, bg=T["bg"])
        row.pack(fill="x")
        for val, title in [("", "Как в теме")] + list(GRAPH_STYLES.items()):
            tk.Radiobutton(row, text=title, value=val, variable=self.v_style,
                           command=lambda: self.pick_theme(self.app.cfg["theme"]),
                           bg=T["bg"], fg=T["fg"], selectcolor=T["panel"], activebackground=T["bg"],
                           activeforeground=T["fg"], font=font(10), bd=0,
                           highlightthickness=0).pack(side="left", padx=(0, int(10 * s)))

        self.section(p, "Windows: панель задач и меню «Пуск»")
        self.v_transp = tk.BooleanVar(value=winstyle.transparency_enabled())
        self.check(p, "Эффекты прозрачности Windows (меню «Пуск», панель задач, окна)", self.v_transp,
                   lambda: winstyle.set_transparency(self.v_transp.get()))
        tk.Label(p, text="Панель задач:", bg=T["bg"], fg=T["fg"], font=font(10), anchor="w").pack(
            fill="x", pady=(int(4 * s), 0))
        self.v_tb = tk.StringVar(value=self.app.cfg["taskbar_mode"])
        row = tk.Frame(p, bg=T["bg"])
        row.pack(fill="x")
        for val, title in translucenttb.MODES:
            tk.Radiobutton(row, text=title, value=val, variable=self.v_tb,
                           command=lambda: self.app.set_taskbar_mode(self.v_tb.get()),
                           bg=T["bg"], fg=T["fg"], selectcolor=T["panel"], activebackground=T["bg"],
                           activeforeground=T["fg"], font=font(10), bd=0,
                           highlightthickness=0).pack(side="left", padx=(0, int(10 * s)))
        tk.Label(p, text="Панель задач красит TranslucentTB (GPL-3.0, скачается сам, ~2 МБ) — работает, "
                         "пока запущен виджет. Меню «Пуск» в Windows 11 становится полупрозрачным только "
                         "через системные эффекты прозрачности (галочка выше).",
                 bg=T["bg"], fg=T["muted"], font=font(8), justify="left", anchor="w",
                 wraplength=int(540 * s)).pack(fill="x", pady=(int(4 * s), 0))

    def preview(self, parent, th, w, h):
        """Мини-превью виджета в теме th."""
        s = self.app.s
        style = self.app.cfg["graph_style"] or th["style"]
        c = tk.Canvas(parent, width=w, height=h, bg=th["bg"], highlightthickness=0)
        pad = int(6 * s)
        c.create_text(pad, pad + 5 * s, text="⠿  PC", anchor="w", fill=th["fg"], font=font(8, True, th))
        c.create_text(w - pad, pad + 5 * s, text="Uptime 3h", anchor="e", fill=th["muted"],
                      font=font(6, False, th))
        rnd = random.Random(hash(th["title"]))
        tw, th_ = (w - pad * 3) // 2, (h - pad * 3 - int(14 * s)) // 2
        labels = [("CPU", "37%"), ("GPU", "41°"), ("RAM", "29%"), ("NET", "1.2M")]
        for i in range(4):
            x0 = pad + (i % 2) * (tw + pad)
            y0 = int(18 * s) + (i // 2) * (th_ + pad)
            c.create_rectangle(x0, y0, x0 + tw, y0 + th_, fill=th["panel"], outline="")
            c.create_text(x0 + 4 * s, y0 + 6 * s, text=labels[i][0], anchor="w", fill=th["muted"],
                          font=font(5, True, th))
            c.create_text(x0 + tw - 4 * s, y0 + 6 * s, text=labels[i][1], anchor="e", fill=th["fg"],
                          font=font(6, True, th))
            gx, gy, gw, gh = x0 + 3 * s, y0 + 12 * s, tw - 6 * s, th_ - 15 * s
            sub = tk.Canvas(c, width=int(gw), height=int(gh), bg=th["graph_bg"], highlightthickness=0)
            v, data = 40, []
            for _ in range(24):
                v = max(5, min(95, v + rnd.uniform(-18, 18)))
                data.append(v)
            draw_series(sub, int(gw), int(gh), [data], [th["p"][i]], 100, style,
                        max(1, th["line_w"] * s * 0.8), th["graph_bg"])
            c.create_window(gx, gy, window=sub, anchor="nw")
            sub.bind("<Button-1>", lambda _e: c.event_generate("<Button-1>"))
        return c

    def pick_theme(self, key):
        style = self.v_style.get() if hasattr(self, "v_style") else self.app.cfg["graph_style"]
        self.app.apply_theme(key, style)
        self.reopen()

    # ---------- вкладка «Датчики» ----------
    def tab_sensors(self, p):
        app, s = self.app, self.app.s
        self.section(p, "Встроенный LibreHardwareMonitor", top=4)
        st = tk.Frame(p, bg=T["bg"])
        st.pack(fill="x")
        self.st_host = tk.Label(st, text="", bg=T["bg"], fg=T["fg"], font=font(10), anchor="w")
        self.st_host.grid(row=0, column=0, sticky="w")
        self.button(st, "Запустить", lambda: (app.start_sensors(allow_uac=True),
                                              self.refresh_status())).grid(row=0, column=1, sticky="e")
        self.st_pawn = tk.Label(st, text="", bg=T["bg"], fg=T["fg"], font=font(10), anchor="w")
        self.st_pawn.grid(row=1, column=0, sticky="w", pady=int(3 * s))
        self.btn_pawn = self.button(st, "Установить драйвер", sensorsvc.install_pawnio)
        self.btn_pawn.grid(row=1, column=1, sticky="e", pady=int(3 * s))
        st.columnconfigure(0, weight=1)

        self.v_lhm = tk.BooleanVar(value=app.cfg["lhm_enabled"])
        self.check(p, "Включить расширенные датчики (температуры ЦП, вентиляторы, частоты…)",
                   self.v_lhm, lambda: app.set_lhm_enabled(self.v_lhm.get()))
        self.v_task = tk.BooleanVar(value=sensorsvc.task_exists())
        self.check(p, "Запускать датчики без запроса UAC (задача в Планировщике)", self.v_task,
                   lambda: (sensorsvc.set_task(self.v_task.get()),
                            self.win.after(4000, self.refresh_status)))
        row = tk.Frame(p, bg=T["bg"])
        row.pack(fill="x", pady=(int(4 * s), 0))
        self.button(row, "Настроить всё одним запросом UAC", app.run_setup, accent=True).pack(side="left")

        self.section(p, "Плитки на виджете")
        srow = tk.Frame(p, bg=T["bg"])
        srow.pack(fill="x", pady=(0, int(4 * s)))
        tk.Label(srow, text="Поиск:", bg=T["bg"], fg=T["fg"], font=font(10)).pack(side="left")
        self.sq = tk.StringVar()
        tk.Entry(srow, textvariable=self.sq, bg=T["panel"], fg=T["fg"], insertbackground=T["fg"],
                 relief="flat", font=font(10)).pack(side="left", fill="x", expand=True,
                                                    padx=int(8 * s), ipady=int(2 * s))
        self.sq.trace_add("write", lambda *_: self.fill_sensor_list())
        self.sensor_box = ScrollFrame(p)
        self.sensor_box.pack(fill="both", expand=True)
        self.fill_sensor_list()
        self.refresh_status()

    def fill_sensor_list(self):
        app, s = self.app, self.app.s
        box = self.sensor_box.inner
        for c in box.winfo_children():
            c.destroy()
        q = self.sq.get().strip().lower()

        def group(title):
            tk.Label(box, text=title, bg=T["bg"], fg=T["accent"], font=font(9, True),
                     anchor="w").pack(fill="x", pady=(int(8 * s), int(2 * s)))

        builtin = [(k, spec[0]) for k, spec in app.TILES.items() if not q or q in spec[0].lower()]
        if builtin:
            group("Основные")
            for key, title in builtin:
                v = tk.BooleanVar(value=app.tile_enabled(key))
                self.check(box, title, v, lambda k=key, var=v: app.set_tile(k, var.get()))

        lhm, ok = app.sensors.get_lhm()
        if not ok:
            tk.Label(box, text="\nРасширенные датчики не запущены — нажми «Запустить» или "
                               "«Настроить всё» выше.", bg=T["bg"], fg=T["muted"], font=font(9),
                     justify="left", anchor="w").pack(fill="x")
            return
        groups = defaultdict(list)
        for sen in lhm:
            label = f"{sen['hw']} · {TYPE_NAMES.get(sen['type'], sen['type'])} · {sen['name']}"
            if q and q not in label.lower():
                continue
            groups[sen["group"]].append(sen)
        for gname, items in groups.items():
            group(gname)
            items.sort(key=lambda x: (x["type"], x["name"]))
            for sen in items:
                key = "lhm:" + sen["id"]
                tname = TYPE_NAMES.get(sen["type"], sen["type"])
                sub = f" ({sen['hw']})" if sen["hw"] != gname else ""
                text = f"{tname}: {sen['name']}{sub}   —   {fmt_value(sen['type'], sen['value'])}"
                v = tk.BooleanVar(value=app.tile_enabled(key))
                meta = {"title": f"{short_hw(sen['hw'])} · {sen['name']}", "type": sen["type"]}
                self.check(box, text, v, lambda k=key, var=v, m=meta: app.set_tile(k, var.get(), m))

    def refresh_status(self):
        if self.win is None or not self.win.winfo_exists() or self.tab != "sensors":
            return

        def work():
            running = sensorsvc.host_running()
            pawn = sensorsvc.pawnio_installed()
            if self.win is not None:
                self.win.after(0, lambda: self._show_status(running, pawn))

        threading.Thread(target=work, daemon=True).start()
        self._status_job = self.win.after(3000, self.refresh_status)

    def _show_status(self, running, pawn):
        try:
            self.st_host.config(text=("● Датчики работают" if running else "○ Датчики не запущены"),
                                fg=T["fg"] if running else T["warn"])
            self.st_pawn.config(text=("● Драйвер PawnIO установлен" if pawn
                                      else "○ Драйвер PawnIO не установлен (нужен для температуры ЦП)"),
                                fg=T["fg"] if pawn else T["warn"])
            if pawn:
                self.btn_pawn.grid_remove()
        except tk.TclError:
            pass

    def _cancel_status(self):
        if self._status_job and self.win is not None:
            try:
                self.win.after_cancel(self._status_job)
            except Exception:
                pass
        self._status_job = None

    # ---------- вкладка «Приложения» ----------
    def tab_apps(self, p):
        app, s = self.app, self.app.s
        row = tk.Frame(p, bg=T["bg"])
        row.pack(fill="x", pady=(int(4 * s), int(8 * s)))
        self.button(row, "Найти установленные…", self.find_installed, accent=True).pack(side="left")
        self.button(row, "Выбрать exe / ярлык…",
                    lambda: (app.launcher.add_file(self.win), self.switch("apps"))).pack(
            side="left", padx=int(8 * s))
        tk.Label(p, text="Порядок здесь = порядок в панели. Цифры 1–9 запускают первые девять.",
                 bg=T["bg"], fg=T["muted"], font=font(8), anchor="w").pack(fill="x")
        box = ScrollFrame(p)
        box.pack(fill="both", expand=True, pady=(int(6 * s), 0))
        apps = app.cfg["apps"]
        if not apps:
            tk.Label(box.inner, text="\nПока пусто. Нажми «Найти установленные…».", bg=T["bg"],
                     fg=T["muted"], font=font(10)).pack()
        for i, a in enumerate(apps):
            r = tk.Frame(box.inner, bg=T["panel"], padx=int(8 * s), pady=int(5 * s))
            r.pack(fill="x", pady=int(2 * s))
            img = ImageTk.PhotoImage(file_icon(a["path"], int(28 * s), T["panel"], a["name"], T["accent"]))
            self._imgs.append(img)
            tk.Label(r, image=img, bg=T["panel"]).pack(side="left", padx=(0, int(8 * s)))
            txt = tk.Frame(r, bg=T["panel"])
            txt.pack(side="left", fill="x", expand=True)
            tk.Label(txt, text=a["name"], bg=T["panel"], fg=T["fg"], font=font(10, True),
                     anchor="w").pack(fill="x")
            path = a["path"].replace("shell:AppsFolder\\", "Магазин / меню Пуск: ")
            tk.Label(txt, text=path, bg=T["panel"], fg=T["muted"], font=font(7), anchor="w").pack(fill="x")
            for sym, cmd, ok in (("✕", lambda k=i: self.app_op("remove", k), True),
                                 ("✎", lambda k=i: self.app_op("rename", k), True),
                                 ("↓", lambda k=i: self.app_op("down", k), i < len(apps) - 1),
                                 ("↑", lambda k=i: self.app_op("up", k), i > 0)):
                b = tk.Label(r, text=sym, bg=T["panel"], fg=T["fg"] if ok else T["hover"],
                             font=font(11), padx=int(6 * s), cursor="hand2" if ok else "")
                b.pack(side="right")
                if ok:
                    b.bind("<Button-1>", lambda _e, c=cmd: c())

    def app_op(self, op, i):
        L = self.app.launcher
        if op == "remove":
            L.remove(i)
        elif op == "rename":
            L.rename(i, self.win)
        elif op == "up":
            L.move(i, -1)
        elif op == "down":
            L.move(i, 1)
        self.switch("apps")

    def find_installed(self):
        picker = self.app.launcher.pick_installed(self.win)
        picker.win.bind("<Destroy>", lambda e: e.widget is picker.win and self.win is not None
                        and self.win.after(100, lambda: self.switch("apps") if self.tab == "apps" else None),
                        add="+")

    # ---------- вкладка «NabyScape» ----------
    def tab_naby(self, p):
        app, s = self.app, self.app.s
        self.section(p, "NabyScape — «остров» на рабочем столе", top=4)
        tk.Label(p, text="Музыка с текстами песен, таймер и фокус, заметки, задачи, буфер обмена, "
                         "погода, календарь и ИИ-компаньон — в одной панели у края экрана.\n"
                         "Виджет сам скачает официальную сборку с GitHub и будет запускать и "
                         "закрывать её вместе с собой.",
                 bg=T["bg"], fg=T["fg"], font=font(9), justify="left", anchor="w",
                 wraplength=int(540 * s)).pack(fill="x")
        self.v_naby = tk.BooleanVar(value=app.cfg["naby_enabled"])
        self.check(p, "Включить NabyScape", self.v_naby,
                   lambda: (app.set_naby_enabled(self.v_naby.get()), self.refresh_naby()))
        self.naby_status = tk.Label(p, text="", bg=T["bg"], fg=T["fg"], font=font(10), anchor="w")
        self.naby_status.pack(fill="x", pady=(int(6 * s), 0))
        self.naby_msg = tk.Label(p, text="", bg=T["bg"], fg=T["accent"], font=font(9), anchor="w")
        self.naby_msg.pack(fill="x")

        self.section(p, "Управление")
        row = tk.Frame(p, bg=T["bg"])
        row.pack(fill="x")
        self.button(row, "Открыть настройки NabyScape", lambda: app.naby_action("settings"),
                    accent=True).pack(side="left")
        self.button(row, "Перезапустить", lambda: app.naby_action("restart")).pack(side="left", padx=int(6 * s))
        self.button(row, "Остановить", lambda: app.naby_action("stop")).pack(side="left")
        row2 = tk.Frame(p, bg=T["bg"])
        row2.pack(fill="x", pady=(int(6 * s), 0))
        self.button(row2, "Проверить обновление", app.naby_update).pack(side="left")
        self.button(row2, "Удалить файлы NabyScape", self.naby_uninstall).pack(side="left", padx=int(6 * s))
        tk.Label(p, text="Внешний вид острова, его виджеты, музыка и ИИ настраиваются в его собственном окне "
                         "(кнопка выше). Отключи там «Запуск с Windows» — запуском управляет этот виджет.",
                 bg=T["bg"], fg=T["muted"], font=font(8), justify="left", anchor="w",
                 wraplength=int(540 * s)).pack(fill="x", pady=(int(8 * s), 0))

        self.section(p, "Автор")
        link = tk.Label(p, text=f"BillyNabil · лицензия MIT · {nabyscape.HOME}", bg=T["bg"],
                        fg=T["accent"], font=font(9), cursor="hand2", anchor="w")
        link.pack(fill="x")
        link.bind("<Button-1>", lambda _e: os.startfile(nabyscape.HOME))
        self.refresh_naby()

    def naby_uninstall(self):
        from tkinter import messagebox
        if messagebox.askyesno("NabyScape", "Удалить скачанные файлы NabyScape? "
                                            "Его настройки и заметки в профиле Windows останутся.",
                               parent=self.win):
            self.v_naby.set(False)
            self.app.set_naby_enabled(False)
            self.app.naby_action("uninstall")

    def refresh_naby(self):
        if self.win is None or not self.win.winfo_exists() or self.tab != "naby":
            return
        try:
            ver = nabyscape.installed_version()
            if not nabyscape.installed():
                st = "○ Не установлен — скачается при включении (~85 МБ)"
            elif nabyscape.running():
                st = f"● Работает · версия {ver or '?'}"
            else:
                st = f"○ Установлен {ver or ''}, не запущен"
            self.naby_status.config(text=st)
            self.naby_msg.config(text=self.app.naby_msg)
        except tk.TclError:
            return
        self._cancel_status()
        self._status_job = self.win.after(700, self.refresh_naby)

    # ---------- горячая клавиша ----------
    def refresh_hotkey(self):
        hk = self.app.cfg["hotkey"]
        self.hk_lbl.config(text=hotkey_label(hk["mods"], hk["vk"]))

    def start_capture(self):
        self.capturing = True
        self.app.hotkeys.set(0, 0)
        self.hk_lbl.config(text="нажмите сочетание…")
        self.hk_msg.config(text="Ctrl / Alt / Shift / Win + клавиша. Esc — отмена.", fg=T["muted"])
        self.win.focus_force()

    def stop_capture(self, restore):
        self.capturing = False
        if restore:
            self.app.apply_hotkey()
        try:
            self.refresh_hotkey()
        except tk.TclError:
            pass

    def clear_hotkey(self):
        self.capturing = False
        self.app.cfg["hotkey"] = {"mods": 0, "vk": 0}
        self.app.apply_hotkey()
        self.app.save()
        self.refresh_hotkey()
        self.hk_msg.config(text="Горячая клавиша отключена — панель открывается кнопкой с точками.", fg=T["muted"])

    def on_key(self, e):
        if not self.capturing:
            return
        vk = e.keycode
        if vk == 0x1B:
            self.stop_capture(restore=True)
            self.hk_msg.config(text="")
            return "break"
        if vk in MODIFIER_VKS:
            return "break"
        mods = current_mods()
        if not mods and not (0x70 <= vk <= 0x87):
            self.hk_msg.config(text="Нужен хотя бы один модификатор (кроме F1–F24).", fg=T["hot"])
            return "break"
        old = dict(self.app.cfg["hotkey"])
        self.app.cfg["hotkey"] = {"mods": mods, "vk": vk}
        if self.app.apply_hotkey():
            self.app.save()
            self.hk_msg.config(text="Сохранено.", fg=T["muted"])
        else:
            self.app.cfg["hotkey"] = old
            self.app.apply_hotkey()
            self.hk_msg.config(text=f"{hotkey_label(mods, vk)} занято другой программой — выберите другое.",
                               fg=T["hot"])
        self.capturing = False
        self.refresh_hotkey()
        return "break"


def short_hw(name):
    """«Intel Xeon E5-2696 v3» → «Xeon E5-2696 v3», «NVIDIA GeForce RTX 3060 Ti» → «RTX 3060 Ti»."""
    for prefix in ("Intel ", "AMD ", "NVIDIA GeForce ", "NVIDIA ", "Nuvoton ", "ITE "):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name
