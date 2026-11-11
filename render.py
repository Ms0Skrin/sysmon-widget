"""Современная отрисовка плиток через PIL: сглаживание (рендер в 2× и уменьшение),
скруглённые карточки, плавные кривые с градиентной заливкой, кольца, иконки Fluent."""
import math
import os
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SS = 2  # суперсэмплинг
WINDOWS = sys.platform == "win32"
FONTS_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")

GLYPHS = {
    "cpu": "\uE950", "cores": "\uEEA1", "temp": "\uE9CA", "ram": "\uEEA0", "gpu": "\uF211",
    "vram": "\uE964", "power": "\uE945", "disk": "\uEDA2", "net": "\uE774", "fan": "\uE9F3",
    "clock": "\uEC4A", "load": "\uE9D9", "uptime": "\uE823", "data": "\uEDA2",
    "time": "\uE823", "weather": "\uE706", "battery": "\uE83F", "procs": "\uE9F5",
    "lock": "\uE72E",
}
if not WINDOWS:  # Linux: значки из Nerd Font (Font Awesome + Material Design)
    GLYPHS = {
        "cpu": "\uF2DB", "cores": "\uF0C9", "temp": "\uF2C9", "ram": "\U000F035B", "gpu": "\U000F08AE",
        "vram": "\uF1C0", "power": "\uF0E7", "disk": "\uF0A0", "net": "\uF0EC", "fan": "\U000F0210",
        "clock": "\uF0E4", "load": "\uF201", "uptime": "\uF017", "data": "\uF0A0",
        "time": "\uF017", "weather": "\uF0C2", "battery": "\uF241", "procs": "\uF03A",
        "lock": "\uF023",
    }
SENSOR_GLYPH = {"Temperature": "temp", "Load": "load", "Fan": "fan", "Power": "power", "Clock": "clock",
                "Voltage": "power", "Current": "power", "Data": "data", "SmallData": "data",
                "Throughput": "net", "Control": "fan"}

_font_cache = {}


_fc_cache = {}


def _fc(*args):
    """fontconfig: строка ответа fc-match / fc-list (с кэшем)."""
    if args not in _fc_cache:
        try:
            _fc_cache[args] = subprocess.run(list(args), capture_output=True, text=True, timeout=5).stdout
        except Exception:
            _fc_cache[args] = ""
    return _fc_cache[args]


def icon_font_file():
    """Linux: файл шрифта со значками Nerd Font (или None, если такого нет)."""
    best = None
    for line in _fc("fc-list", ":charset=f2db", "file", "family", "style").splitlines():
        path = line.split(":")[0].strip()
        if "Nerd Font" not in line or not path:
            continue
        rank = (0 if "Symbols" in line else 1, 0 if "Regular" in line else 1,
                0 if "Mono" not in line and "Propo" not in line else 1, path)
        if best is None or rank < best:
            best = rank
    return best[3] if best else None


def _linux_font(kind, size, family):
    """Шрифты через fontconfig: Inter (или системный без засечек), моноширинный для «Терминала»."""
    try:
        if kind == "icon":
            path = icon_font_file()
            return ImageFont.truetype(path, size) if path else ImageFont.load_default()
        base = "monospace" if family == "Consolas" else "Inter"
        weight = ("bold" if family == "Consolas" else "semibold") if kind == "value" else "regular"
        path, _, index = _fc("fc-match", "-f", "%{file}|%{index}", f"{base}:{weight}").partition("|")
        f = ImageFont.truetype(path, size, index=int(index or 0) & 0xFFFF)
        if kind == "value":
            try:  # вариативный шрифт: нужная насыщенность задаётся осью
                f.set_variation_by_name("Bold" if family == "Consolas" else "SemiBold")
            except Exception:
                pass
        return f
    except Exception:
        return ImageFont.load_default()


