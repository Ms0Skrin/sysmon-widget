"""Мелкие помощники GTK: картинки PIL → текстуры, стили по теме, диалоги."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from theme import T, blend, is_dark  # noqa: E402

ICON_FONTS = '"Symbols Nerd Font", "Hack Nerd Font", "FiraCode Nerd Font", "JetBrainsMono Nerd Font"'


def texture(img):
    """Картинка PIL → Gdk.Texture."""
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    fmt = Gdk.MemoryFormat.R8G8B8 if img.mode == "RGB" else Gdk.MemoryFormat.R8G8B8A8
    return Gdk.MemoryTexture.new(img.width, img.height, fmt, GLib.Bytes.new(img.tobytes()),
                                 img.width * len(img.mode))


def picture(img=None):
    p = Gtk.Picture()
    p.set_can_shrink(False)
    p.set_halign(Gtk.Align.START)
    p.set_valign(Gtk.Align.START)
    if img is not None:
        p.set_paintable(texture(img))
    return p


_provider = None


def apply_theme_css():
    """Общие стили окон виджета в цветах текущей темы."""
    global _provider
    display = Gdk.Display.get_default()
    if _provider is not None:
        Gtk.StyleContext.remove_provider_for_display(display, _provider)
    bg, fg, muted, accent, panel = T["bg"], T["fg"], T["muted"], T["accent"], T["panel"]
    hover = blend(accent, bg, 0.22)
    border = blend(fg, bg, 0.14)
    mono = "monospace" if T.get("font") == "Consolas" else "Inter, sans-serif"
    css = f"""
window.sysmon-panel, window.sysmon-launcher, window.sysmon-probe {{ background: transparent; }}
.sm-body {{ background-color: {bg}; color: {fg}; font-family: {mono}; }}
.sm-backdrop {{ background-color: rgba(0, 0, 0, 0.38); }}
.sm-card {{ background-color: {bg}; color: {fg}; border-radius: 22px; border: 1px solid {border};
           padding: 18px; font-family: {mono}; }}
.sm-card entry {{ background-color: {panel}; color: {fg}; border: 1px solid {border}; border-radius: 12px;
                 min-height: 38px; padding: 0 12px; box-shadow: none; outline: none; caret-color: {accent}; }}
.sm-card entry:focus-within {{ border-color: {accent}; }}
.sm-card scrolledwindow, .sm-card flowbox, .sm-card viewport {{ background: transparent; }}
.sm-card flowboxchild {{ padding: 0; border-radius: 14px; outline: none; }}
.sm-app {{ background: none; border: none; box-shadow: none; color: {fg}; border-radius: 14px;
          padding: 10px 6px 8px 6px; font-weight: normal; }}
.sm-app:hover, .sm-card flowboxchild:focus-visible .sm-app, .sm-app:focus-visible {{ background-color: {hover}; }}
.sm-app label {{ font-size: 0.86em; }}
.sm-badge {{ color: {muted}; font-size: 0.72em; }}
.sm-dim {{ color: {muted}; font-size: 0.86em; }}
.sm-icon {{ font-family: {ICON_FONTS}; color: {accent}; min-width: 20px; }}
popover.sm-menu {{ background: transparent; }}
popover.sm-menu > contents {{ background-color: {bg}; color: {fg}; border-radius: 12px;
                             border: 1px solid {border}; padding: 5px; box-shadow: none;
                             font-family: {mono}; }}
popover.sm-menu button {{ background: none; border: none; box-shadow: none; color: {fg};
                         padding: 5px 10px; border-radius: 7px; min-height: 0; font-weight: normal; }}
popover.sm-menu button:hover {{ background-color: {hover}; }}
popover.sm-menu separator {{ background-color: {border}; margin: 4px 6px; min-height: 1px; }}
.sm-menu-title {{ color: {muted}; font-size: 0.82em; font-weight: bold; padding: 4px 10px 2px 10px; }}
.sm-menu-info {{ color: {muted}; font-size: 0.82em; padding: 3px 10px; }}
"""
    _provider = Gtk.CssProvider()
    _provider.load_from_string(css)
    Gtk.StyleContext.add_provider_for_display(display, _provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
    settings = Gtk.Settings.get_default()
    if settings is not None:  # окно настроек — тёмное или светлое под тему виджета
        try:
            scheme = Gtk.InterfaceColorScheme.DARK if is_dark() else Gtk.InterfaceColorScheme.LIGHT
            settings.set_property("gtk-interface-color-scheme", scheme)
        except (AttributeError, TypeError):  # GTK старше 4.20
            settings.set_property("gtk-application-prefer-dark-theme", is_dark())


def clear(box):
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


# ---------- диалоги ----------
def message(parent, title, text):
    dlg = Gtk.AlertDialog(message=title, detail=text, buttons=["OK"], modal=True)
    dlg.show(parent)


def confirm(parent, title, text, on_yes, yes="Да", no="Нет"):
    dlg = Gtk.AlertDialog(message=title, detail=text, buttons=[no, yes], modal=True,
                          cancel_button=0, default_button=1)

    def done(d, res):
        try:
            if d.choose_finish(res) == 1:
                on_yes()
        except GLib.Error:
            pass

    dlg.choose(parent, None, done)


def ask_string(parent, title, prompt, initial, on_ok):
    """Окно с одним полем ввода; on_ok(text) вызывается для непустой строки."""
    win = Gtk.Window(title=title, modal=parent is not None, resizable=False)
    if parent is not None:
        win.set_transient_for(parent)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    for side in ("top", "bottom", "start", "end"):
        getattr(box, f"set_margin_{side}")(16)
    lbl = Gtk.Label(label=prompt, xalign=0, wrap=True)
    entry = Gtk.Entry(text=initial or "", width_chars=34)
    row = Gtk.Box(spacing=8, halign=Gtk.Align.END)
    cancel = Gtk.Button(label="Отмена")
    ok = Gtk.Button(label="OK")
    ok.add_css_class("suggested-action")

    def accept(*_a):
        text = entry.get_text().strip()
        win.destroy()
        if text:
            on_ok(text)

    cancel.connect("clicked", lambda *_a: win.destroy())
    ok.connect("clicked", accept)
    entry.connect("activate", accept)
    keys = Gtk.EventControllerKey()
    keys.connect("key-pressed", lambda _c, kv, *_a: (win.destroy(), True)[1] if kv == Gdk.KEY_Escape else False)
    win.add_controller(keys)
    row.append(cancel)
    row.append(ok)
    for w in (lbl, entry, row):
        box.append(w)
    win.set_child(box)
    win.present()
    entry.grab_focus()
    return win
