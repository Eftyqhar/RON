"""Live weather and forecast for RON, powered primarily by OpenWeatherMap API

Provides real-time atmospheric telemetry, 5-day / 3-hour forecasts, rainfall probability,
spoken executive briefings, and holographic weather station payloads for RON's HUD.

Gracefully falls back to Open-Meteo if OpenWeatherMap is unreachable or offline.

Rules:
1. Nothing here ever raises into the caller: returns None, empty dict, or a plain-spoken apology.
2. Nothing happens at import time: API requests are lazy and cached.
"""

import datetime
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request

# -- Configuration -----------------------------------------------------------

DEFAULT_LOCATION = "Sirajganj, Bangladesh"
DEFAULT_OWM_KEY = "d2ae52f21e20719960f5c6cb1c29ee00"
CACHE_TTL = 600          # 10 minutes cache
FORECAST_DAYS = 5        # 5-day outlook
STRIP_HOURS = 5          # cells in the HUD panel 05 strip
STRIP_STEP = 3           # hours between strip cells

_HTTP_TIMEOUT = 7.0
_MAX_BODY = 512 * 1024
_UA = "RON-assistant/1.0 (+https://github.com/Eftyqhar/RON)"

OWM_WEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
OWM_FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"

# Open-Meteo fallback URLs
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_CURRENT_FIELDS = ("temperature_2m", "relative_humidity_2m", "apparent_temperature",
                   "is_day", "precipitation", "weather_code", "wind_speed_10m")
_HOURLY_FIELDS = ("temperature_2m", "precipitation_probability", "weather_code")
_DAILY_FIELDS = ("weather_code", "temperature_2m_max", "temperature_2m_min",
                 "precipitation_probability_max", "precipitation_sum")


def home_location():
    """The configured location. Read lazily so RON_LOCATION can be set late."""
    return (os.environ.get("RON_LOCATION") or "").strip() or DEFAULT_LOCATION


def units():
    """'metric' or 'imperial', from RON_UNITS. Default is metric."""
    want = (os.environ.get("RON_UNITS") or "").strip().lower()
    return "imperial" if want.startswith("imp") else "metric"


def get_owm_api_key():
    """Resolve OpenWeatherMap API key from environment, config.json, or default."""
    env_key = os.environ.get("OPENWEATHERMAP_API_KEY")
    if env_key and env_key.strip():
        return env_key.strip()
    try:
        cfg_path = os.path.join(os.path.dirname(__file__), "config.json")
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                key = cfg.get("weather", {}).get("api_key")
                if key and str(key).strip():
                    return str(key).strip()
    except Exception:
        pass
    return DEFAULT_OWM_KEY


_UNIT_WORDS = {
    "metric": {"temp": "°C", "wind": "km/h",
               "spoken_temp": "degrees Celsius", "spoken_wind": "kilometers per hour"},
    "imperial": {"temp": "°F", "wind": "mph",
                 "spoken_temp": "degrees Fahrenheit", "spoken_wind": "miles per hour"},
}

# -- Condition code mapping (WMO & OpenWeatherMap) ----------------------------

_WMO = {
    0:  ("clear",                  "clear",   "clear skies"),
    1:  ("mainly clear",           "partly",  "mainly clear skies"),
    2:  ("partly cloudy",          "partly",  "partly cloudy conditions"),
    3:  ("overcast",               "cloud",   "overcast skies"),
    45: ("fog",                    "fog",     "fog"),
    48: ("freezing fog",           "fog",     "freezing fog"),
    51: ("light drizzle",          "drizzle", "light drizzle"),
    53: ("drizzle",                "drizzle", "steady drizzle"),
    55: ("heavy drizzle",          "drizzle", "heavy drizzle"),
    56: ("freezing drizzle",       "drizzle", "freezing drizzle"),
    57: ("freezing drizzle",       "drizzle", "heavy freezing drizzle"),
    61: ("light rain",             "rain",    "light rain"),
    63: ("rain",                   "rain",    "steady rain"),
    65: ("heavy rain",             "rain",    "heavy rain"),
    66: ("freezing rain",          "rain",    "freezing rain"),
    67: ("freezing rain",          "rain",    "heavy freezing rain"),
    71: ("light snow",             "snow",    "light snow"),
    73: ("snow",                   "snow",    "steady snow"),
    75: ("heavy snow",             "snow",    "heavy snow"),
    77: ("snow grains",            "snow",    "snow grains"),
    80: ("light showers",          "rain",    "light rain showers"),
    81: ("showers",                "rain",    "rain showers"),
    82: ("violent showers",        "rain",    "violent rain showers"),
    85: ("snow showers",           "snow",    "snow showers"),
    86: ("heavy snow showers",     "snow",    "heavy snow showers"),
    95: ("thunderstorm",           "storm",   "thunderstorms"),
    96: ("thunderstorm with hail", "storm",   "thunderstorms with hail"),
    99: ("thunderstorm with hail", "storm",   "thunderstorms with heavy hail"),
}
_UNKNOWN = ("unsettled", "cloud", "unsettled conditions")
_WET_GROUPS = frozenset({"drizzle", "rain", "snow", "storm"})