def _font(kind, size, family="Segoe UI"):
    """kind: value | label | icon. Шрифты Windows 11 с запасными вариантами."""
    key = (kind, size, family)
    if key in _font_cache:
        return _font_cache[key]
    if not WINDOWS:
        f = _font_cache[key] = _linux_font(kind, size, family)
        return f
    f = None
    if kind == "icon":
        for name in ("SegoeIcons.ttf", "segmdl2.ttf"):
            try:
                f = ImageFont.truetype(os.path.join(FONTS_DIR, name), size)
                break
            except OSError:
                pass
    elif family == "Consolas":
        try:
            f = ImageFont.truetype(os.path.join(FONTS_DIR, "consolab.ttf" if kind == "value" else "consola.ttf"), size)
        except OSError:
            pass
    else:
        try:
            f = ImageFont.truetype(os.path.join(FONTS_DIR, "SegUIVar.ttf"), size)
            f.set_variation_by_name("Display Semibold" if kind == "value" else "Text")
        except Exception:
            f = None
        if f is None:
            for name in (("seguisb.ttf",) if kind == "value" else ("segoeui.ttf",)):
                try:
                    f = ImageFont.truetype(os.path.join(FONTS_DIR, name), size)
                except OSError:
                    pass
    f = f or ImageFont.load_default()
    _font_cache[key] = f
    return f


def hex_rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def mix(a, b, t):
    a, b = hex_rgb(a) if isinstance(a, str) else a, hex_rgb(b) if isinstance(b, str) else b
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _smooth(pts, steps=6):
    """Catmull-Rom: ломаная → плавная кривая."""
    if len(pts) < 3:
        return pts
    out = []
    p = [pts[0]] + pts + [pts[-1]]
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        for k in range(steps):
            t = k / steps
            t2, t3 = t * t, t * t * t
            x = 0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 +
                       (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 +
                       (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            out.append((x, y))
    out.append(pts[-1])
    return out


def _fit(d, text, font, maxw):
    """Обрезать текст с «…», чтобы влез в maxw."""
    if not text or d.textlength(text, font=font) <= maxw:
        return text
    while text and d.textlength(text + "…", font=font) > maxw:
        text = text[:-1]
    return text.rstrip() + "…"


_card_cache = {}


def _card(w, h, th, s):
    """Подложка карточки: скругление, лёгкий вертикальный градиент, тонкая обводка.
    Одинаковые подложки берутся из кэша — вызывающий не должен менять картинку (только convert)."""
    key = (w, h, s, th["bg"], th["panel"], th["fg"])
    img = _card_cache.get(key)
    if img is None:
        if len(_card_cache) > 64:
            _card_cache.clear()
        img = _card_cache[key] = _card_new(w, h, th, s)
    return img


def _card_new(w, h, th, s):
    W, H, r = w * SS, h * SS, int(14 * s * SS)
    img = Image.new("RGB", (W, H), hex_rgb(th["bg"]))
    top, bot = mix(th["panel"], th["fg"], 0.035), hex_rgb(th["panel"])
    grad = Image.new("RGB", (1, H))
    for y in range(H):
        grad.putpixel((0, y), mix(top, bot, y / max(1, H - 1)))
    grad = grad.resize((W, H))
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, W - 1, H - 1), r, fill=255)
    img.paste(grad, (0, 0), mask)
    ImageDraw.Draw(img).rounded_rectangle((0, 0, W - 1, H - 1), r, outline=mix(th["panel"], th["fg"], 0.09),
                                          width=max(1, SS))
    return img


