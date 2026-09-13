"""Proactive Morning / Evening Executive Briefing ("Good morning, RON") for RON.

Orchestrates an executive start-of-day or end-of-day audio-visual briefing:
- Real-time weather observation & rain forecast (via `weather.py`)
- Multi-calendar temporal dates: English, Bangla, and Hijri (via `clock.py`)
- Priority unread email summary (via `email_notify.py`)
- Hardware battery & power status (via `psutil`)
- Network connectivity health & latency check
- Optional ambient focus / relaxation audio (via `tools.play_youtube`)
- Holographic HUD dashboard broadcasting (via `bus.briefing`)

Two rules inherited from the rest of RON:
1. Never raises into the caller. Any individual subsystem fault (offline IMAP,
   weather timeout, battery not present) is handled gracefully with fallback values.
2. Nothing at import time. Settings from `config.json` are read lazily.
"""

import json
import os
import socket
import time
import urllib.request

import bus
import clock
import email_notify
import tools
import weather

try:
    import psutil
except ImportError:
    psutil = None


# Default ambient soundtrack queries
DEFAULT_SOUNDTRACKS = {
    "morning": "morning lofi hip hop beats to wake up to",
    "evening": "evening ambient jazz lofi chill beats",
}


def _load_briefing_config() -> dict:
    """Read briefing preferences from config.json."""
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return cfg.get("briefing") or {}
        except Exception as e:
            print(f"[briefing] Could not load config.json: {e}")
    return {}


def get_battery_status() -> dict:
    """Retrieve laptop battery percentage and AC charging status.
    
    Returns a dict with:
      - 'available': bool
      - 'percent': int | None
      - 'plugged': bool | None
      - 'description': str
    """
    if psutil is None:
        return {
            "available": False,
            "percent": None,
            "plugged": True,
            "description": "Desktop Workstation (AC Power)",
        }

    try:
        battery = psutil.sensors_battery()
        if battery is None:
            return {
                "available": False,
                "percent": None,
                "plugged": True,
                "description": "Desktop Workstation (AC Power)",
            }

        pct = int(battery.percent)
        plugged = bool(battery.power_plugged)
        if plugged:
            desc = f"Battery at {pct}%, plugged into AC power"
        else:
            desc = f"Battery at {pct}%, running on internal power"

        return {
            "available": True,
            "percent": pct,
            "plugged": plugged,
            "description": desc,
        }
    except Exception as e:
        return {
            "available": False,
            "percent": None,
            "plugged": True,
            "description": f"Power monitor unavailable ({e})",
        }


def get_network_ping() -> dict:
    """Perform a lightweight network latency check against Cloudflare DNS (1.1.1.1).
    
    Returns a dict with:
      - 'online': bool
      - 'latency_ms': int | None
      - 'description': str
    """
    start = time.time()
    try:
        # Quick TCP ping to 1.1.1.1 on port 53 (DNS) with 1.5s timeout
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.5)
        sock.connect(("1.1.1.1", 53))
        sock.close()
        latency = int((time.time() - start) * 1000)
        return {
            "online": True,
            "latency_ms": latency,
            "description": f"Online ({latency}ms latency)",
        }
    except Exception:
        # Fallback to HTTP HEAD
        try:
            req = urllib.request.Request("https://1.1.1.1", method="HEAD")
            with urllib.request.urlopen(req, timeout=2.0):
                latency = int((time.time() - start) * 1000)
                return {
                    "online": True,
                    "latency_ms": latency,
                    "description": f"Online ({latency}ms latency)",
                }
        except Exception:
            return {
                "online": False,
                "latency_ms": None,
                "description": "Network offline or unreachable",
            }