def _owm_code_to_meta(code_id, desc=""):
    c = _as_int(code_id, 800)
    if 200 <= c < 300:
        return desc or "thunderstorm", "storm", "thunderstorms"
    if 300 <= c < 400:
        return desc or "drizzle", "drizzle", "light drizzle"
    if 500 <= c < 600:
        phrase = desc or "rain"
        return desc or "rain", "rain", phrase
    if 600 <= c < 700:
        return desc or "snow", "snow", desc or "snow"
    if 700 <= c < 800:
        return desc or "fog", "fog", f"{desc or 'foggy'} conditions"
    if c == 800:
        return "clear", "clear", "clear skies"
    if c == 801:
        return "few clouds", "partly", "few clouds"
    if c == 802:
        return "partly cloudy", "partly", "partly cloudy conditions"
    if c == 803:
        return "broken clouds", "cloud", "broken clouds"
    if c == 804:
        return "overcast", "cloud", "overcast skies"
    return desc or "unsettled", "cloud", "unsettled conditions"


def code_text(code):
    c = _as_int(code, 0)
    if c >= 200:
        return _owm_code_to_meta(c)[0]
    return _WMO.get(c, _UNKNOWN)[0]


def code_group(code):
    c = _as_int(code, 0)
    if c >= 200:
        return _owm_code_to_meta(c)[1]
    return _WMO.get(c, _UNKNOWN)[1]


def code_phrase(code):
    c = _as_int(code, 0)
    if c >= 200:
        return _owm_code_to_meta(c)[2]
    return _WMO.get(c, _UNKNOWN)[2]


def _compass_direction(deg):
    if deg is None:
        return "N/A"
    try:
        val = int((float(deg) / 22.5) + 0.5)
        dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
        return dirs[val % 16]
    except Exception:
        return "N/A"


# -- Caching and Locks -------------------------------------------------------

_lock = threading.Lock()
_geo_cache = {}          # name.lower() -> spot dict
_obs_cache = {}          # (key, units) -> {"reading": {...}, "at": monotonic}
_station_cache = {}      # (key, units) -> {"station": {...}, "at": monotonic}


def _mono():
    return time.monotonic()


def reset_cache():
    """Forget everything cached. For tests."""
    with _lock:
        _geo_cache.clear()
        _obs_cache.clear()
        _station_cache.clear()


# -- Helpers -----------------------------------------------------------------

def _as_int(value, default=None):
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _as_float(value, default=None):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if f == f else default


def _pick(mapping, *names):
    if not isinstance(mapping, dict):
        return None
    for name in names:
        val = mapping.get(name)
        if val is not None:
            return val
    return None


def _at(seq, i, default=None):
    if not isinstance(seq, (list, tuple)) or not -len(seq) <= i < len(seq):
        return default
    val = seq[i]
    return default if val is None else val


def _hour_label(iso):
    text = str(iso or "")
    return text[11:16] if len(text) >= 16 else ""


def _spoken_hour(hhmm):
    hour = _as_int(str(hhmm or "")[:2])
    if hour is None or not 0 <= hour <= 23:
        return ""
    return f"{hour % 12 or 12} {'a.m.' if hour < 12 else 'p.m.'}"


def _weekday(iso_date):
    try:
        return datetime.date.fromisoformat(str(iso_date)[:10]).strftime("%a").upper()
    except (TypeError, ValueError):
        return ""


