"""Окно настроек: горячая клавиша, иконки рабочего стола, автозапуск и т.п."""
import tkinter as tk

import desktop
from config import autostart_enabled, set_autostart
from hotkey import MODIFIER_VKS, current_mods, hotkey_label
from theme import ACCENT, BG, FG, HOT, HOVER, MUTED, PANEL, dark_titlebar


class SettingsWindow:
    def __init__(self, app):
        self.app = app  # Widget
        self.win = None
        self.capturing = False

    def show(self):
        if self.win is not None and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
            return
        app, s = self.app, self.app.s
        w = self.win = tk.Toplevel(app.root, bg=BG)
        w.title("Настройки виджета")
        w.resizable(False, False)
        w.attributes("-topmost", True)
        dark_titlebar(w)
        w.protocol("WM_DELETE_WINDOW", self.close)

        pad = int(16 * s)
        body = tk.Frame(w, bg=BG, padx=pad, pady=pad)
        body.pack(fill="both", expand=True)

        def section(text):
            tk.Label(body, text=text.upper(), bg=BG, fg=MUTED, font=("Segoe UI Semibold", 8),
                     anchor="w").pack(fill="x", pady=(int(10 * s), int(4 * s)))

        def button(parent, text, cmd):
            b = tk.Label(parent, text=text, bg=PANEL, fg=FG, font=("Segoe UI", 9),
                         padx=int(12 * s), pady=int(4 * s), cursor="hand2")
            b.bind("<Button-1>", lambda _e: cmd())
            b.bind("<Enter>", lambda _e: b.config(bg=HOVER))
            b.bind("<Leave>", lambda _e: b.config(bg=PANEL))
            return b

        def check(text, var, cmd):
            tk.Checkbutton(body, text=text, variable=var, command=cmd, bg=BG, fg=FG,
                           selectcolor=PANEL, activebackground=BG, activeforeground=FG,
                           font=("Segoe UI", 10), anchor="w", bd=0,
                           highlightthickness=0).pack(fill="x", pady=int(2 * s))

        section("Меню приложений")
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x")
        tk.Label(row, text="Горячая клавиша:", bg=BG, fg=FG, font=("Segoe UI", 10)).pack(side="left")
        self.hk_lbl = tk.Label(row, text="", bg=BG, fg=ACCENT, font=("Segoe UI Semibold", 10))
        self.hk_lbl.pack(side="left", padx=int(8 * s))
        button(row, "Отключить", self.clear_hotkey).pack(side="right")
        button(row, "Изменить", self.start_capture).pack(side="right", padx=int(6 * s))
        self.hk_msg = tk.Label(body, text="", bg=BG, fg=MUTED, font=("Segoe UI", 8), anchor="w")
        self.hk_msg.pack(fill="x", pady=(int(4 * s), 0))
        self.refresh_hotkey()

        section("Рабочий стол")
        self.v_icons_hidden = tk.BooleanVar(value=not desktop.icons_visible())
        check("Скрыть иконки рабочего стола", self.v_icons_hidden,
              lambda: app.set_desktop_icons_hidden(self.v_icons_hidden.get()))

        section("Виджет")
        check("Закрепить на месте", app.v_lock, app.toggle_lock)
        check("Поверх всех окон", app.v_top, app.toggle_top)
        app.v_auto.set(autostart_enabled())
        check("Запускать вместе с Windows", app.v_auto, app.toggle_autostart)

        foot = tk.Frame(body, bg=BG)
        foot.pack(fill="x", pady=(int(14 * s), 0))
        button(foot, "Закрыть", self.close).pack(side="right")

        w.bind("<KeyPress>", self.on_key)
        w.update_idletasks()
        x = (w.winfo_screenwidth() - w.winfo_reqwidth()) // 2
        y = (w.winfo_screenheight() - w.winfo_reqheight()) // 3
        w.geometry(f"+{x}+{y}")
        w.focus_force()

    def close(self):
        if self.capturing:
            self.stop_capture(restore=True)
        if self.win is not None:
            self.win.destroy()
        self.win = None

    # --- горячая клавиша ---
    def refresh_hotkey(self):
        hk = self.app.cfg["hotkey"]
        self.hk_lbl.config(text=hotkey_label(hk["mods"], hk["vk"]))

    def start_capture(self):
        self.capturing = True
        self.app.hotkeys.set(0, 0)  # снимаем старую, чтобы она не перехватила нажатие
        self.hk_lbl.config(text="нажмите сочетание…")
        self.hk_msg.config(text="Ctrl / Alt / Shift / Win + клавиша. Esc — отмена.", fg=MUTED)
        self.win.focus_force()

    def stop_capture(self, restore):
        self.capturing = False
        if restore:
            self.app.apply_hotkey()
        self.refresh_hotkey()

    def clear_hotkey(self):
        self.capturing = False
        self.app.cfg["hotkey"] = {"mods": 0, "vk": 0}
        self.app.apply_hotkey()
        self.app.save()
        self.refresh_hotkey()
        self.hk_msg.config(text="Горячая клавиша отключена — меню открывается кнопкой ⊞.", fg=MUTED)

    def on_key(self, e):
        if not self.capturing:
            return
        vk = e.keycode  # в Tk под Windows это виртуальный код клавиши
        if vk == 0x1B:  # Esc
            self.stop_capture(restore=True)
            self.hk_msg.config(text="", fg=MUTED)
            return "break"
        if vk in MODIFIER_VKS:
            return "break"
        mods = current_mods()
        if not mods and not (0x70 <= vk <= 0x87):
            self.hk_msg.config(text="Нужен хотя бы один модификатор (кроме F1–F24).", fg=HOT)
            return "break"
        old = dict(self.app.cfg["hotkey"])
        self.app.cfg["hotkey"] = {"mods": mods, "vk": vk}
        if self.app.apply_hotkey():
            self.app.save()
            self.hk_msg.config(text="Сохранено.", fg=MUTED)
        else:
            self.app.cfg["hotkey"] = old
            self.app.apply_hotkey()
            self.hk_msg.config(text=f"{hotkey_label(mods, vk)} занято другой программой — выберите другое.",
                               fg=HOT)
        self.capturing = False
        self.refresh_hotkey()
        return "break"