def gather_briefing_data(mode: str = "morning", lang: str = "en") -> dict:
    """Aggregate data from weather, clock, email, and hardware monitors."""
    mode = "morning" if mode != "evening" else "evening"

    # 1. Temporal data across 3 calendars
    try:
        temporal = clock.snapshot()
    except Exception:
        temporal = {"english": "Today", "time": "08:00"}

    # 2. Weather observation & rain forecast
    try:
        w_reading = weather.observe()
        w_desc = weather.describe()
        w_rain = weather.rain_answer()
        weather_data = {
            "ok": bool(w_reading),
            "temp": w_reading.get("temp") if w_reading else None,
            "place": w_reading.get("place") if w_reading else "Current City",
            "condition": w_reading.get("condition") if w_reading else "Clear",
            "humidity": w_reading.get("humidity") if w_reading else None,
            "wind": w_reading.get("wind") if w_reading else None,
            "rain_chance": w_reading.get("rain_today_pct", 0) if w_reading else 0,
            "summary": w_desc,
            "rain_summary": w_rain,
        }
    except Exception:
        weather_data = {
            "ok": False,
            "temp": None,
            "place": "Current City",
            "condition": "Unavailable",
            "humidity": None,
            "wind": None,
            "rain_chance": 0,
            "summary": "Weather service unavailable.",
            "rain_summary": "",
        }

    # 3. Email inbox inspection
    try:
        inbox = email_notify.check_inbox(filter_type="unread", limit=3, lang=lang)
    except Exception:
        inbox = {
            "ok": False,
            "total_unread": 0,
            "messages": [],
            "summary_spoken": "Email service unavailable.",
        }

    # 4. Hardware & network telemetry
    battery = get_battery_status()
    network = get_network_ping()

    return {
        "mode": mode,
        "temporal": temporal,
        "weather": weather_data,
        "inbox": inbox,
        "battery": battery,
        "network": network,
    }


def generate_spoken_briefing(data: dict, mode: str = "morning", lang: str = "en") -> str:
    """Compose natural, charismatic JARVIS-style spoken speech."""
    is_bn = lang == "bn"
    mode = data.get("mode", mode)
    temporal = data.get("temporal", {})
    w = data.get("weather", {})
    inbox = data.get("inbox", {})
    battery = data.get("battery", {})
    network = data.get("network", {})

    if is_bn:
        # Bengali spoken response
        greeting = "শুভ সকাল, স্যার।" if mode == "morning" else "শুভ সন্ধ্যা, স্যার।"
        parts = [greeting]

        # Date
        bn_date = temporal.get("bangla", "")
        if bn_date:
            parts.append(f"আজকের বাংলা তারিখ {bn_date}।")

        # Weather
        if w.get("ok") and w.get("temp") is not None:
            parts.append(f"{w.get('place')}তে বর্তমান তাপমাত্রা {int(w.get('temp'))} ডিগ্রি সেলসিয়াস।")
            rain_pct = w.get("rain_chance", 0)
            if rain_pct > 20:
                parts.append(f"আজ বৃষ্টির সম্ভাবনা প্রায় {rain_pct} শতাংশ।")

        # Emails
        unread_cnt = inbox.get("total_unread", 0)
        if unread_cnt > 0:
            parts.append(f"আপনার ইনবক্সে {unread_cnt}টি অপঠিত ইমেইল রয়েছে।")
        else:
            parts.append("ইনবক্স সম্পূর্ণ পরিষ্কার রয়েছে।")

        # Battery / Network
        if battery.get("available") and battery.get("percent") is not None:
            if not battery.get("plugged") and battery.get("percent") < 30:
                parts.append(f"সতর্কতা: ব্যাটারি লেভেল {battery.get('percent')} শতাংশে নেমে এসেছে।")

        parts.append("সকল সিস্টেম প্রস্তুত আছে, স্যার। দিনটি ভালো কাটুক।" if mode == "morning" else "সকল সিস্টেম প্রস্তুত আছে, স্যার। শুভরাত্রি।")
        return " ".join(parts)

    # English spoken response
    greeting = "Good morning, Sir." if mode == "morning" else "Good evening, Sir."
    parts = [greeting]

    # Date
    eng_date = temporal.get("english", "")
    if eng_date:
        parts.append(f"Today is {eng_date}.")

    # Weather
    if w.get("ok") and w.get("temp") is not None:
        parts.append(f"The temperature in {w.get('place')} is {int(w.get('temp'))} degrees with {w.get('condition', 'clear skies').lower()}.")
        rain_pct = w.get("rain_chance", 0)
        if rain_pct > 20:
            parts.append(f"There is a {rain_pct} percent chance of precipitation today.")
        elif mode == "morning":
            parts.append("No rain is expected today.")

    # Emails
    unread_cnt = inbox.get("total_unread", 0)
    if unread_cnt == 1:
        parts.append("You have 1 unread email awaiting your attention.")
    elif unread_cnt > 1:
        parts.append(f"You have {unread_cnt} unread emails waiting in your inbox.")
    else:
        parts.append("Your inbox is completely clear.")

    # Battery check
    if battery.get("available") and battery.get("percent") is not None:
        if not battery.get("plugged") and battery.get("percent") <= 25:
            parts.append(f"Notice: Battery is low at {battery.get('percent')} percent.")
        elif mode == "morning":
            parts.append(f"Battery is charged at {battery.get('percent')} percent.")

    # Network check
    if not network.get("online"):
        parts.append("Warning: network connection appears to be offline.")

    # Closing
    if mode == "morning":
        parts.append("All primary systems are calibrated and ready. Have a productive day, Sir.")
    else:
        parts.append("All systems operational. Have a relaxing evening, Sir.")

    return " ".join(parts)


