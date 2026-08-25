"""Live weather and forecast for RON, from Open-Meteo.

No API key, no signup, no new dependency -- `urllib.request` from the standard
library. Two rules, inherited from `history.py`:

1. **Nothing here ever raises into the caller.** A dead network, a DNS failure, a
   rate limit, malformed JSON, a missing field: all of it comes back as `None` or
   a plain-spoken apology. Weather is a nicety; it must never take RON down.
2. **Nothing happens at import time.** No network call until something asks, so
   `RON_LOCATION` can be set after the import and `--probe` can point this
   wherever it likes.

Everything outside this file talks to the reading dict built by `_build()`, never
to Open-Meteo's own field names. If the upstream API renames something, this is
the only file that changes -- and `_pick()` already accepts both of Open-Meteo's
historical naming conventions, so most renames cost nothing at all.

    python weather.py                 # the three spoken answers, no mic, no LLM
    python weather.py --probe         # raw JSON for the default location
    python weather.py --probe Tokyo   # raw JSON for somewhere else
"""

import datetime
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request

# -- configuration -----------------------------------------------------------

DEFAULT_LOCATION = "Sirajganj, Bangladesh"
CACHE_TTL = 600          # seconds; finer than the data actually changes
FORECAST_DAYS = 4        # today + three days, enough for "this week"
STRIP_HOURS = 5          # cells in the HUD's hourly strip
STRIP_STEP = 3           # hours between those cells

_HTTP_TIMEOUT = 6.0
_MAX_BODY = 512 * 1024   # a bounded read: no endpoint gets to stream at us
_UA = "RON-assistant/1.0 (+https://github.com/Eftyqhar/RON)"

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

_CURRENT_FIELDS = ("temperature_2m", "relative_humidity_2m", "apparent_temperature",
                   "is_day", "precipitation", "weather_code", "wind_speed_10m")
_HOURLY_FIELDS = ("temperature_2m", "precipitation_probability", "weather_code")
_DAILY_FIELDS = ("weather_code", "temperature_2m_max", "temperature_2m_min",
                 "precipitation_probability_max", "precipitation_sum")


def home_location():
    """The configured location. Read lazily so `RON_LOCATION` can be set late."""
    return (os.environ.get("RON_LOCATION") or "").strip() or DEFAULT_LOCATION


def units():
    """'metric' or 'imperial', from `RON_UNITS`. Anything unrecognised is metric."""
    want = (os.environ.get("RON_UNITS") or "").strip().lower()
    return "imperial" if want.startswith("imp") else "metric"


_UNIT_WORDS = {
    "metric": {"temp": "°C", "wind": "km/h",
               "spoken_temp": "degrees Celsius", "spoken_wind": "kilometers per hour"},
    "imperial": {"temp": "°F", "wind": "mph",
                 "spoken_temp": "degrees Fahrenheit", "spoken_wind": "miles per hour"},
}

# -- WMO weather codes -------------------------------------------------------
# code -> (label for the HUD, icon group, phrase that reads well after "with")
#
# The third column is why there are three strings and not one: "with partly
# cloudy conditions" is the wording asked for, but "with thunderstorms
# conditions" is not English. Each code carries its own finished phrase.

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

# Groups that mean water is falling right now.
_WET_GROUPS = frozenset({"drizzle", "rain", "snow", "storm"})


def code_text(code):
    """WMO code -> 'partly cloudy'. Unknown codes read 'unsettled'."""
    return _WMO.get(_as_int(code), _UNKNOWN)[0]


def code_group(code):
    """WMO code -> icon group: clear, partly, cloud, fog, drizzle, rain, snow, storm."""
    return _WMO.get(_as_int(code), _UNKNOWN)[1]


def code_phrase(code):
    """WMO code -> a phrase that reads naturally after the word 'with'."""
    return _WMO.get(_as_int(code), _UNKNOWN)[2]