def _graph(img, box, series_list, colors, top, style, th, s):
    x0, y0, x1, y1 = box
    gw, gh = x1 - x0, y1 - y0
    n = len(series_list[0]) if series_list else 0
    if n < 2 or gw <= 0:
        return
    lw = max(2, int(th.get("line_w", 1.5) * s * SS * 1.15))
    for idx, (series, color) in enumerate(zip(series_list, colors)):
        rgb = hex_rgb(color)
        ys = [y1 - min(max(v, 0) / top, 1.0) * gh for v in series]
        xs = [x0 + i * gw / (n - 1) for i in range(n)]
        if style == "bars":
            bw = gw / n
            d = ImageDraw.Draw(img, "RGBA")
            for i, y in enumerate(ys):
                bx = x0 + i * bw + bw * 0.18
                if idx:
                    continue
                d.rounded_rectangle((bx, min(y, y1 - SS * 2), bx + bw * 0.64, y1), int(bw * 0.3),
                                    fill=rgb + (255,))
            continue
        if style == "step":
            pts = []
            for i in range(n):
                if i:
                    pts.append((xs[i], ys[i - 1]))
                pts.append((xs[i], ys[i]))
        else:
            pts = _smooth(list(zip(xs, ys)))
        if style in ("area", "step") and idx == 0:
            # градиентная заливка под кривой: сверху цвет, к низу — в ноль
            layer = Image.new("RGBA", img.size, rgb + (0,))
            alpha = Image.new("L", (1, gh + 1))
            for yy in range(gh + 1):
                alpha.putpixel((0, yy), int(110 * (1 - yy / gh) ** 1.4))
            alpha = alpha.resize((gw + 1, gh + 1))
            mask = Image.new("L", img.size, 0)
            ImageDraw.Draw(mask).polygon([(x0, y1)] + pts + [(x1, y1)], fill=255)
            full = Image.new("L", img.size, 0)
            full.paste(alpha, (x0, y0))
            from PIL import ImageChops
            layer.putalpha(ImageChops.multiply(mask, full))
            img.alpha_composite(layer)
        d = ImageDraw.Draw(img, "RGBA")
        line_rgb = rgb if idx == 0 else mix(th["panel"], color, 0.65)
        d.line(pts, fill=line_rgb + (255,), width=lw if idx == 0 else max(1, lw - SS), joint="curve")
        if idx == 0:  # светящаяся точка на конце
            ex, ey = pts[-1]
            gr = 7 * s * SS
            blur = 4 * s * SS
            m = int(gr + blur * 3) + 2  # свечение размываем на маленьком кусочке, а не на всей плитке
            ox, oy = int(ex) - m, int(ey) - m
            glow = Image.new("RGBA", (2 * m, 2 * m), (0, 0, 0, 0))
            ImageDraw.Draw(glow).ellipse((ex - ox - gr, ey - oy - gr, ex - ox + gr, ey - oy + gr),
                                         fill=rgb + (120,))
            glow = glow.filter(ImageFilter.GaussianBlur(blur))
            box = (max(0, -ox), max(0, -oy), min(2 * m, img.width - ox), min(2 * m, img.height - oy))
            if box[2] > box[0] and box[3] > box[1]:
                img.alpha_composite(glow.crop(box), (ox + box[0], oy + box[1]))
            r = 3.2 * s * SS
            d = ImageDraw.Draw(img, "RGBA")
            d.ellipse((ex - r, ey - r, ex + r, ey + r), fill=rgb + (255,),
                      outline=hex_rgb(th["panel"]) + (255,), width=max(1, int(1.2 * s * SS)))


def _ring(img, cx, cy, radius, frac, color, th, s):
    d = ImageDraw.Draw(img, "RGBA")
    w = int(7 * s * SS)
    box = (cx - radius, cy - radius, cx + radius, cy + radius)
    d.arc(box, 0, 360, fill=mix(th["panel"], th["fg"], 0.10) + (255,), width=w)
    frac = max(0.0, min(1.0, frac))
    if frac > 0.002:
        start, end = -90, -90 + 360 * frac
        d.arc(box, start, end, fill=hex_rgb(color) + (255,), width=w)
        rr = radius - w / 2
        for ang in (start, end):  # скруглённые концы дуги
            a = math.radians(ang)
            px, py = cx + rr * math.cos(a), cy + rr * math.sin(a)
            d.ellipse((px - w / 2, py - w / 2, px + w / 2, py + w / 2), fill=hex_rgb(color) + (255,))


