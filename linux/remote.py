"""Быстрая передача команды уже работающему виджету (горячая клавиша должна срабатывать сразу):
нужен только D-Bus, без загрузки GTK, PIL и остального."""
import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .config import APP_ID  # noqa: E402


def forward(argv):
    """Виджет запущен → отдать ему командную строку и вернуть код возврата; иначе None."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        running = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                "NameHasOwner", GLib.Variant("(s)", (APP_ID,)), None,
                                Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
    except GLib.Error:
        return None
    if not running:
        return None
    app = Gio.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
    return app.run(argv)