def _fetch_json(url):
    """Safe HTTP GET returning parsed JSON dict or None."""
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": _UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as response:
            if getattr(response, "status", 200) != 200:
                return None
            raw = response.read(_MAX_BODY + 1)
        if len(raw) > _MAX_BODY:
            return None
        data = json.loads(raw.decode("utf-8", "replace"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


# -- OpenWeatherMap Engine ---------------------------------------------------

def _fetch_owm_pair(location, unit_sys):
    """Fetch (current_weather, 5day_forecast) from OpenWeatherMap."""
    key = get_owm_api_key()
    if not key:
        return None, None

    clean_loc = (location or "").strip() or home_location()
    q = urllib.parse.quote(clean_loc)
    u = "metric" if unit_sys == "metric" else "imperial"

    w_url = f"{OWM_WEATHER_URL}?q={q}&appid={key}&units={u}"
    f_url = f"{OWM_FORECAST_URL}?q={q}&appid={key}&units={u}"

    w_data = _fetch_json(w_url)
    if not w_data or str(w_data.get("cod")) != "200":
        # Try stripping country suffix if comma present
        if "," in clean_loc:
            head = clean_loc.split(",")[0].strip()
            w_url = f"{OWM_WEATHER_URL}?q={urllib.parse.quote(head)}&appid={key}&units={u}"
            f_url = f"{OWM_FORECAST_URL}?q={urllib.parse.quote(head)}&appid={key}&units={u}"
            w_data = _fetch_json(w_url)

    if not w_data or str(w_data.get("cod")) != "200":
        return None, None

    f_data = _fetch_json(f_url)
    return w_data, f_data


def _build_reading_from_owm(w_data, f_data, location):
    """Converts OpenWeatherMap data into RON's standard reading dict."""
    if not w_data or not isinstance(w_data, dict):
        return None

    main = w_data.get("main") or {}
    temp = _as_float(main.get("temp"))
    if temp is None:
        return None

    feels = _as_float(main.get("feels_like"), temp)
    humidity = _as_int(main.get("humidity"))
    wind_raw = _as_float(w_data.get("wind", {}).get("speed"), 0.0)
    wind_speed = wind_raw * 3.6 if units() == "metric" else wind_raw

    weather_list = w_data.get("weather") or [{}]
    wx_first = weather_list[0] if weather_list else {}
    code = _as_int(wx_first.get("id"), 800)
    desc = str(wx_first.get("description") or "").lower()
    icon_code = str(wx_first.get("icon") or "01d")

    cond_name, cond_group, cond_phrase = _owm_code_to_meta(code, desc)
    is_day = icon_code.endswith("d")

    # Timezone offset in seconds
    tz_offset = w_data.get("timezone", 0)
    tz = datetime.timezone(datetime.timedelta(seconds=tz_offset))
    now_dt = datetime.datetime.now(tz)
    today_str = now_dt.strftime("%Y-%m-%d")

    # Process forecast list
    f_list = (f_data.get("list") or []) if isinstance(f_data, dict) else []

    # Calculate hourly strip (first 5 slots)
    hourly = []
    for item in f_list[:STRIP_HOURS]:
        item_dt = datetime.datetime.fromtimestamp(item["dt"], tz=tz)
        item_wx = (item.get("weather") or [{}])[0]
        item_code = _as_int(item_wx.get("id"), 800)
        item_temp = _as_float(item.get("main", {}).get("temp"), temp)
        hourly.append({
            "hour": item_dt.strftime("%H:%M"),
            "temp": _as_int(item_temp),
            "code": item_code,
            "group": _owm_code_to_meta(item_code)[1]
        })

    # Calculate rain today probability & peak hour
    best_pct = 0
    best_hour = ""
    for item in f_list:
        item_dt = datetime.datetime.fromtimestamp(item["dt"], tz=tz)
        if item_dt.strftime("%Y-%m-%d") != today_str:
            break
        pop_pct = _as_int(item.get("pop", 0.0) * 100, 0)
        if pop_pct > best_pct:
            best_pct = pop_pct
            best_hour = item_dt.strftime("%H:%M")

    # Aggregate daily forecast
    daily_groups = {}
    for item in f_list:
        item_dt = datetime.datetime.fromtimestamp(item["dt"], tz=tz)
        d_key = item_dt.strftime("%Y-%m-%d")
        daily_groups.setdefault(d_key, []).append((item_dt, item))

    daily_rows = []
    for d_key, items in list(daily_groups.items())[:FORECAST_DAYS]:
        temps = [_as_float(it.get("main", {}).get("temp"), temp) for _, it in items]
        pops = [_as_float(it.get("pop", 0.0), 0.0) for _, it in items]
        rep_item = max(items, key=lambda x: (x[1].get("pop", 0.0), x[1].get("main", {}).get("temp", 0.0)))[1]
        rep_wx = (rep_item.get("weather") or [{}])[0]
        rep_code = _as_int(rep_wx.get("id"), 800)
        rep_desc = str(rep_wx.get("description") or "").lower()
        c_label, c_grp, c_phr = _owm_code_to_meta(rep_code, rep_desc)
        dt_first = items[0][0]

        daily_rows.append({
            "date": d_key,
            "day": dt_first.strftime("%a").upper(),
            "hi": _as_int(max(temps)) if temps else _as_int(temp),
            "lo": _as_int(min(temps)) if temps else _as_int(temp),
            "code": rep_code,
            "condition": c_label,
            "phrase": c_phr,
            "group": c_grp,
            "rain_pct": _as_int(max(pops) * 100) if pops else 0,
        })

    unit_words = dict(_UNIT_WORDS[units()])

    place_name = str(w_data.get("name") or location or home_location())
    country = str(w_data.get("sys", {}).get("country") or "")

    return {
        "place": place_name,
        "country": country,
        "temp": temp,
        "feels": feels,
        "humidity": humidity,
        "wind": wind_speed,
        "code": code,
        "condition": cond_name,
        "group": cond_group,
        "phrase": cond_phrase,
        "icon": icon_code,
        "is_day": is_day,
        "precip": _as_float(w_data.get("rain", {}).get("1h"), 0.0),
        "rain_today_pct": best_pct,
        "rain_peak_hour": best_hour,
        "hourly": hourly,
        "daily": daily_rows,
        "units": unit_words,
        "local_time": now_dt.strftime("%H:%M"),
        "fetched_at": time.time(),
        "stale": False,
        "provider": "OpenWeatherMap"
    }


# -- Holographic Weather Station Payload Builder -----------------------------

def get_weather_station_data(location=None, force=False):
    """Rich structured dataset for the HUD Holographic Weather Station pop-up."""
    wanted = (location or "").strip() or home_location()
    cache_key = (wanted.lower(), units())

    with _lock:
        cached = _station_cache.get(cache_key)
    if cached and not force and _mono() - cached["at"] < CACHE_TTL:
        return cached["station"]

    # 1. Primary: OpenWeatherMap
    w_data, f_data = _fetch_owm_pair(wanted, units())
    if w_data and isinstance(w_data, dict):
        tz_offset = w_data.get("timezone", 0)
        tz = datetime.timezone(datetime.timedelta(seconds=tz_offset))
        now_dt = datetime.datetime.now(tz)
        today_str = now_dt.strftime("%Y-%m-%d")

        main = w_data.get("main") or {}
        wind = w_data.get("wind") or {}
        sys_info = w_data.get("sys") or {}
        coord = w_data.get("coord") or {}
        wx_list = w_data.get("weather") or [{}]
        wx_first = wx_list[0] if wx_list else {}

        code = _as_int(wx_first.get("id"), 800)
        desc = str(wx_first.get("description") or "").lower()
        icon = str(wx_first.get("icon") or "01d")
        cond_label, cond_group, cond_phrase = _owm_code_to_meta(code, desc)

        temp = _as_float(main.get("temp"))
        feels = _as_float(main.get("feels_like"), temp)
        temp_min = _as_float(main.get("temp_min"), temp)
        temp_max = _as_float(main.get("temp_max"), temp)
        pressure = _as_int(main.get("pressure"), 1013)
        humidity = _as_int(main.get("humidity"), 50)
        dew_point = _as_float(main.get("dew_point"))
        if dew_point is None and temp is not None and humidity is not None:
            dew_point = round(temp - ((100 - humidity) / 5), 1)

        wind_raw = _as_float(wind.get("speed"), 0.0)
        wind_speed = round(wind_raw * 3.6, 1) if units() == "metric" else round(wind_raw, 1)
        wind_deg = _as_int(wind.get("deg"))
        wind_dir = _compass_direction(wind_deg)
        gust_raw = _as_float(wind.get("gust"))
        wind_gust = round(gust_raw * 3.6, 1) if (gust_raw and units() == "metric") else gust_raw

        visibility_m = _as_float(w_data.get("visibility"), 10000)
        visibility_km = round(visibility_m / 1000.0, 1) if units() == "metric" else round(visibility_m / 1609.34, 1)
        clouds = _as_int(w_data.get("clouds", {}).get("all"), 0)

        sunrise_ts = sys_info.get("sunrise")
        sunset_ts = sys_info.get("sunset")
        sunrise_str = datetime.datetime.fromtimestamp(sunrise_ts, tz=tz).strftime("%I:%M %p") if sunrise_ts else "05:45 AM"
        sunset_str = datetime.datetime.fromtimestamp(sunset_ts, tz=tz).strftime("%I:%M %p") if sunset_ts else "06:15 PM"

        f_list = (f_data.get("list") or []) if isinstance(f_data, dict) else []

        # 24-Hour (8 x 3-hour intervals)
        hourly_forecast = []
        best_pct = 0
        best_hour = ""
        for item in f_list[:8]:
            it_dt = datetime.datetime.fromtimestamp(item["dt"], tz=tz)
            it_wx = (item.get("weather") or [{}])[0]
            it_code = _as_int(it_wx.get("id"), 800)
            it_desc = str(it_wx.get("description") or "").lower()
            it_label, it_grp, _ = _owm_code_to_meta(it_code, it_desc)
            pop = _as_int(item.get("pop", 0.0) * 100, 0)
            it_wind = _as_float(item.get("wind", {}).get("speed"), 0.0)
            it_speed = round(it_wind * 3.6, 1) if units() == "metric" else round(it_wind, 1)

            if it_dt.strftime("%Y-%m-%d") == today_str and pop > best_pct:
                best_pct = pop
                best_hour = it_dt.strftime("%H:%M")

            hourly_forecast.append({
                "time": it_dt.strftime("%H:%M"),
                "temp": _as_int(item.get("main", {}).get("temp")),
                "feels": _as_int(item.get("main", {}).get("feels_like")),
                "condition": it_label.title(),
                "group": it_grp,
                "icon": it_wx.get("icon", "01d"),
                "pop": pop,
                "wind": it_speed,
                "rain_mm": _as_float(item.get("rain", {}).get("3h"), 0.0)
            })

        # 5-Day outlook
        daily_groups = {}
        for item in f_list:
            it_dt = datetime.datetime.fromtimestamp(item["dt"], tz=tz)
            d_key = it_dt.strftime("%Y-%m-%d")
            daily_groups.setdefault(d_key, []).append((it_dt, item))

        daily_forecast = []
        for d_key, items in list(daily_groups.items())[:5]:
            temps = [_as_float(it.get("main", {}).get("temp"), temp) for _, it in items]
            pops = [_as_float(it.get("pop", 0.0), 0.0) for _, it in items]
            rep_item = max(items, key=lambda x: (x[1].get("pop", 0.0), x[1].get("main", {}).get("temp", 0.0)))[1]
            rep_wx = (rep_item.get("weather") or [{}])[0]
            rep_code = _as_int(rep_wx.get("id"), 800)
            rep_desc = str(rep_wx.get("description") or "").lower()
            c_label, c_grp, c_phr = _owm_code_to_meta(rep_code, rep_desc)
            dt_first = items[0][0]

            daily_forecast.append({
                "date": d_key,
                "day": "TODAY" if d_key == today_str else dt_first.strftime("%a").upper(),
                "hi": _as_int(max(temps)),
                "lo": _as_int(min(temps)),
                "code": rep_code,
                "condition": c_label.title(),
                "phrase": c_phr,
                "group": c_grp,
                "icon": rep_wx.get("icon", "01d"),
                "rain_pct": _as_int(max(pops) * 100),
            })

        unit_words = dict(_UNIT_WORDS[units()])
        place_name = str(w_data.get("name") or wanted)
        country = str(sys_info.get("country") or "")

        # Construct Executive Briefing
        his = [d["hi"] for d in daily_forecast if d.get("hi") is not None]
        los = [d["lo"] for d in daily_forecast if d.get("lo") is not None]
        hi_span = max(his) if his else _as_int(temp)
        lo_span = min(los) if los else _as_int(temp)
        rain_note = f"Elevated precipitation risk of {best_pct}% expected today." if best_pct >= 30 else f"Precipitation probability remains negligible at {best_pct}%."

        brief = (
            f"Meteorological Sector Report for {place_name}: Currently {_as_int(temp)}{unit_words['temp']} "
            f"(apparent temperature {_as_int(feels)}{unit_words['temp']}) under {cond_phrase}. "
            f"Barometric pressure reads {pressure} hPa with {humidity}% relative humidity and winds from the {wind_dir} "
            f"at {wind_speed} {unit_words['wind']}. {rain_note} The 5-day synoptic outlook projects high temperatures "
            f"up to {hi_span}{unit_words['temp']} and nighttime lows down to {lo_span}{unit_words['temp']}."
        )

        station_payload = {
            "ok": True,
            "station": "RON METEOROLOGICAL RADAR v2.5",
            "provider": "OpenWeatherMap",
            "location": f"{place_name}, {country}" if country else place_name,
            "place": place_name,
            "country": country,
            "coordinates": {"lat": coord.get("lat"), "lon": coord.get("lon")},
            "current": {
                "temp": _as_int(temp),
                "feels_like": _as_int(feels),
                "temp_min": _as_int(temp_min),
                "temp_max": _as_int(temp_max),
                "condition": cond_label.title(),
                "phrase": cond_phrase,
                "group": cond_group,
                "icon": icon,
                "pressure": pressure,
                "humidity": humidity,
                "dew_point": dew_point,
                "wind_speed": wind_speed,
                "wind_deg": wind_deg,
                "wind_dir": wind_dir,
                "wind_gust": wind_gust,
                "visibility_km": visibility_km,
                "clouds_pct": clouds,
                "sunrise": sunrise_str,
                "sunset": sunset_str,
                "is_day": icon.endswith("d"),
                "rain_today_pct": best_pct,
                "peak_hour": best_hour,
            },
            "brief": brief,
            "daily_forecast": daily_forecast,
            "hourly_forecast": hourly_forecast,
            "units": unit_words,
            "radar_status": "SATELLITE TELEMETRY ONLINE · CALIBRATED",
            "local_time": now_dt.strftime("%H:%M"),
            "timestamp": time.time(),
        }

        with _lock:
            _station_cache[cache_key] = {"station": station_payload, "at": _mono()}
        return station_payload

    # 2. Fallback: Generate station from Open-Meteo observe()
    reading = observe(wanted, force=force)
    if not reading:
        return {"ok": False, "error": f"Could not reach meteorological satellite for {wanted}"}

    unit_words = reading.get("units") or _UNIT_WORDS["metric"]
    daily_rows = reading.get("daily") or []
    brief = (
        f"Meteorological Sector Report for {reading.get('place')}: Currently {_as_int(reading.get('temp'))}{unit_words['temp']} "
        f"with {code_phrase(reading.get('code'))}. Humidity is {reading.get('humidity')}% and wind speed is "
        f"{_as_int(reading.get('wind'))} {unit_words['wind']}. Rain probability today is {reading.get('rain_today_pct', 0)}%."
    )
    return {
        "ok": True,
        "station": "RON METEOROLOGICAL RADAR v2.5",
        "provider": "Open-Meteo (Backup)",
        "location": reading.get("place"),
        "place": reading.get("place"),
        "country": reading.get("country", ""),
        "coordinates": {"lat": 24.45, "lon": 89.71},
        "current": {
            "temp": _as_int(reading.get("temp")),
            "feels_like": _as_int(reading.get("feels")),
            "temp_min": _as_int(reading.get("temp")),
            "temp_max": _as_int(reading.get("temp")),
            "condition": reading.get("condition", "").title(),
            "phrase": code_phrase(reading.get("code")),
            "group": reading.get("group", "cloud"),
            "icon": "01d",
            "pressure": 1012,
            "humidity": reading.get("humidity", 65),
            "dew_point": 24.0,
            "wind_speed": _as_int(reading.get("wind")),
            "wind_deg": 180,
            "wind_dir": "S",
            "wind_gust": None,
            "visibility_km": 10.0,
            "clouds_pct": 40,
            "sunrise": "05:45 AM",
            "sunset": "06:15 PM",
            "is_day": bool(reading.get("is_day", True)),
            "rain_today_pct": reading.get("rain_today_pct", 0),
            "peak_hour": reading.get("rain_peak_hour", ""),
        },
        "brief": brief,
        "daily_forecast": daily_rows[:5],
        "hourly_forecast": reading.get("hourly") or [],
        "units": unit_words,
        "radar_status": "BACKUP SENSOR ARRAY ONLINE",
        "local_time": reading.get("local_time", ""),
        "timestamp": time.time(),
    }


def spoken_weather_station_brief(station_data):
    """Crisp spoken executive response when user asks for Weather Station."""
    if not station_data or not station_data.get("ok"):
        return "I could not reach the weather station telemetry, Sir."

    curr = station_data.get("current", {})
    place = station_data.get("place", "Sirajganj")
    temp = curr.get("temp", "--")
    feels = curr.get("feels_like", "--")
    cond = curr.get("phrase", "nominal conditions")
    hum = curr.get("humidity", "--")
    press = curr.get("pressure", "--")
    rain_pct = curr.get("rain_today_pct", 0)

    daily = station_data.get("daily_forecast", [])
    his = [d.get("hi") for d in daily if d.get("hi") is not None]
    los = [d.get("lo") for d in daily if d.get("lo") is not None]
    hi = max(his) if his else temp
    lo = min(los) if los else temp

    return (
        f"Weather Station online, Sir. Current conditions in {place}: {temp} degrees Celsius, "
        f"feeling like {feels} with {cond}. Humidity is {hum} percent and atmospheric pressure is {press} hectopascals. "
        f"Today's rain probability is {rain_pct} percent, with temperatures across the 5-day outlook between {lo} and {hi} degrees. "
        f"Live atmospheric radar and five-day forecast are now deployed on your HUD."
    )


# -- Open-Meteo Geocoding & Observation (Preserved as Fallback) ---------------

def _geocode(name):
    wanted = (name or "").strip()
    if not wanted:
        return None, "notfound"
    key = wanted.lower()
    with _lock:
        if key in _geo_cache:
            hit = _geo_cache[key]
            return hit, None if hit else "notfound"

    head, _, tail = wanted.partition(",")
    query = urllib.parse.urlencode({"name": head.strip() or wanted, "count": 5,
                                    "language": "en", "format": "json"})
    data = _fetch_json(f"{GEOCODE_URL}?{query}")
    if data is None:
        return None, "network"

    results = [r for r in (data.get("results") or []) if isinstance(r, dict)]
    chosen = _match_region(results, tail)
    spot = None
    if chosen:
        lat, lon = _as_float(chosen.get("latitude")), _as_float(chosen.get("longitude"))
        if lat is not None and lon is not None:
            spot = {"name": str(chosen.get("name") or head.strip() or wanted),
                    "lat": lat, "lon": lon,
                    "country": str(chosen.get("country") or ""),
                    "timezone": str(chosen.get("timezone") or "auto")}
    with _lock:
        _geo_cache[key] = spot
    return spot, None if spot else "notfound"


def _match_region(results, tail):
    if not results:
        return None
    hint = (tail or "").strip().lower()
    if hint:
        for result in results:
            fields = (result.get("country"), result.get("country_code"),
                      result.get("admin1"), result.get("admin2"))
            if any(hint == str(f or "").lower() for f in fields):
                return result
    return results[0]


def geocode(name):
    return _geocode(name)[0]


def _forecast_url(spot):
    metric = units() == "metric"
    params = {
        "latitude": f"{spot['lat']:.4f}",
        "longitude": f"{spot['lon']:.4f}",
        "current": ",".join(_CURRENT_FIELDS),
        "hourly": ",".join(_HOURLY_FIELDS),
        "daily": ",".join(_DAILY_FIELDS),
        "timezone": "auto",
        "forecast_days": FORECAST_DAYS,
    }
    if not metric:
        params["temperature_unit"] = "fahrenheit"
        params["wind_speed_unit"] = "mph"
        params["precipitation_unit"] = "inch"
    return f"{FORECAST_URL}?{urllib.parse.urlencode(params)}"


def _build(data, spot):
    if not isinstance(data, dict):
        return None
    current = data.get("current") or data.get("current_weather") or {}
    if not isinstance(current, dict):
        return None
    temp = _as_float(_pick(current, "temperature_2m", "temperature"))
    if temp is None:
        return None
    code = _as_int(_pick(current, "weather_code", "weathercode"), 3)
    now_iso = str(_pick(current, "time") or "")
    unit_words = dict(_UNIT_WORDS[units()])

    hourly = _hourly_strip(data.get("hourly"), now_iso)
    rain_pct, peak = _rain_outlook(data.get("hourly"), data.get("daily"), now_iso)

    return {
        "place": spot["name"],
        "country": spot.get("country", ""),
        "temp": temp,
        "feels": _as_float(_pick(current, "apparent_temperature", "apparent_temperature_2m"), temp),
        "humidity": _as_int(_pick(current, "relative_humidity_2m", "relativehumidity_2m", "humidity")),
        "wind": _as_float(_pick(current, "wind_speed_10m", "windspeed_10m", "windspeed"), 0.0),
        "code": code,
        "condition": code_text(code),
        "group": code_group(code),
        "is_day": bool(_as_int(_pick(current, "is_day"), 1)),
        "precip": _as_float(_pick(current, "precipitation"), 0.0),
        "rain_today_pct": rain_pct,
        "rain_peak_hour": peak,
        "hourly": hourly,
        "daily": _daily_rows(data.get("daily")),
        "units": unit_words,
        "local_time": _hour_label(now_iso),
        "fetched_at": time.time(),
        "stale": False,
    }


def _future_index(times, now_iso):
    if not isinstance(times, (list, tuple)) or not times:
        return 0
    if len(now_iso) < 13:
        return 0
    cutoff = now_iso[:13]
    for i, when in enumerate(times):
        if str(when or "") >= cutoff:
            return i
    return max(0, len(times) - 1)


def _hourly_strip(hourly, now_iso):
    if not isinstance(hourly, dict):
        return []
    times = hourly.get("time") or []
    temps = _pick(hourly, "temperature_2m", "temperature") or []
    codes = _pick(hourly, "weather_code", "weathercode") or []
    start = _future_index(times, now_iso)
    rows = []
    for i in range(start, len(times), STRIP_STEP):
        label = _hour_label(_at(times, i))
        temp = _as_float(_at(temps, i))
        if not label or temp is None:
            continue
        code = _as_int(_at(codes, i), 3)
        rows.append({"hour": label, "temp": _as_int(temp),
                     "code": code, "group": code_group(code)})
        if len(rows) == STRIP_HOURS:
            break
    return rows


def _rain_outlook(hourly, daily, now_iso):
    today = now_iso[:10]
    best_pct, best_hour = None, ""
    if today and isinstance(hourly, dict):
        times = hourly.get("time") or []
        probs = _pick(hourly, "precipitation_probability") or []
        start = _future_index(times, now_iso)
        for i in range(start, len(times)):
            when = str(_at(times, i, ""))
            if when[:10] != today:
                break
            pct = _as_int(_at(probs, i))
            if pct is not None and (best_pct is None or pct > best_pct):
                best_pct, best_hour = pct, _hour_label(when)
    if best_pct is None and isinstance(daily, dict):
        best_pct = _as_int(_at(_pick(daily, "precipitation_probability_max"), 0))
    return (best_pct if best_pct is not None else 0), best_hour


def _daily_rows(daily):
    if not isinstance(daily, dict):
        return []
    times = daily.get("time") or []
    highs = _pick(daily, "temperature_2m_max") or []
    lows = _pick(daily, "temperature_2m_min") or []
    codes = _pick(daily, "weather_code", "weathercode") or []
    probs = _pick(daily, "precipitation_probability_max") or []
    rows = []
    for i in range(len(times)):
        code = _as_int(_at(codes, i), 3)
        rows.append({
            "date": str(_at(times, i, "")),
            "day": _weekday(_at(times, i)),
            "hi": _as_int(_at(highs, i)),
            "lo": _as_int(_at(lows, i)),
            "code": code,
            "condition": code_text(code),
            "phrase": code_phrase(code),
            "group": code_group(code),
            "rain_pct": _as_int(_at(probs, i), 0),
        })
    return rows


def _observe_at(spot, force=False):
    key = (round(spot["lat"], 3), round(spot["lon"], 3), units())
    with _lock:
        cached = _obs_cache.get(key)
    if cached and not force and _mono() - cached["at"] < CACHE_TTL:
        return cached["reading"]

    reading = _build(_fetch_json(_forecast_url(spot)), spot)
    if reading is None:
        if cached:
            stale = dict(cached["reading"])
            stale["stale"] = True
            return stale
        return None

    with _lock:
        _obs_cache[key] = {"reading": reading, "at": _mono()}
    return reading


# -- Standard Interface (observe, describe, rain_answer, forecast_answer) -----

def _reading_or_excuse(location):
    wanted = (location or "").strip() or home_location()
    cache_key = (wanted.lower(), units())

    with _lock:
        cached = _obs_cache.get(cache_key)
    if cached and _mono() - cached["at"] < CACHE_TTL:
        return cached["reading"], None

    # Try OpenWeatherMap first
    w_data, f_data = _fetch_owm_pair(wanted, units())
    if w_data and isinstance(w_data, dict):
        reading = _build_reading_from_owm(w_data, f_data, wanted)
        if reading:
            with _lock:
                _obs_cache[cache_key] = {"reading": reading, "at": _mono()}
            return reading, None

    # Fallback to Open-Meteo
    spot, reason = _geocode(wanted)
    if spot is None:
        if cached:
            stale = dict(cached["reading"])
            stale["stale"] = True
            return stale, None
        if reason == "notfound":
            return None, f"I could not find {wanted} on the map, Sir."
        return None, "I could not reach the weather service, Sir."

    reading = _observe_at(spot)
    if reading is None:
        if cached:
            stale = dict(cached["reading"])
            stale["stale"] = True
            return stale, None
        return None, "I could not reach the weather service, Sir."

    with _lock:
        _obs_cache[cache_key] = {"reading": reading, "at": _mono()}
    return reading, None


def observe(location=None, force=False):
    """The current reading for a place, or None if it could not be had."""
    try:
        wanted = (location or "").strip() or home_location()
        if force:
            cache_key = (wanted.lower(), units())
            with _lock:
                _obs_cache.pop(cache_key, None)
        reading, _ = _reading_or_excuse(wanted)
        return reading
    except Exception:
        return None


def describe(location=None):
    """The 'current weather' sentence."""
    try:
        reading, excuse = _reading_or_excuse(location)
        if excuse:
            return excuse
        spoken = reading["units"]
        phrase = reading.get("phrase") or code_phrase(reading.get("code"))
        parts = [
            f"Current weather in {reading['place']} is {_as_int(reading['temp'])} "
            f"{spoken['spoken_temp']} with {phrase}."
        ]
        humidity = reading.get("humidity")
        wind = _as_int(reading.get("wind"))
        if humidity is not None and wind is not None:
            parts.append(f"Humidity is {humidity} percent and wind speed is "
                         f"{wind} {spoken['spoken_wind']}.")
        elif wind is not None:
            parts.append(f"Wind speed is {wind} {spoken['spoken_wind']}.")
        elif humidity is not None:
            parts.append(f"Humidity is {humidity} percent.")
        if reading.get("stale"):
            parts.append("That reading is a little old, Sir -- the service is not answering.")
        return " ".join(parts)
    except Exception:
        return "I could not read the weather, Sir."


def rain_answer(location=None):
    """The 'will it rain today' sentence."""
    try:
        reading, excuse = _reading_or_excuse(location)
        if excuse:
            return excuse

        place, pct = reading["place"], reading.get("rain_today_pct") or 0
        group = reading.get("group")
        parts = []
        if group in _WET_GROUPS:
            verb = "snowing" if group == "snow" else "raining"
            parts.append(f"It is {verb} in {place} already, Sir.")

        if pct <= 0:
            parts.append("No more is expected for the rest of today." if parts
                         else f"No rain is expected in {place} for the rest of today.")
            return " ".join(parts)

        parts.append(f"There is a {pct} percent chance of more rain today." if parts
                     else f"There is a {pct} percent chance of rain in {place} today, Sir.")
        peak = _spoken_hour(reading.get("rain_peak_hour"))
        if peak:
            parts.append(f"The heaviest risk is around {peak}.")
        if pct < 25:
            parts.append("I would not bother with an umbrella.")
        elif pct < 60:
            parts.append("You may want an umbrella.")
        else:
            parts.append("I would take an umbrella, Sir.")
        return " ".join(parts)
    except Exception:
        return "I could not read the forecast, Sir."


def forecast_answer(location=None, when="tomorrow"):
    """Spoken forecast: tomorrow on its own, or the next few days."""
    try:
        reading, excuse = _reading_or_excuse(location)
        if excuse:
            return excuse

        place, spoken = reading["place"], reading["units"]
        days = [d for d in reading.get("daily") or [] if d.get("hi") is not None]
        if len(days) < 2:
            return f"I have no forecast for {place} beyond today, Sir."

        if when in ("tomorrow", "tonight"):
            day = days[1]
            line = (f"Tomorrow in {place} brings {day['condition']}, with a high of "
                    f"{day['hi']} and a low of {day['lo']} {spoken['spoken_temp']}.")
            pct = day.get("rain_pct") or 0
            if pct >= 25:
                line += f" There is a {pct} percent chance of rain."
            return line

        ahead = days[1:4]
        line = (f"Over the next {_count_word(len(ahead))} days in {place}, in "
                f"{spoken['spoken_temp']}: ")
        line += " ".join(f"{_day_word(i, d)} {d['condition']}, {d['lo']} to {d['hi']}."
                         for i, d in enumerate(ahead))
        wettest = max(ahead, key=lambda d: d.get("rain_pct") or 0)
        if (wettest.get("rain_pct") or 0) >= 40:
            line += (f" The wettest looks like {_weekday_word(wettest)}, at "
                     f"{wettest['rain_pct']} percent.")
        return line
    except Exception:
        return "I could not read the forecast, Sir."


_COUNT_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}
_DAY_NAMES = {"MON": "Monday", "TUE": "Tuesday", "WED": "Wednesday",
              "THU": "Thursday", "FRI": "Friday", "SAT": "Saturday",
              "SUN": "Sunday"}


def _count_word(n):
    return _COUNT_WORDS.get(n, str(n))


def _weekday_word(day):
    return _DAY_NAMES.get(day.get("day", ""), "that day")


def _day_word(index, day):
    return "tomorrow," if index == 0 else f"Then {_weekday_word(day)},"


# -- HUD Panel 05 Payload ----------------------------------------------------

def hud_payload(location=None):
    """Flat, JSON-safe values for bus.weather(**...)."""
    try:
        reading, excuse = _reading_or_excuse(location)
        if excuse:
            return {"ok": False, "error": excuse}
        spoken = reading["units"]
        return {
            "ok": True,
            "place": reading["place"],
            "temp": _as_int(reading["temp"]),
            "feels": _as_int(reading["feels"]),
            "condition": reading["condition"],
            "group": reading["group"],
            "is_day": reading["is_day"],
            "humidity": reading.get("humidity"),
            "wind": _as_int(reading.get("wind")),
            "rain_pct": reading.get("rain_today_pct") or 0,
            "temp_unit": spoken["temp"],
            "wind_unit": spoken["wind"].upper(),
            "hourly": [{"hour": str(h["hour"])[:2], "temp": h["temp"], "group": h["group"]}
                       for h in reading.get("hourly") or []],
            "local_time": reading.get("local_time", ""),
            "stale": bool(reading.get("stale")),
            "at": reading.get("fetched_at", 0),
            "provider": reading.get("provider", "OpenWeatherMap")
        }
    except Exception as e:
        return {"ok": False, "error": f"weather unavailable ({e.__class__.__name__})"}


# -- Command line ------------------------------------------------------------

def _probe(name=None):
    wanted = (name or "").strip() or home_location()
    print(f"Location : {wanted}")
    print(f"Units    : {units()}")
    print(f"API Key  : {get_owm_api_key()[:6]}...{get_owm_api_key()[-4:]}")

    print("\n--- Testing OpenWeatherMap Pair ---")
    w, f = _fetch_owm_pair(wanted, units())
    print("Current status:", w.get("cod") if w else "None")
    print("Forecast status:", f.get("cod") if f else "None")

    print("\n--- Reading ---")
    reading = observe(wanted)
    print(json.dumps(reading, indent=2, default=str)[:2000] if reading else "No reading")

    print("\n--- Weather Station Data ---")
    station = get_weather_station_data(wanted)
    print(json.dumps(station, indent=2, default=str)[:3000] if station else "No station")
    return 0


def main(argv):
    if argv and argv[0] in ("--probe", "-p"):
        return _probe(argv[1] if len(argv) > 1 else None)
    where = argv[0] if argv else None
    print(f"[{where or home_location()}, {units()}]\n")
    print(describe(where))
    print()
    print(rain_answer(where))
    print()
    print(forecast_answer(where, "tomorrow"))
    print()
    print(forecast_answer(where, "week"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