def tile_image(th, s, w, h, *, title, glyph, main, sub="", value_color=None, series=None, colors=None,
               top=100, style="area", bars=None, ring=None):
    """Картинка плитки. ring — доля 0..1 для стиля «Кольца»."""
    fam = th.get("font", "Segoe UI")
    img = _card(w, h, th, s).convert("RGBA")
    d = ImageDraw.Draw(img, "RGBA")
    pad = int(12 * s * SS)
    accent = colors[0] if colors else th["accent"]

    # иконка в «таблетке» + заголовок
    isz = int(22 * s * SS)
    d.rounded_rectangle((pad, pad, pad + isz, pad + isz), int(7 * s * SS),
                        fill=mix(th["panel"], accent, 0.2) + (255,))
    gf = _font("icon", int(12 * s * SS))
    d.text((pad + isz / 2, pad + isz / 2), GLYPHS.get(glyph, GLYPHS["load"]), font=gf,
           fill=hex_rgb(accent) + (255,), anchor="mm")
    lf = _font("label", int(11 * s * SS), fam)
    W0 = img.size[0]
    tx0 = pad + isz + int(8 * s * SS)
    d.text((tx0, pad + isz / 2), _fit(d, title, lf, W0 - pad - tx0), font=lf,
           fill=hex_rgb(th["muted"]) + (255,), anchor="lm")
    text_w = W0 - 2 * pad - (int(76 * s * SS) if ring is not None else 0)

    vf = _font("value", int(22 * s * SS), fam)
    vy = pad + isz + int(8 * s * SS)
    d.text((pad, vy), _fit(d, main, vf, text_w), font=vf, fill=hex_rgb(value_color or th["fg"]) + (255,),
           anchor="lt")
    if sub:
        sf = _font("label", int(10.5 * s * SS), fam)
        d.text((pad, vy + int(29 * s * SS)), _fit(d, sub, sf, text_w), font=sf,
               fill=hex_rgb(th["muted"]) + (255,), anchor="lt")

    W, H = img.size
    if ring is not None:
        rad = int(min(H - 2 * pad, 64 * s * SS) / 2)
        _ring(img, W - pad - rad, H - pad - rad, rad - int(2 * s * SS), ring, accent, th, s)
    elif bars is not None:
        n = max(1, len(bars))
        gx0, gx1 = pad, W - pad
        gy1 = H - pad
        gh = int(26 * s * SS)
        bw = (gx1 - gx0) / n
        for i, v in enumerate(bars):
            bh = max(SS * 2, v / 100 * gh)
            col = th["hot"] if v >= 90 else accent
            d.rounded_rectangle((gx0 + i * bw + bw * 0.15, gy1 - bh, gx0 + i * bw + bw * 0.85, gy1),
                                max(1, int(bw * 0.35)), fill=hex_rgb(col) + (255,))
    elif series:
        gy0 = H - pad - int(30 * s * SS)
        _graph(img, (pad, gy0, W - pad, H - pad), series, colors, top, style, th, s)

    return img.resize((w, h), Image.LANCZOS).convert("RGB")


def header_image(th, s, w, title, right, locked=False):
    """Заголовок виджета: имя ПК слева (с замком, если панель закреплена), «таблетка» аптайма справа."""
    fam = th.get("font", "Segoe UI")
    h = int(30 * s)
    W, H = w * SS, h * SS
    img = Image.new("RGBA", (W, H), hex_rgb(th["bg"]) + (255,))
    d = ImageDraw.Draw(img, "RGBA")
    tf = _font("value", int(14 * s * SS), fam)
    d.text((0, H / 2), title, font=tf, fill=hex_rgb(th["fg"]) + (255,), anchor="lm")
    if locked:  # значок из шрифта иконок: эмодзи в обычном шрифте рисуется пустым квадратом
        d.text((d.textlength(title, font=tf) + int(8 * s * SS), H / 2), GLYPHS["lock"],
               font=_font("icon", int(10 * s * SS)), fill=hex_rgb(th["muted"]) + (255,), anchor="lm")
    rf = _font("label", int(10.5 * s * SS), fam)
    gf = _font("icon", int(10 * s * SS))
    tw = d.textlength(right, font=rf)
    pw = tw + int(30 * s * SS)
    ph = int(22 * s * SS)
    x1 = W - 1
    d.rounded_rectangle((x1 - pw, (H - ph) / 2, x1, (H + ph) / 2), ph / 2, fill=hex_rgb(th["panel"]) + (255,))
    d.text((x1 - pw + int(14 * s * SS), H / 2), GLYPHS["uptime"], font=gf, fill=hex_rgb(th["accent"]) + (255,),
           anchor="mm")
    d.text((x1 - int(10 * s * SS), H / 2), right, font=rf, fill=hex_rgb(th["muted"]) + (255,), anchor="rm")
    return img.resize((w, h), Image.LANCZOS).convert("RGB")


def apps_button_image(th, s, hover=False):
    size = int(30 * s)
    S = size * SS
    img = Image.new("RGBA", (S, S), hex_rgb(th["bg"]) + (255,))
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle((0, 0, S - 1, S - 1), int(9 * s * SS),
                        fill=hex_rgb(th["hover"] if hover else th["panel"]) + (255,))
    step, r = S / 4, 2.4 * s * SS
    for i in range(3):
        for j in range(3):
            cx, cy = step * (i + 1), step * (j + 1)
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=hex_rgb(th["accent"]) + (255,))
    return img.resize((size, size), Image.LANCZOS).convert("RGB")