def hud_payload(data: dict, spoken: str, audio_active: bool = False) -> dict:
    """Format full JSON-serialisable payload for the HUD overlay."""
    temporal = data.get("temporal", {})
    weather_info = data.get("weather", {})
    inbox = data.get("inbox", {})
    battery = data.get("battery", {})
    network = data.get("network", {})
    mode = data.get("mode", "morning")

    return {
        "mode": mode,
        "title": f"EXECUTIVE {'MORNING' if mode == 'morning' else 'EVENING'} BRIEFING",
        "spoken": spoken,
        "temporal": {
            "time": temporal.get("time", ""),
            "weekday": temporal.get("weekday", ""),
            "english": temporal.get("english", ""),
            "bangla": temporal.get("bangla", ""),
            "arabic": temporal.get("arabic", ""),
        },
        "weather": {
            "ok": weather_info.get("ok", False),
            "temp": weather_info.get("temp"),
            "place": weather_info.get("place", ""),
            "condition": weather_info.get("condition", ""),
            "humidity": weather_info.get("humidity"),
            "wind": weather_info.get("wind"),
            "rain_chance": weather_info.get("rain_chance", 0),
        },
        "inbox": {
            "ok": inbox.get("ok", False),
            "total_unread": inbox.get("total_unread", 0),
            "messages": [
                {
                    "from": m.get("from_name") or m.get("from", "Unknown"),
                    "subject": m.get("subject", "(No Subject)"),
                    "snippet": m.get("snippet", ""),
                }
                for m in inbox.get("messages", [])[:3]
            ],
        },
        "telemetry": {
            "battery_available": battery.get("available", False),
            "battery_percent": battery.get("percent"),
            "battery_plugged": battery.get("plugged", True),
            "battery_desc": battery.get("description", ""),
            "network_online": network.get("online", True),
            "network_ping_ms": network.get("latency_ms"),
            "network_desc": network.get("description", ""),
            "audio_active": audio_active,
            "audio_desc": "Silent / Standby" if not audio_active else ("Evening Soundscape Active" if mode == "evening" else "Audio Stream Active"),
        },
    }


def execute_briefing(mode: str = "morning", play_music: bool = False, lang: str = "en") -> dict:
    """Execute the full morning/evening executive briefing routine.
    
    Note: Per user configuration, morning briefing never plays YouTube music.
    """
    mode = "morning" if mode != "evening" else "evening"
    cfg = _load_briefing_config()

    # Determine music playback: morning briefing is always silent
    if mode == "morning":
        should_play_music = False
    else:
        should_play_music = bool(play_music or cfg.get("evening_play_music", False))

    # Announce status to event bus
    bus.set_state(bus.EXECUTING, f"COMPILING {mode.upper()} BRIEFING")
    bus.activity(f"Gathering {mode} briefing telemetry", "pending")

    # 1. Gather all data
    data = gather_briefing_data(mode=mode, lang=lang)

    # 2. Formulate spoken summary
    spoken = generate_spoken_briefing(data, mode=mode, lang=lang)

    # 3. Prepare HUD payload
    payload = hud_payload(data, spoken, audio_active=should_play_music)

    # 4. Publish to event bus
    bus.briefing(**payload)
    bus.activity(f"{mode.title()} briefing delivered", "ok")

    # 5. Play ambient soundtrack only if enabled (evening mode only)
    if should_play_music:
        query = cfg.get(f"{mode}_music") or DEFAULT_SOUNDTRACKS.get(mode)
        if query:
            try:
                tools.play_youtube(query)
            except Exception as e:
                print(f"[briefing] Could not start ambient audio: {e}")

    return {
        "ok": True,
        "mode": mode,
        "spoken": spoken,
        "payload": payload,
    }
