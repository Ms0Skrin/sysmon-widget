"""Поиск установленных приложений (всё, что есть в меню «Пуск», включая Магазин)
и окно выбора с поиском и галочками."""
import json
import subprocess
import threading
import tkinter as tk

from PIL import ImageTk

from icons import file_icon
from theme import T, dark_titlebar, font, is_dark

NO_WINDOW = 0x08000000
SKIP_WORDS = ("uninstall", "удалить", "удаление", "readme", "help", "справка", "documentation",
              "website", "веб-сайт", "release notes", "license")


def scan_installed():
    """→ [{"name", "path"}] — path вида shell:AppsFolder\\<AppID>, запускается через os.startfile."""
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
         "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
        capture_output=True, timeout=60, creationflags=NO_WINDOW)
    try:
        items = json.loads(r.stdout.decode("utf-8", "replace") or "[]")
    except json.JSONDecodeError:
        return []
    if isinstance(items, dict):
        items = [items]
    seen, out = set(), []
    for it in items:
        name, app_id = (it.get("Name") or "").strip(), it.get("AppID") or ""
        if not name or not app_id or any(w in name.lower() for w in SKIP_WORDS):
            continue
        if app_id.lower().startswith(("http://", "https://")):
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "path": "shell:AppsFolder\\" + app_id})
    out.sort(key=lambda a: a["name"].lower())
    return out


class AppPicker:
    """Окно «Найти установленные приложения». on_add(list_of_apps) вызывается при подтверждении."""

    def __init__(self, master, scale, existing_paths, on_add):
        self.s, self.on_add = scale, on_add
        self.existing = {p.lower() for p in existing_paths}
        self.apps, self.vars, self.rows, self._icons = [], {}, {}, {}
        s = scale
        w = self.win = tk.Toplevel(master, bg=T["bg"])
        w.title("Добавить приложения")
        w.attributes("-topmost", True)
        w.geometry(f"{int(460 * s)}x{int(560 * s)}")
        w.minsize(int(360 * s), int(360 * s))
        dark_titlebar(w, is_dark())

        top = tk.Frame(w, bg=T["bg"], padx=int(12 * s), pady=int(10 * s))
        top.pack(fill="x")
        tk.Label(top, text="Поиск:", bg=T["bg"], fg=T["fg"], font=font(10)).pack(side="left")
        self.q = tk.StringVar()
        e = tk.Entry(top, textvariable=self.q, bg=T["panel"], fg=T["fg"], insertbackground=T["fg"],
                     relief="flat", font=font(10))
        e.pack(side="left", fill="x", expand=True, padx=(int(8 * s), 0), ipady=int(3 * s))
        self.q.trace_add("write", lambda *_: self.filter())

        mid = tk.Frame(w, bg=T["bg"])
        mid.pack(fill="both", expand=True, padx=int(12 * s))
        self.canvas = tk.Canvas(mid, bg=T["bg"], highlightthickness=0)
        sb = tk.Scrollbar(mid, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.list = tk.Frame(self.canvas, bg=T["bg"])
        self.canvas.create_window((0, 0), window=self.list, anchor="nw", tags="inner")
        self.list.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda ev: self.canvas.itemconfigure("inner", width=ev.width))
        w.bind("<MouseWheel>", lambda ev: self.canvas.yview_scroll(int(-ev.delta / 120), "units"))

        bottom = tk.Frame(w, bg=T["bg"], padx=int(12 * s), pady=int(10 * s))
        bottom.pack(fill="x")
        self.status = tk.Label(bottom, text="Сканирую меню «Пуск»…", bg=T["bg"], fg=T["muted"],
                               font=font(9))
        self.status.pack(side="left")
        self._btn(bottom, "Добавить выбранные", self.confirm, accent=True).pack(side="right")
        self._btn(bottom, "Отмена", w.destroy).pack(side="right", padx=int(6 * s))

        e.focus_set()
        threading.Thread(target=self._scan, daemon=True).start()

    def _btn(self, parent, text, cmd, accent=False):
        bg = T["accent"] if accent else T["panel"]
        fg = T["bg"] if accent else T["fg"]
        b = tk.Label(parent, text=text, bg=bg, fg=fg, font=font(9, True), cursor="hand2",
                     padx=int(12 * self.s), pady=int(5 * self.s))
        b.bind("<Button-1>", lambda _e: cmd())
        return b

    def _scan(self):
        try:
            apps = scan_installed()
        except Exception:
            apps = []
        self.win.after(0, lambda: self._fill(apps))

    def _fill(self, apps):
        if not self.win.winfo_exists():
            return
        self.apps = apps
        s = self.s
        for i, app in enumerate(apps):
            added = app["path"].lower() in self.existing
            v = self.vars[app["path"]] = tk.BooleanVar(value=False)
            row = tk.Frame(self.list, bg=T["bg"])
            ic = tk.Label(row, bg=T["bg"], width=int(28 * s), height=int(28 * s))
            ic.pack(side="left", padx=(0, int(8 * s)))
            cb = tk.Checkbutton(row, text=app["name"] + ("   ✓ уже добавлено" if added else ""),
                                variable=v, bg=T["bg"], fg=T["muted"] if added else T["fg"],
                                selectcolor=T["panel"], activebackground=T["bg"],
                                activeforeground=T["fg"], font=font(10), anchor="w", bd=0,
                                highlightthickness=0, state="disabled" if added else "normal")
            cb.pack(side="left", fill="x", expand=True)
            self.rows[app["path"]] = (row, ic, app)
            row.pack(fill="x", pady=int(1 * s))
        self.status.config(text=f"Найдено: {len(apps)}")
        self._load_icons(0)

    def _load_icons(self, start):
        """Иконки грузим порциями, чтобы окно не подвисало."""
        if not self.win.winfo_exists():
            return
        paths = list(self.rows)
        for p in paths[start:start + 8]:
            _row, ic, app = self.rows[p]
            img = ImageTk.PhotoImage(file_icon(app["path"], int(28 * self.s), T["bg"], app["name"],
                                               T["accent"]))
            self._icons[p] = img
            ic.config(image=img, width=0, height=0)
        if start + 8 < len(paths):
            self.win.after(1, lambda: self._load_icons(start + 8))

    def filter(self):
        q = self.q.get().strip().lower()
        for row, _ic, app in self.rows.values():
            row.pack_forget()
        shown = 0
        for row, _ic, app in self.rows.values():
            if not q or q in app["name"].lower():
                row.pack(fill="x", pady=int(1 * self.s))
                shown += 1
        self.canvas.yview_moveto(0)
        self.status.config(text=f"Найдено: {shown}")

    def confirm(self):
        chosen = [dict(name=a["name"], path=a["path"]) for a in self.apps if self.vars[a["path"]].get()]
        self.win.destroy()
        if chosen:
            self.on_add(chosen)
