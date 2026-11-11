#!/bin/sh
# Установка SysMon Widget для Linux (Wayland: niri, Hyprland, Sway, KDE Plasma…) в домашнюю папку:
# команда sysmon-widget, ярлык в меню приложений, значок. Права администратора не нужны.
#   ./install-linux.sh              установить
#   ./install-linux.sh --autostart  установить и включить запуск при входе в систему
#   ./install-linux.sh --remove     убрать команду, ярлык, значок и автозапуск (настройки остаются)
set -e
DIR=$(cd "$(dirname "$0")" && pwd)
APP_ID=io.github.ms0skrin.SysMonWidget
BIN="$HOME/.local/bin/sysmon-widget"
DESKTOP="${XDG_DATA_HOME:-$HOME/.local/share}/applications/$APP_ID.desktop"
ICON="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps/sysmon-widget.png"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/sysmon-widget.desktop"

if [ "$1" = "--remove" ]; then
    "$BIN" --quit 2>/dev/null || true
    rm -f "$BIN" "$DESKTOP" "$ICON" "$AUTOSTART"
    echo "SysMon Widget удалён. Настройки остались в ${XDG_CONFIG_HOME:-$HOME/.config}/sysmon-widget."
    exit 0
fi

if ! python3 - <<'PY'
import ctypes.util, sys
missing = []
try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk, Gtk4LayerShell  # noqa: F401
except Exception as ex:
    missing.append(f"GTK4 + gtk4-layer-shell + PyGObject ({ex})")
for mod, name in (("psutil", "psutil"), ("PIL", "Pillow")):
    try:
        __import__(mod)
    except ImportError:
        missing.append(name)
if missing:
    print("Не хватает: " + "; ".join(missing))
    sys.exit(1)
PY
then
    echo
    echo "Поставь зависимости и запусти скрипт ещё раз:"
    echo "  Arch / CachyOS:  sudo pacman -S --needed gtk4 gtk4-layer-shell python-gobject python-psutil python-pillow"
    echo "  Fedora:          sudo dnf install gtk4 gtk4-layer-shell python3-gobject python3-psutil python3-pillow"
    echo "  Debian / Ubuntu: sudo apt install libgtk-4-1 libgtk4-layer-shell0 gir1.2-gtk4layershell-1.0 python3-gi python3-psutil python3-pil"
    exit 1
fi

mkdir -p "$(dirname "$BIN")" "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
cat > "$BIN" <<EOF
#!/bin/sh
exec python3 "$DIR/widget.py" "\$@"
EOF
chmod +x "$BIN"
python3 -c "
import sys
from PIL import Image
im = Image.open(sys.argv[1]); im.load()
im.convert('RGBA').resize((256, 256), Image.LANCZOS).save(sys.argv[2])" "$DIR/assets/app.ico" "$ICON"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=SysMon Widget
Comment=Монитор системы и панель приложений на рабочем столе
Exec=$BIN
Icon=sysmon-widget
Terminal=false
Categories=System;Monitor;
StartupWMClass=$APP_ID
Actions=launcher;settings;quit;

[Desktop Action launcher]
Name=Панель приложений
Exec=$BIN --launcher

[Desktop Action settings]
Name=Настройки
Exec=$BIN --settings

[Desktop Action quit]
Name=Выход
Exec=$BIN --quit
EOF
if [ "$1" = "--autostart" ]; then
    mkdir -p "$(dirname "$AUTOSTART")"
    sed -e '/^Actions=/,$d' "$DESKTOP" > "$AUTOSTART"
    echo "NoDisplay=true" >> "$AUTOSTART"
fi

echo "Готово. Запуск: sysmon-widget  (или «SysMon Widget» в меню приложений)."
echo "Панель приложений: sysmon-widget --launcher — назначь команду на сочетание клавиш, например в niri:"
echo '    binds { Ctrl+Alt+Space { spawn "sysmon-widget" "--launcher"; } }'
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "Внимание: $HOME/.local/bin нет в PATH — добавь его или вызывай $BIN." ;; esac
