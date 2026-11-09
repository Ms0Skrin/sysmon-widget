"""Фирменные картинки мастера установки (светлая и тёмная версии, несколько масштабов).

python packaging/installer_art.py <папка вывода>
"""
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
THEMES = {
    "dark": {"top": (22, 24, 29), "bottom": (14, 16, 20), "fg": (232, 234, 237), "muted": (139, 145, 156),
             "tile": (31, 35, 43), "accent": (79, 163, 255)},
    "light": {"top": (243, 244, 247), "bottom": (226, 231, 240), "fg": (29, 35, 48), "muted": (107, 114, 128),
              "tile": (255, 255, 255), "accent": (37, 99, 235)},
}
PALETTE = [(79, 163, 255), (176, 124, 255), (118, 185, 0), (255, 122, 89)]
SCALES = (1.0, 1.25, 1.5, 2.0)


def font(name, size):
    for f in name:
        try:
            return ImageFont.truetype(os.path.join(FONTS, f), size)
        except OSError:
            pass
    return ImageFont.load_default()


def gradient(w, h, top, bottom):
    img = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        img.putpixel((0, y), tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return img.resize((w, h))


def dots_icon(size, th, radius_k=0.26):
    S = size * 4
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, S - 1, S - 1), int(S * radius_k), fill=th["tile"] + (255,))
    step, r = S / 4, S * 0.075
    for i in range(3):
        for j in range(3):
            cx, cy = step * (i + 1), step * (j + 1)
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=th["accent"] + (255,))
    return img.resize((size, size), Image.LANCZOS)


def wizard_image(th, k):
    """Большая картинка слева (164×314 при 100 %)."""
    w, h = round(164 * k), round(314 * k)
    S = 3
    W, H = w * S, h * S
    img = gradient(W, H, th["top"], th["bottom"]).convert("RGBA")
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-W * 0.4, H * 0.05, W * 1.1, H * 0.6), fill=th["accent"] + (70,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(W * 0.18)))
    d = ImageDraw.Draw(img)
    # декоративные графики внизу — «плитки» виджета
    for n, col in enumerate(PALETTE[:3]):
        y1 = H * (0.70 + n * 0.095)
        pts = [(x, y1 - (math.sin(x / W * 9 + n * 1.7) * 0.5 + 0.5) * H * 0.045) for x in range(0, W + 1, S * 2)]
        d.line(pts, fill=col + (230,), width=int(2.2 * S * k), joint="curve")
    icon = dots_icon(int(64 * k * S), th)
    img.alpha_composite(icon, (int(W * 0.5 - icon.width / 2), int(H * 0.17)))
    f1 = font(("seguisb.ttf", "segoeuib.ttf"), int(21 * k * S))
    f2 = font(("segoeui.ttf",), int(9 * k * S))
    d.text((W / 2, H * 0.46), "SysMon", font=f1, fill=th["fg"] + (255,), anchor="mm")
    d.text((W / 2, H * 0.535), "Widget", font=f1, fill=th["fg"] + (255,), anchor="mm")
    d.text((W / 2, H * 0.60), "панели · датчики · приложения", font=f2, fill=th["muted"] + (255,), anchor="mm")
    return img.resize((w, h), Image.LANCZOS).convert("RGB")


def small_image(th, k, bg):
    """Маленькая картинка в шапке (55×55 при 100 %) на фоне мастера."""
    s = round(55 * k)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))  # прозрачный фон — подходит к светлой и тёмной шапке
    icon = dots_icon(int(s * 0.86), th)
    img.alpha_composite(icon, ((s - icon.width) // 2, (s - icon.height) // 2))
    return img


def main(out):
    os.makedirs(out, exist_ok=True)
    backs = {"dark": (32, 32, 32), "light": (255, 255, 255)}  # фон шапки мастера Inno
    for mode, th in THEMES.items():
        for k in SCALES:
            tag = f"{mode}_{int(k * 100)}"
            wizard_image(th, k).save(os.path.join(out, f"wizard_{tag}.bmp"))
            small_image(th, k, backs[mode]).save(os.path.join(out, f"small_{tag}.png"))
    print("art ->", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "build/art")