# -- state -------------------------------------------------------------------
# The lock guards the two caches only, and is never held across a network call:
# a six-second fetch must not block the HUD's poller from reading a cached
# value. Two threads racing to refresh the same place duplicate one request,
# which is a far better trade than serialising every reader behind HTTP.

_lock = threading.Lock()
_geo_cache = {}          # name.lower() -> spot dict, or None for 'no such place'
_obs_cache = {}          # (lat, lon, units) -> {"reading": {...}, "at": monotonic}


def _mono():
    """Monotonic clock, isolated so the tests can wind it forward."""
    return time.monotonic()


# -- helpers -----------------------------------------------------------------

def _pick(mapping, *names):
    """First present, non-null value among `names`.

    Open-Meteo has shipped two naming conventions over its life -- the current
    `weather_code` / `wind_speed_10m` / `relative_humidity_2m` and the older
    `weathercode` / `windspeed_10m` / `relativehumidity_2m`. Accepting both means
    the exact spelling upstream is not load-bearing here.
    """
    if not isinstance(mapping, dict):
        return None
    for name in names:
        value = mapping.get(name)
        if value is not None:
            return value
    return None


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
    return f if f == f else default          # NaN in JSON -> default


def _at(seq, i, default=None):
    """`seq[i]`, tolerating a non-list, a short list, or a null element."""
    if not isinstance(seq, (list, tuple)) or not -len(seq) <= i < len(seq):
        return default
    value = seq[i]
    return default if value is None else value


def _hour_label(iso):
    """'2026-08-23T15:00' -> '15:00'. Anything unparseable -> ''."""
    text = str(iso or "")
    return text[11:16] if len(text) >= 16 else ""


def _spoken_hour(hhmm):
    """'16:00' -> '4 p.m.', which SAPI reads correctly."""
    hour = _as_int(str(hhmm or "")[:2])
    if hour is None or not 0 <= hour <= 23:
        return ""
    return f"{hour % 12 or 12} {'a.m.' if hour < 12 else 'p.m.'}"


def _weekday(iso_date):
    """'2026-08-25' -> 'TUE'. Pure date arithmetic, no timezone involved."""
    try:
        return datetime.date.fromisoformat(str(iso_date)[:10]).strftime("%a").upper()
    except (TypeError, ValueError):
        return ""


