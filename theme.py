"""Темы оформления. Текущая тема — словарь T (меняется на месте через set_theme)."""
import ctypes

GRAPH_STYLES = {"area": "Область", "line": "Линия", "bars": "Столбцы", "step": "Ступеньки", "ring": "Кольца"}

# p — палитра из 6 цветов для графиков
THEMES = {
    "graphite": {
        "title": "Графит", "bg": "#16181d", "panel": "#1f232b", "hover": "#2a2f3a",
        "fg": "#e8eaed", "muted": "#8b919c", "accent": "#4fa3ff", "warn": "#ffb347", "hot": "#ff5c5c",
        "graph_bg": "#1a1d24", "font": "Segoe UI", "style": "area", "line_w": 1.5,
        "p": ["#4fa3ff", "#b07cff", "#76b900", "#2bc4c4", "#ff7a59", "#f2c94c"],
    },
    "midnight": {
        "title": "Полночь", "bg": "#0e1424", "panel": "#151d33", "hover": "#1e2945",
        "fg": "#e6ecff", "muted": "#7d8bb0", "accent": "#6ea8ff", "warn": "#ffc66d", "hot": "#ff6b81",
        "graph_bg": "#111829", "font": "Segoe UI", "style": "area", "line_w": 2,
        "p": ["#6ea8ff", "#9d8cff", "#4dd4ff", "#57e3b5", "#ff8fa3", "#ffd36e"],
    },
    "light": {
        "title": "Светлая", "bg": "#f3f4f7", "panel": "#ffffff", "hover": "#e7eaf1",
        "fg": "#1d2330", "muted": "#6b7280", "accent": "#2563eb", "warn": "#d97706", "hot": "#dc2626",
        "graph_bg": "#f6f7fa", "font": "Segoe UI", "style": "area", "line_w": 2,
        "p": ["#2563eb", "#7c3aed", "#16a34a", "#0891b2", "#ea580c", "#ca8a04"],
    },
    "neon": {
        "title": "Неон", "bg": "#0b0b12", "panel": "#14121f", "hover": "#221d33",
        "fg": "#f5f3ff", "muted": "#8f86b3", "accent": "#ff3ea5", "warn": "#ffe14d", "hot": "#ff2e63",
        "graph_bg": "#100e19", "font": "Segoe UI", "style": "line", "line_w": 2.5,
        "p": ["#ff3ea5", "#00e5ff", "#b4ff39", "#a259ff", "#ff7b00", "#ffe14d"],
    },
    "terminal": {
        "title": "Терминал", "bg": "#000000", "panel": "#06110a", "hover": "#0d2214",
        "fg": "#39ff6a", "muted": "#1f9c45", "accent": "#39ff6a", "warn": "#ffb000", "hot": "#ff3b3b",
        "graph_bg": "#030a05", "font": "Consolas", "style": "step", "line_w": 1.5,
        "p": ["#39ff6a", "#2bd955", "#9cff57", "#00d68f", "#ffb000", "#e8ff3d"],
    },
    "sunset": {
        "title": "Закат", "bg": "#1a1214", "panel": "#261a1d", "hover": "#33232a",
        "fg": "#ffeee6", "muted": "#b08f8a", "accent": "#ff8a5c", "warn": "#ffc15e", "hot": "#ff4f5e",
        "graph_bg": "#21171a", "font": "Segoe UI", "style": "bars", "line_w": 1.5,
        "p": ["#ff8a5c", "#ff5e8a", "#ffc15e", "#c77dff", "#ff4f5e", "#ffd88a"],
    },
}

# какой цвет палитры у какой плитки
SERIES_IDX = {
    "cpu": [0], "cores": [0], "cpu_temp": [4], "ram": [1], "gpu": [2], "gpu_temp": [4],
    "vram": [3], "gpu_pwr": [5], "disk": [5, 4], "net": [2, 5],
}
SENSOR_TYPE_IDX = {"Temperature": 4, "Load": 0, "Fan": 3, "Power": 5, "Clock": 1, "Control": 2}

T = dict(THEMES["graphite"])
T["name"] = "graphite"


def native_theme():
    """Родной стиль Windows 11: системные светлый/тёмный режим и акцентный цвет."""
    import winstyle
    acc = winstyle.accent_color()
    if winstyle.system_dark():
        base = {"bg": "#202020", "panel": "#2b2b2b", "hover": "#383838", "fg": "#ffffff",
                "muted": "#9d9d9d", "graph_bg": "#262626", "warn": "#fce100", "hot": "#ff99a4",
                "p": [blend(acc, "#ffffff", 0.75), "#b4a0ff", "#6ccb5f", "#60cdff", "#ff8c5a", "#f7c548"]}
        acc = blend(acc, "#ffffff", 0.75)  # в тёмном режиме Windows берёт светлый вариант акцента
    else:
        base = {"bg": "#f3f3f3", "panel": "#fbfbfb", "hover": "#eaeaea", "fg": "#1a1a1a",
                "muted": "#616161", "graph_bg": "#f6f6f6", "warn": "#9d5d00", "hot": "#c42b1c",
                "p": [acc, "#8764b8", "#0f7b0f", "#005fb8", "#ca5010", "#986f0b"]}
    base.update({"title": "Windows", "accent": acc, "font": "Segoe UI", "style": "area", "line_w": 1.5})
    return base




