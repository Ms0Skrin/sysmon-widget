"""Погода для плитки: open-meteo.com (бесплатно, без ключа). Город задаётся в настройках;
наружу уходит только его название."""
import json
import threading
import time
import urllib.parse
import urllib.request

REFRESH = 30 * 60
UA = {"User-Agent": "sysmon-widget"}
WMO = {
    0: "ясно", 1: "малооблачно", 2: "переменная облачность", 3: "пасмурно", 45: "туман", 48: "изморозь",
    51: "морось", 53: "морось", 55: "сильная морось", 56: "ледяная морось", 57: "ледяная морось",
    61: "небольшой дождь", 63: "дождь", 65: "сильный дождь", 66: "ледяной дождь", 67: "ледяной дождь",
    71: "небольшой снег", 73: "снег", 75: "сильный снег", 77: "снежная крупа",
    80: "ливень", 81: "ливень", 82: "сильный ливень", 85: "снегопад", 86: "сильный снегопад",
    95: "гроза", 96: "гроза с градом", 99: "гроза с градом",
}


def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
        return json.load(r)


def fetch(city):
    """→ {"main": "+5 °C", "sub": "Москва · пасмурно"}"""
    q = urllib.parse.quote(city)
    geo = _get(f"https://geocoding-api.open-meteo.com/v1/search?name={q}&count=1&language=ru")
    if not geo.get("results"):
        return {"main": "—", "sub": f"город «{city}» не найден"}
    g = geo["results"][0]
    cur = _get("https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}"
               "&current=temperature_2m,weather_code,wind_speed_10m&wind_speed_unit=ms".format(**g))["current"]
    t = round(cur["temperature_2m"])
    desc = WMO.get(cur.get("weather_code"), "")
    wind = cur.get("wind_speed_10m")
    sub = " · ".join(x for x in (g.get("name", city), desc, f"{wind:.0f} м/с" if wind is not None else "") if x)
    return {"main": f"{t:+d} °C" if t else "0 °C", "sub": sub}


class Weather(threading.Thread):
    """Обновляет погоду раз в полчаса и сразу после смены города. needed() — есть ли плитка погоды."""

    def __init__(self, get_city, needed):
        super().__init__(daemon=True)
        self.get_city, self.needed = get_city, needed
        self.data = {"main": "—", "sub": "укажи город: Настройки → Панели"}

    def get(self):
        return dict(self.data)

    def run(self):
        city, stamp = None, 0.0
        while True:
            want = (self.get_city() or "").strip()
            if not want:
                city, self.data = None, {"main": "—", "sub": "укажи город: Настройки → Панели"}
            elif self.needed() and (want != city or time.time() - stamp > REFRESH):
                try:
                    self.data = fetch(want)
                    city, stamp = want, time.time()
                except Exception:
                    if want != city:
                        self.data = {"main": "—", "sub": "нет связи с сервером погоды"}
                    stamp = time.time() - REFRESH + 120  # повторим через пару минут
                    city = want
            time.sleep(3)
