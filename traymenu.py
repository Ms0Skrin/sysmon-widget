"""Красивое меню трея в стиле системы: скруглённые края, иконки Fluent, плавное появление."""
import ctypes
import tkinter.font as tkfont
import tkinter as tk

from launcher import work_area
from theme import blend, native_theme, round_region

user32 = ctypes.windll.user32

ICONS = {  # Segoe Fluent Icons / Segoe MDL2 Assets
    "show": "", "apps": "", "settings": "", "music": "",
    "restart": "", "update": "", "exit": "", "info": "", "look": "",
}
ICONS.update({  # меню панели
    "check": "", "back": "", "more": "", "tiles": "", "cols": "",
    "add": "", "delete": "", "tray": "", "sensors": "", "stop": "",
})


def icon_font(root):
    fams = set(tkfont.families(root))
    return "Segoe Fluent Icons" if "Segoe Fluent Icons" in fams else "Segoe MDL2 Assets"


class TrayMenu:
    """items: список (icon, text, callback[, opts]) | ("-",) разделитель | ("info", text) неактивная строка.

    opts: {"check": bool} — пункт-галочка (вместо иконки — отметка), {"sub": True} — стрелка «дальше»,
    {"keep": True} — меню не закрывается (callback сам перерисует его, напр. страницу подменю).
    """

    def __init__(self, root, scale):
        self.root, self.s = root, scale
        self.top = None
        self._ifont = None

    def visible(self):
        return self.top is not None and self.top.winfo_exists()

    def close(self):
        if self.visible():
            self.top.destroy()
        self.top = None

    def show(self, x, y, items, title=None, at_cursor=False, animate=True):
        """at_cursor — контекстное меню: раскрывается от курсора вниз-вправо (а не над значком трея)."""
        self.close()
        s = self.s
        th = native_theme()  # всегда цвета системы, независимо от темы виджета
        bg, fg, muted = th["bg"], th["fg"], th["muted"]
        hover = blend(th["accent"], bg, 0.22)
        border = blend(fg, bg, 0.14)
        self._ifont = self._ifont or icon_font(self.root)
        radius = int(10 * s)

        top = self.top = tk.Toplevel(self.root, bg=border)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.attributes("-alpha", 0.0)
        inner = tk.Frame(top, bg=bg, padx=int(5 * s), pady=int(5 * s))
        inner.pack(padx=1, pady=1)

        if title:
            tk.Label(inner, text=title, bg=bg, fg=muted, font=("Segoe UI Semibold", 9), anchor="w",
                     padx=int(10 * s), pady=int(4 * s)).pack(fill="x")

        for it in items:
            if it[0] == "-":
                tk.Frame(inner, bg=border, height=1).pack(fill="x", padx=int(8 * s), pady=int(4 * s))
                continue
            if it[0] == "info":
                tk.Label(inner, text=it[1], bg=bg, fg=muted, font=("Segoe UI", 8), anchor="w",
                         padx=int(12 * s), pady=int(3 * s)).pack(fill="x")
                continue
            icon, text, cb = it[:3]
            opts = it[3] if len(it) > 3 else {}
            if "check" in opts:
                icon = "check" if opts["check"] else ""
            row = tk.Frame(inner, bg=bg, cursor="hand2")
            row.pack(fill="x")
            ic = tk.Label(row, text=ICONS.get(icon, ""), bg=bg, fg=th["accent"], font=(self._ifont, 11),
                          width=2, padx=int(6 * s), pady=int(6 * s))
            ic.pack(side="left")
            lb = tk.Label(row, text=text, bg=bg, fg=fg, font=("Segoe UI", 10), anchor="w",
                          padx=int(4 * s), pady=int(6 * s))
            lb.pack(side="left", fill="x", expand=True, ipadx=int(20 * s))
            parts = [row, ic, lb]
            if opts.get("sub"):
                ch = tk.Label(row, text=ICONS["more"], bg=bg, fg=muted, font=(self._ifont, 8), padx=int(8 * s))
                ch.pack(side="right")
                parts.append(ch)

            def hl(on, parts=parts):
                for w in parts:
                    w.config(bg=hover if on else bg)

            for w in parts:
                w.bind("<Enter>", lambda _e, h=hl: h(True))
                w.bind("<Leave>", lambda _e, h=hl: h(False))
                w.bind("<ButtonRelease-1>", lambda _e, c=cb, k=opts.get("keep"): self._run(c, k))

        top.update_idletasks()
        w, h = top.winfo_reqwidth(), top.winfo_reqheight()
        l, t, r, b = work_area(x, y)
        if at_cursor:
            mx = x if x + w <= r - 4 else max(l + 4, x - w)
            my = y if y + h <= b - 4 else max(t + 4, min(y - h, b - h - 4))
        else:
            mx = min(max(l + 4, x - w // 2 if x + w // 2 <= r else x - w), r - w - 4)
            my = y - h - 8 if y - h - 8 >= t else min(y + 8, b - h - 4)
        top.geometry(f"{w}x{h}+{int(mx)}+{int(my) + (10 if animate else 0)}")
        round_region(top, radius, w, h)
        top.bind("<Escape>", lambda _e: self.close())
        top.bind("<FocusOut>", lambda _e: top.after(120, self._check_focus))
        try:
            user32.SetForegroundWindow(user32.GetParent(top.winfo_id()))
        except Exception:
            pass
        top.focus_force()
        if animate:
            self._animate(int(mx), int(my), w, h, 0)
        else:  # перерисовка того же меню (подменю, галочка) — без повторного «всплытия»
            top.attributes("-alpha", 0.97)

    def _animate(self, x, y, w, h, step, steps=8):
        if not self.visible():
            return
        k = 1 - (1 - (step / steps)) ** 3
        self.top.geometry(f"{w}x{h}+{x}+{int(y + 10 * (1 - k))}")
        self.top.attributes("-alpha", 0.97 * k)
        if step < steps:
            self.top.after(14, lambda: self._animate(x, y, w, h, step + 1, steps))

    def _check_focus(self):
        if not self.visible():
            return
        try:
            f = self.top.focus_get()
        except Exception:
            f = None
        if f is None or not str(f).startswith(str(self.top)):
            self.close()

    def _run(self, cb, keep=False):
        if not keep:
            self.close()
        if cb:
            self.root.after(10, cb)