def _fetch_json(url):
    """The only HTTP call in this module. Returns a dict, or None.

    Every fault -- DNS, timeout, 4xx, 5xx, oversized or malformed body -- arrives
    here as None. The test suite replaces this function wholesale, which is the
    reason nothing else in the file touches urllib.
    """
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": _UA, "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as response:
            if getattr(response, "status", 200) != 200:
                return None
            raw = response.read(_MAX_BODY + 1)
        if len(raw) > _MAX_BODY:
            return None
        data = json.loads(raw.decode("utf-8", "replace"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


# -- geocoding ---------------------------------------------------------------

def _geocode(name):
    """Resolve a place name. Returns (spot, reason).

    `reason` is None on success, 'notfound' when the geocoder had no match, and
    'network' when the lookup itself failed. The distinction matters: "I could
    not find Tokyo on the map" is the wrong thing to say when the real problem is
    that the network is down.
    """
    wanted = (name or "").strip()
    if not wanted:
        return None, "notfound"

    key = wanted.lower()
    with _lock:
        if key in _geo_cache:
            hit = _geo_cache[key]
            return hit, None if hit else "notfound"

    # "Sirajganj, Bangladesh" -> search for "Sirajganj", then prefer a result in
    # Bangladesh. The geocoder itself only understands the bare name.
    head, _, tail = wanted.partition(",")
    query = urllib.parse.urlencode({"name": head.strip() or wanted, "count": 5,
                                    "language": "en", "format": "json"})
    data = _fetch_json(f"{GEOCODE_URL}?{query}")
    if data is None:
        return None, "network"                # not cached: this may work later

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
        _geo_cache[key] = spot                # a definitive miss is worth caching
    return spot, None if spot else "notfound"


def _match_region(results, tail):
    """Pick the result matching a ', <country or region>' suffix, else the first."""
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
    """Resolve a place name to `{name, lat, lon, country, timezone}`, or None."""
    return _geocode(name)[0]


# -- observation -------------------------------------------------------------

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
    """Turn an Open-Meteo response into RON's own reading shape, or None.

    This is the whole boundary. Nothing above this function knows an Open-Meteo
    field name, and nothing below it knows what RON does with the numbers.
    """
    if not isinstance(data, dict):
        return None

    current = data.get("current") or data.get("current_weather") or {}
    if not isinstance(current, dict):
        return None

    temp = _as_float(_pick(current, "temperature_2m", "temperature"))
    if temp is None:
        return None                            # without a temperature there is no reading

    code = _as_int(_pick(current, "weather_code", "weathercode"), 3)
    now_iso = str(_pick(current, "time") or "")
    unit_words = dict(_UNIT_WORDS[units()])

    hourly = _hourly_strip(data.get("hourly"), now_iso)
    rain_pct, peak = _rain_outlook(data.get("hourly"), data.get("daily"), now_iso)

    return {
        "place": spot["name"],
        "country": spot.get("country", ""),
        "temp": temp,
        "feels": _as_float(_pick(current, "apparent_temperature",
                                 "apparent_temperature_2m"), temp),
        "humidity": _as_int(_pick(current, "relative_humidity_2m",
                                  "relativehumidity_2m", "humidity")),
        "wind": _as_float(_pick(current, "wind_speed_10m", "windspeed_10m",
                                "windspeed"), 0.0),
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
    """Index of the first hourly slot at or after the reading's own local time.

    Anchoring on the API's `current.time` rather than this machine's clock keeps
    the arithmetic right for a city in another timezone -- and ISO strings sort
    correctly, so no parsing is needed.
    """
    if not isinstance(times, (list, tuple)) or not times:
        return 0
    if len(now_iso) < 13:
        return 0
    cutoff = now_iso[:13]                      # 'YYYY-MM-DDTHH'
    for i, when in enumerate(times):
        if str(when or "") >= cutoff:
            return i
    return max(0, len(times) - 1)


def _hourly_strip(hourly, now_iso):
    """The next few hours, `STRIP_STEP` apart, for the HUD strip."""
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
    """(chance of rain for the rest of today, the hour of peak risk).

    Deliberately the rest of *today*, not the whole day: at eleven at night, a
    downpour that happened at three in the afternoon is not an answer to "will it
    rain today".
    """
    today = now_iso[:10]
    best_pct, best_hour = None, ""

    # Without a local date there is no way to tell where today ends, and scanning
    # the whole 72-hour window would report a Thursday storm as today's risk.
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
        # No usable hours left today -- fall back to the daily figure.
        best_pct = _as_int(_at(_pick(daily, "precipitation_probability_max"), 0))

    return (best_pct if best_pct is not None else 0), best_hour


def _daily_rows(daily):
    """Per-day highs, lows and rain chance. Spoken only; the HUD does not use it."""
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
    """Cached reading for a resolved spot. Serves a stale one if a refresh fails."""
    key = (round(spot["lat"], 3), round(spot["lon"], 3), units())

    with _lock:
        cached = _obs_cache.get(key)
    if cached and not force and _mono() - cached["at"] < CACHE_TTL:
        return cached["reading"]

    reading = _build(_fetch_json(_forecast_url(spot)), spot)
    if reading is None:
        if cached:
            # Better a reading from twenty minutes ago than an apology.
            stale = dict(cached["reading"])
            stale["stale"] = True
            return stale
        return None

    with _lock:
        _obs_cache[key] = {"reading": reading, "at": _mono()}
    return reading


def _reading_or_excuse(location):
    """(reading, spoken apology). Exactly one of the two is None."""
    wanted = (location or "").strip() or home_location()
    spot, reason = _geocode(wanted)
    if spot is None:
        if reason == "notfound":
            return None, f"I could not find {wanted} on the map, Sir."
        return None, "I could not reach the weather service, Sir."

    reading = _observe_at(spot)
    if reading is None:
        return None, "I could not reach the weather service, Sir."
    return reading, None


def observe(location=None, force=False):
    """The current reading for a place, or None if it could not be had."""
    try:
        wanted = (location or "").strip() or home_location()
        spot = _geocode(wanted)[0]
        return _observe_at(spot, force) if spot else None
    except Exception:
        return None


# -- spoken answers ----------------------------------------------------------

def describe(location=None):
    """The 'current weather' sentence."""
    try:
        reading, excuse = _reading_or_excuse(location)
        if excuse:
            return excuse
        spoken = reading["units"]
        parts = [
            f"Current weather in {reading['place']} is {_as_int(reading['temp'])} "
            f"{spoken['spoken_temp']} with {code_phrase(reading['code'])}."
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

        if when == "tomorrow":
            day = days[1]
            line = (f"Tomorrow in {place} brings {day['condition']}, with a high of "
                    f"{day['hi']} and a low of {day['lo']} {spoken['spoken_temp']}.")
            pct = day.get("rain_pct") or 0
            if pct >= 25:
                line += f" There is a {pct} percent chance of rain."
            return line

        # The unit is spoken once, in the preamble. Repeating "degrees Celsius"
        # after every high and low turns a three-day outlook into a chant.
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
    # "tomorrow," follows a colon and stays lowercase; the rest follow a full
    # stop, so they are capitalised.
    return "tomorrow," if index == 0 else f"Then {_weekday_word(day)},"


# -- the HUD -----------------------------------------------------------------

def hud_payload(location=None):
    """Flat, JSON-safe values for `bus.weather(**...)`.

    Always returns a dict. On failure that dict is `{"ok": False, "error": ...}`,
    so panel 05 can say OFFLINE instead of showing stale numbers as though they
    were live.
    """
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
            "hourly": [{"hour": h["hour"][:2], "temp": h["temp"], "group": h["group"]}
                       for h in reading.get("hourly") or []],
            "local_time": reading.get("local_time", ""),
            "stale": bool(reading.get("stale")),
            "at": reading.get("fetched_at", 0),
        }
    except Exception as e:
        return {"ok": False, "error": f"weather unavailable ({e.__class__.__name__})"}


def reset_cache():
    """Forget everything cached. For the tests and for `--probe`."""
    with _lock:
        _geo_cache.clear()
        _obs_cache.clear()


# -- command line ------------------------------------------------------------

def _probe(name=None):
    """Print the raw upstream JSON, so the field names can be verified by eye."""
    wanted = (name or "").strip() or home_location()
    head, _, _ = wanted.partition(",")
    geo_url = f"{GEOCODE_URL}?" + urllib.parse.urlencode(
        {"name": head.strip() or wanted, "count": 5, "language": "en", "format": "json"})

    print(f"location : {wanted}")
    print(f"units    : {units()}")
    print(f"\nGET {geo_url}\n")
    geo = _fetch_json(geo_url)
    print(json.dumps(geo, indent=2)[:4000] if geo else "  (no response)")

    spot = _geocode(wanted)[0]
    if not spot:
        print("\nCould not resolve that place; stopping here.")
        return 1
    print(f"\nresolved : {spot}")

    url = _forecast_url(spot)
    print(f"\nGET {url}\n")
    data = _fetch_json(url)
    if not data:
        print("  (no response)")
        return 1
    print(json.dumps(data, indent=2)[:8000])

    reading = _build(data, spot)
    print("\nparsed reading:")
    print(json.dumps(reading, indent=2, default=str) if reading
          else "  PARSE FAILED -- a field name has changed; fix _build()/_pick().")
    return 0 if reading else 1


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