def card_image(th, s, w, h, *, glyph, title, device, ring, ring_text, rows, accent, bars=None,
               ring_label="нагрузка"):
    """Большая плитка-карточка: кольцо нагрузки слева, таблица показателей справа, потоки снизу."""
    fam = th.get("font", "Segoe UI")
    img = _card(w, h, th, s).convert("RGBA")
    d = ImageDraw.Draw(img, "RGBA")
    W, H = img.size
    pad = int(14 * s * SS)
    isz = int(22 * s * SS)
    d.rounded_rectangle((pad, pad, pad + isz, pad + isz), int(7 * s * SS), fill=mix(th["panel"], accent, 0.2) + (255,))
    d.text((pad + isz / 2, pad + isz / 2), GLYPHS.get(glyph, GLYPHS["load"]), font=_font("icon", int(12 * s * SS)),
           fill=hex_rgb(accent) + (255,), anchor="mm")
    tx = pad + isz + int(8 * s * SS)
    d.text((tx, pad + isz / 2), title, font=_font("value", int(13 * s * SS), fam), fill=hex_rgb(th["fg"]) + (255,),
           anchor="lm")
    tw = d.textlength(title, font=_font("value", int(13 * s * SS), fam))
    dev_font = _font("label", int(10.5 * s * SS), fam)
    dev = device or ""
    max_dev = W - pad - (tx + tw + int(10 * s * SS))
    while dev and d.textlength(dev, font=dev_font) > max_dev:
        dev = dev[:-2]
    if dev != (device or ""):
        dev = dev.rstrip() + "…"
    d.text((W - pad, pad + isz / 2), dev, font=dev_font, fill=hex_rgb(th["muted"]) + (255,), anchor="rm")

    top = pad + isz + int(12 * s * SS)
    bars_h = int(30 * s * SS) if bars else 0
    body_h = H - top - pad - (bars_h + int(10 * s * SS) if bars else 0)
    rad = int(min(body_h, 96 * s * SS) / 2)
    cx, cy = pad + rad, top + body_h / 2
    _ring(img, cx, cy, rad - int(4 * s * SS), ring if ring is not None else 0, accent, th, s)
    d = ImageDraw.Draw(img, "RGBA")
    d.text((cx, cy - int(2 * s * SS)), ring_text, font=_font("value", int(18 * s * SS), fam),
           fill=hex_rgb(th["fg"]) + (255,), anchor="mm")
    d.text((cx, cy + int(16 * s * SS)), ring_label, font=_font("label", int(9 * s * SS), fam),
           fill=hex_rgb(th["muted"]) + (255,), anchor="mm")

    # таблица показателей: 2 колонки
    gx0 = pad + 2 * rad + int(16 * s * SS)
    colw = (W - pad - gx0) / 2
    rows = rows[:8]
    nrows = max(1, math.ceil(len(rows) / 2))
    rh = body_h / nrows
    lf, vf = _font("label", int(9.5 * s * SS), fam), _font("value", int(13 * s * SS), fam)
    for i, row in enumerate(rows):
        label, value = row[0], row[1]
        color = row[2] if len(row) > 2 and row[2] else th["fg"]
        c, r_ = i % 2, i // 2
        x = gx0 + c * colw
        y = top + r_ * rh + rh / 2
        d.text((x, y - int(9 * s * SS)), _fit(d, label, lf, colw - int(6 * s * SS)), font=lf,
               fill=hex_rgb(th["muted"]) + (255,), anchor="lm")
        d.text((x, y + int(8 * s * SS)), _fit(d, value, vf, colw - int(6 * s * SS)), font=vf,
               fill=hex_rgb(color) + (255,), anchor="lm")

    if bars:
        n = len(bars)
        gy1 = H - pad
        bw = (W - 2 * pad) / n
        for i, v in enumerate(bars):
            bh = max(SS * 2, v / 100 * bars_h)
            col = th["hot"] if v >= 90 else accent
            d.rounded_rectangle((pad + i * bw + bw * 0.15, gy1 - bh, pad + i * bw + bw * 0.85, gy1),
                                max(1, int(bw * 0.35)), fill=hex_rgb(col) + (255,))
    return img.resize((w, h), Image.LANCZOS).convert("RGB")