def set_theme(name, graph_style=None):
    if name == "windows":
        THEMES["windows"] = native_theme()  # подхватываем текущие системные цвета
    base = THEMES.get(name, THEMES["graphite"])
    T.clear()
    T.update(base)
    T["name"] = name if name in THEMES else "graphite"
    if graph_style in GRAPH_STYLES:
        T["style"] = graph_style


def series_colors(key, sensor_type=None):
    p = T["p"]
    if key in SERIES_IDX:
        return [p[i] for i in SERIES_IDX[key]]
    return [p[SENSOR_TYPE_IDX.get(sensor_type, 1)]]


def font(size, bold=False, theme=None):
    fam = (theme or T)["font"]
    if bold and fam == "Segoe UI":
        fam = "Segoe UI Semibold"
    return (fam, size, "bold") if bold and fam != "Segoe UI Semibold" else (fam, size)


def pct_to_alpha(p):
    """Ползунок «Прозрачность» 0–100 % → альфа окна. Нелинейно: у непрозрачного края шаги мельче,
    потому что глаз сильнее всего замечает разницу именно там."""
    return round(1 - 0.9 * (max(0, min(100, p)) / 100) ** 2, 3)


def alpha_to_pct(a):
    return int(round(100 * (max(0.0, 1 - a) / 0.9) ** 0.5))


def blend(hex_color, bg_hex, a):
    c = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(bg_hex[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(b[i] + (c[i] - b[i]) * a):02x}" for i in range(3))


def draw_series(c, w, h, series_list, colors, top, style, line_w, bg, pad=2):
    """Рисует один или несколько рядов на canvas в заданном стиле."""
    n = len(series_list[0]) if series_list else 0
    if n < 2:
        return
    step = w / (n - 1)
    for idx, (series, color) in enumerate(zip(series_list, colors)):
        ys = [h - pad - min(max(v, 0) / top, 1) * (h - 2 * pad) for v in series]
        if style == "bars":
            bw = w / n
            for i, y in enumerate(ys):
                x0 = i * bw + (bw * 0.5 if idx else 0)
                c.create_rectangle(x0, y, x0 + max(1, bw * (0.5 if len(colors) > 1 else 0.8)), h,
                                   fill=color if idx else blend(color, bg, 0.85), outline="")
            continue
        if style == "step":
            pts = []
            for i, y in enumerate(ys):
                x = i * step
                if i:
                    pts += [x, pts[-1]]
                pts += [x, y]
        else:
            pts = []
            for i, y in enumerate(ys):
                pts += [i * step, y]
        if style in ("area", "step") and idx == 0:
            c.create_polygon(0, h, *pts, w, h, fill=blend(color, bg, 0.25), outline="")
        c.create_line(*pts, fill=color, width=line_w, smooth=(style == "line"))


def _hwnd(tk_window):
    return ctypes.windll.user32.GetParent(tk_window.winfo_id())


def round_corners(tk_window):
    """Скруглённые углы Windows 11 для окна без рамки."""
    try:
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(_hwnd(tk_window), 33, ctypes.byref(pref), 4)
    except Exception:
        pass


def dark_titlebar(tk_window, dark=True):
    try:
        tk_window.update_idletasks()
        on = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(_hwnd(tk_window), 20, ctypes.byref(on), 4)
    except Exception:
        pass


def is_dark(theme=None):
    bg = (theme or T)["bg"]
    r, g, b = (int(bg[i:i + 2], 16) for i in (1, 3, 5))
    return (r * 299 + g * 587 + b * 114) / 1000 < 128


THEMES = {"windows": native_theme(), **THEMES}  # родная тема — первой в списке


_gdi32 = ctypes.windll.gdi32
_gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
_gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
ctypes.windll.user32.SetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
_gdi32.CreateRectRgn.restype = ctypes.c_void_p
_gdi32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
_gdi32.CombineRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
_gdi32.DeleteObject.argtypes = [ctypes.c_void_p]


def round_region(tk_window, radius, w=None, h=None, flat=(False, False, False, False)):
    """Скругление через форму окна — работает и там, где не работает DWM (окна без рамки).
    flat — какие углы оставить прямыми: (левый верхний, правый верхний, левый нижний, правый нижний)."""
    try:
        # сначала применяем отложенную геометрию, иначе SetWindowRgn откатит позицию окна в 0,0
        tk_window.update_idletasks()
        w = int(w or tk_window.winfo_width())
        h = int(h or tk_window.winfo_height())
        rgn = _gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, int(radius * 2), int(radius * 2))
        r = int(radius) + 1
        for on, box in zip(flat, ((0, 0, r, r), (w - r, 0, w, r), (0, h - r, r, h), (w - r, h - r, w, h))):
            if on:  # прямой угол: добавляем к форме квадрат поверх скругления
                sq = _gdi32.CreateRectRgn(*box)
                _gdi32.CombineRgn(rgn, rgn, sq, 2)  # RGN_OR
                _gdi32.DeleteObject(sq)
        ctypes.windll.user32.SetWindowRgn(_hwnd(tk_window), rgn, True)  # регион переходит окну
    except Exception:
        pass
