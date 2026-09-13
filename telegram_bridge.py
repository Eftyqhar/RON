"""telegram_bridge.py — RON Pocket Uplink (Two-Way Remote Command Center via Telegram)

Enables remote communication between your smartphone and RON desktop assistant:
1. Long-polling Telegram Bot API engine (zero extra heavy frameworks, pure requests).
2. Pairing & Whitelist Security Guard (rejects unauthorized users; PIN pairing).
3. Remote command execution:
   - /status: Real-time CPU, RAM, Battery, Uptime, Active Window.
   - /screenshot: Captures active desktop and uploads photo.
   - /research <topic>: Runs autonomous deep research and uploads PDF dossier.
   - /tribune: Compiles today's RON World Tribune and uploads broadsheet PDF.
   - /lock: Locks Windows workstation remotely.
   - /weather: Returns live weather telemetry.
   - /standup: Returns active daily goals.
   - Conversational AI & tools via main.py execution pipeline.
4. Broadcasts live telemetry to HUD event bus (bus.telegram).
"""

import ctypes
import datetime
import json
import os
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional

import requests

import bus

# Try importing optional system libraries
try:
    import psutil
except ImportError:
    psutil = None

try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None

try:
    import win32gui
except ImportError:
    win32gui = None


CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

_daemon_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_state_lock = threading.Lock()

_bot_info: Dict[str, Any] = {
    "enabled": False,
    "connected": False,
    "bot_id": None,
    "username": None,
    "first_name": None,
    "last_command": None,
    "last_activity": None,
    "authorized_users": [],
}


# ---------------------------------------------------------------------------
# Configuration & Persistence
# ---------------------------------------------------------------------------

def load_telegram_config() -> Dict[str, Any]:
    """Load Telegram settings from config.json or environment variables."""
    cfg: Dict[str, Any] = {
        "enabled": True,
        "bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
        "allowed_chat_ids": [],
        "pair_pin": "RON-7701",
        "send_voice_replies": False,
        "morning_tribune_push": True,
    }

    env_chat = os.environ.get("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
    if env_chat:
        cfg["allowed_chat_ids"] = [str(env_chat)]

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                tg_data = data.get("telegram", {})
                if isinstance(tg_data, dict):
                    if tg_data.get("bot_token") and not cfg["bot_token"]:
                        cfg["bot_token"] = str(tg_data["bot_token"]).strip()
                    if "enabled" in tg_data:
                        cfg["enabled"] = bool(tg_data["enabled"])
                    if tg_data.get("allowed_chat_ids"):
                        # Support list or comma-separated string
                        raw_chats = tg_data["allowed_chat_ids"]
                        if isinstance(raw_chats, list):
                            cfg["allowed_chat_ids"] = [str(c).strip() for c in raw_chats if str(c).strip()]
                        elif isinstance(raw_chats, str):
                            cfg["allowed_chat_ids"] = [c.strip() for c in raw_chats.split(",") if c.strip()]
                    if tg_data.get("pair_pin"):
                        cfg["pair_pin"] = str(tg_data["pair_pin"]).strip()
                    if "send_voice_replies" in tg_data:
                        cfg["send_voice_replies"] = bool(tg_data["send_voice_replies"])
                    if "morning_tribune_push" in tg_data:
                        cfg["morning_tribune_push"] = bool(tg_data["morning_tribune_push"])
        except Exception as e:
            print(f"[telegram_bridge] Failed to load config.json: {e}")

    return cfg


def save_allowed_chat_id(chat_id: str | int) -> bool:
    """Save an authorized chat_id into config.json permanently."""
    chat_str = str(chat_id).strip()
    if not chat_str:
        return False

    with _state_lock:
        if chat_str not in _bot_info["authorized_users"]:
            _bot_info["authorized_users"].append(chat_str)

    try:
        data = {}
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        
        tg = data.get("telegram", {})
        allowed = tg.get("allowed_chat_ids", [])
        if not isinstance(allowed, list):
            allowed = []
        if chat_str not in [str(c) for c in allowed]:
            allowed.append(chat_str)
        tg["allowed_chat_ids"] = allowed
        data["telegram"] = tg

        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[telegram_bridge] Paired and saved authorized Telegram chat ID: {chat_str}")
        return True
    except Exception as e:
        print(f"[telegram_bridge] Failed to persist chat ID to config.json: {e}")
        return False


def is_authorized(chat_id: str | int | None, cfg: Dict[str, Any]) -> bool:
    """Check if a chat ID is in the whitelist."""
    if chat_id is None:
        return False
    chat_str = str(chat_id).strip()
    if not chat_str:
        return False
    allowed = [str(c).strip() for c in cfg.get("allowed_chat_ids", [])]
    return chat_str in allowed


def match_command(text: str) -> tuple[str, str]:
    """Extract canonical command and arguments from raw user text or voice note."""
    t = (text or "").strip()
    low = t.lower()

    if low in ("/status", "status", "status report", "system status", "health", "ron, status report"):
        return "status", ""
    if low in ("/screenshot", "screenshot", "send screenshot", "screen"):
        return "screenshot", ""
    if low in ("/lock", "lock", "lock pc", "lock workstation"):
        return "lock", ""
    if low in ("/tribune", "tribune", "send newspaper", "newspaper", "today's newspaper"):
        return "tribune", ""
    weather_m = re.match(r"^[/\\]?weather(?:\s+(?:in\s+)?(.+))?$", low)
    if weather_m or low in ("what's the weather", "current weather"):
        loc = weather_m.group(1).strip() if (weather_m and weather_m.group(1)) else ""
        return "weather", loc
    if low in ("/standup", "/goals", "standup", "goals", "what are my goals"):
        return "standup", ""
    if low in ("/netscan", "/radar", "netscan", "scan network", "scan wifi", "network radar"):
        return "netscan", ""

    live_m = re.match(r"^[/\\]?live(?:\s+(\d+))?$", low)
    if live_m or low in ("live audio", "listen live", "record live", "room audio", "record audio"):
        secs = live_m.group(1).strip() if (live_m and live_m.group(1)) else "5"
        return "live", secs

    share_m = re.match(r"^[/\\]?share(?:\s+(.*))?$", t, re.IGNORECASE)
    if share_m or low in ("share", "ron share", "ron-share", "shared files", "share files"):
        arg = (share_m.group(1) or "").strip() if share_m else ""
        return "share", arg

    res_m = re.match(r"^(?:/research|research)\s+(.+)$", t, re.IGNORECASE)
    if res_m:
        return "research", res_m.group(1).strip()

    return "chat", t



# ---------------------------------------------------------------------------
# Telegram Bot API Low-Level Client
# ---------------------------------------------------------------------------

class TelegramClient:
    """Lightweight, thread-safe Telegram Bot API client using requests."""

    def __init__(self, token: str):
        self.token = token.strip()
        self.base_url = f"https://api.telegram.org/bot{self.token}"

    def get_me(self) -> Dict[str, Any]:
        """Verify token and fetch bot metadata."""
        url = f"{self.base_url}/getMe"
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    return {"ok": True, "result": data.get("result", {})}
            return {"ok": False, "error": resp.text}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_updates(self, offset: Optional[int] = None, timeout: int = 25) -> List[Dict[str, Any]]:
        """Long poll updates from Telegram."""
        url = f"{self.base_url}/getUpdates"
        params: Dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        try:
            resp = requests.get(url, params=params, timeout=timeout + 5)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    return data.get("result", [])
            return []
        except requests.exceptions.Timeout:
            return []
        except Exception as e:
            time.sleep(1.0)
            return []

    def send_message(self, chat_id: str | int, text: str, parse_mode: str = "Markdown") -> bool:
        """Send a formatted text message to a chat."""
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        try:
            resp = requests.post(url, json=payload, timeout=12)
            if resp.status_code == 200:
                return True
            # Retry without parse_mode in case markdown syntax failed
            if resp.status_code == 400:
                payload.pop("parse_mode", None)
                resp2 = requests.post(url, json=payload, timeout=12)
                return resp2.status_code == 200
            return False
        except Exception as e:
            print(f"[telegram_bridge] send_message failed: {e}")
            return False

    def send_photo(self, chat_id: str | int, photo_path: str, caption: str = "") -> bool:
        """Upload and send a photo to chat."""
        url = f"{self.base_url}/sendPhoto"
        if not os.path.exists(photo_path):
            return False
        try:
            with open(photo_path, "rb") as f:
                files = {"photo": f}
                data = {"chat_id": chat_id, "caption": caption}
                resp = requests.post(url, data=data, files=files, timeout=25)
                return resp.status_code == 200
        except Exception as e:
            print(f"[telegram_bridge] send_photo failed: {e}")
            return False

    def send_document(self, chat_id: str | int, doc_path: str, caption: str = "") -> bool:
        """Upload and send a document (PDF, report, etc.) to chat."""
        url = f"{self.base_url}/sendDocument"
        if not os.path.exists(doc_path):
            return False
        try:
            with open(doc_path, "rb") as f:
                files = {"document": f}
                data = {"chat_id": chat_id, "caption": caption}
                resp = requests.post(url, data=data, files=files, timeout=45)
                return resp.status_code == 200
        except Exception as e:
            print(f"[telegram_bridge] send_document failed: {e}")
            return False

    def send_voice(self, chat_id: str | int, voice_path: str, caption: str = "", duration: Optional[int] = None) -> bool:
        """Upload and send an audio file as a native Telegram voice note."""
        url = f"{self.base_url}/sendVoice"
        if not os.path.exists(voice_path):
            return False
        try:
            with open(voice_path, "rb") as f:
                files = {"voice": (os.path.basename(voice_path), f, "audio/ogg")}
                data: Dict[str, Any] = {"chat_id": chat_id, "caption": caption}
                if duration:
                    data["duration"] = int(duration)
                resp = requests.post(url, data=data, files=files, timeout=45)
                if resp.status_code == 200:
                    return True
                # If sendVoice is not accepted, fallback to sendAudio
                return self.send_audio(chat_id, voice_path, caption=caption)
        except Exception as e:
            print(f"[telegram_bridge] send_voice failed: {e}")
            return self.send_audio(chat_id, voice_path, caption=caption)

    def send_audio(self, chat_id: str | int, audio_path: str, caption: str = "") -> bool:
        """Upload and send an audio file / voice reply to chat."""
        url = f"{self.base_url}/sendAudio"
        if not os.path.exists(audio_path):
            return False
        try:
            with open(audio_path, "rb") as f:
                files = {"audio": f}
                data = {"chat_id": chat_id, "caption": caption}
                resp = requests.post(url, data=data, files=files, timeout=30)
                return resp.status_code == 200
        except Exception as e:
            print(f"[telegram_bridge] send_audio failed: {e}")
            return False

    def download_file(self, file_id: str, dest_path: str) -> bool:
        """Download a file sent to the bot by file_id."""
        try:
            info_url = f"{self.base_url}/getFile"
            resp = requests.get(info_url, params={"file_id": file_id}, timeout=15)
            if resp.status_code != 200 or not resp.json().get("ok"):
                return False
            file_path = resp.json()["result"].get("file_path")
            if not file_path:
                return False
            dl_url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
            dl_resp = requests.get(dl_url, timeout=60)
            if dl_resp.status_code == 200:
                os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
                with open(dest_path, "wb") as f:
                    f.write(dl_resp.content)
                return True
            return False
        except Exception as e:
            print(f"[telegram_bridge] download_file failed: {e}")
            return False


# ---------------------------------------------------------------------------
# Remote Command Handlers
# ---------------------------------------------------------------------------

def handle_status_command() -> str:
    """Generate a rich, tactical system status report."""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "🛰️ *RON POCKET UPLINK — SYSTEM STATUS REPORT*",
        f"⏱️ *Timestamp:* `{now_str}`",
        "─────────────────────────────",
    ]

    # CPU & RAM Telemetry
    if psutil:
        cpu_pct = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        mem_used_gb = mem.used / (1024 ** 3)
        mem_total_gb = mem.total / (1024 ** 3)
        lines.append(f"⚡ *CPU Utilization:* `{cpu_pct}%`")
        lines.append(f"🧠 *RAM Usage:* `{mem_used_gb:.1f} GB / {mem_total_gb:.1f} GB ({mem.percent}%)`")

        # Battery
        battery = psutil.sensors_battery()
        if battery:
            plugged = "🔌 Plugged In" if battery.power_plugged else "🔋 On Battery"
            lines.append(f"🔋 *Battery:* `{battery.percent}%` ({plugged})")

        # Uptime
        boot_time = datetime.datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.datetime.now() - boot_time
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)
        lines.append(f"⏳ *System Uptime:* `{hours}h {minutes}m`")
    else:
        lines.append("⚡ *Hardware Telemetry:* `psutil not installed`")

    # Active Foreground Window
    active_win = "Unknown"
    if win32gui:
        try:
            hwnd = win32gui.GetForegroundWindow()
            active_win = win32gui.GetWindowText(hwnd) or "Desktop"
        except Exception:
            pass
    lines.append(f"🖥️ *Foreground Window:* `{active_win[:60]}`")

    # HUD Event Bus State
    snap = bus.snapshot()
    st = snap.get("state", {})
    mode_label = st.get("mode", "ONLINE").upper() if isinstance(st, dict) else "ONLINE"
    lines.append(f"🤖 *RON Status:* `{mode_label}`")

    lines.append("─────────────────────────────")
    lines.append("✅ *Workstation operational and responsive, Sir.*")
    return "\n".join(lines)


# Alias for diagnostic and reporting callers
build_status_report = handle_status_command



def handle_screenshot_command(client: TelegramClient, chat_id: str | int) -> bool:
    """Capture desktop screenshot and upload to Telegram chat."""
    snap_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "screenshots")
    os.makedirs(snap_dir, exist_ok=True)
    snap_path = os.path.join(snap_dir, "telegram_remote_screenshot.png")

    captured = False
    # Attempt 1: PIL ImageGrab
    if ImageGrab:
        try:
            img = ImageGrab.grab()
            img.save(snap_path, "PNG")
            captured = True
        except Exception:
            captured = False

    # Attempt 2: Win32 BitBlt
    if not captured and win32gui:
        try:
            import win32api
            import win32con
            import win32ui
            from PIL import Image

            hwin = win32gui.GetDesktopWindow()
            width = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
            height = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
            left = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
            top = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
            hwindc = win32gui.GetWindowDC(hwin)
            srcdc = win32ui.CreateDCFromHandle(hwindc)
            memdc = srcdc.CreateCompatibleDC()
            bmp = win32ui.CreateBitmap()
            bmp.CreateCompatibleBitmap(srcdc, width, height)
            memdc.SelectObject(bmp)
            memdc.BitBlt((0, 0), (width, height), srcdc, (left, top), win32con.SRCCOPY)
            bmpinfo = bmp.GetInfo()
            bmpstr = bmp.GetBitmapBits(True)
            im = Image.frombuffer("RGB", (bmpinfo["bmWidth"], bmpinfo["bmHeight"]), bmpstr, "raw", "BGRX", 0, 1)
            im.save(snap_path, "PNG")
            win32gui.DeleteObject(bmp.GetHandle())
            memdc.DeleteDC()
            srcdc.DeleteDC()
            win32gui.ReleaseDC(hwin, hwindc)
            captured = True
        except Exception:
            captured = False

    if captured and os.path.exists(snap_path):
        ts = datetime.datetime.now().strftime("%I:%M %p")
        return client.send_photo(chat_id, snap_path, caption=f"🖥️ Workstation Screen Capture · {ts}")
    else:
        client.send_message(
            chat_id,
            "⚠️ *Screen capture unavailable, Sir.*\n"
            "The desktop may currently be locked or operating in a background headless session.",
        )
        return False


def handle_lock_command() -> str:
    """Remotely lock the Windows desktop workstation."""
    try:
        ctypes.windll.user32.LockWorkStation()
        return "🔒 *Workstation locked successfully, Sir.*"
    except Exception as e:
        return f"⚠️ *Failed to lock workstation:* `{e}`"


def handle_weather_remote(location: str = "") -> str:
    """Return live weather conditions for remote Telegram user."""
    try:
        import weather
        loc = (location or "").strip() or None
        obs = weather.observe(location=loc)
        if obs and isinstance(obs, dict) and "temp" in obs:
            place = obs.get("place") or obs.get("location") or (loc or "Sirajganj")
            country = f", {obs.get('country')}" if obs.get("country") else ""
            temp = round(float(obs.get("temp", 0)))
            feels = round(float(obs.get("feels", temp)))
            condition = (obs.get("condition") or obs.get("phrase") or "Clear").title()
            humidity = obs.get("humidity", 0)
            wind = round(float(obs.get("wind", 0)))
            precip = obs.get("precip", 0)
            rain_pct = obs.get("rain_today_pct", 0)
            stale_warning = "\n⚠️ _Note: Displaying cached telemetry._" if obs.get("stale") else ""

            forecast_lines = []
            daily = obs.get("daily", [])
            if daily:
                forecast_lines.append("\n📅 *Upcoming Forecast:*")
                for d in daily[:3]:
                    day_name = d.get("day", "")
                    hi = d.get("hi", "")
                    lo = d.get("lo", "")
                    cond = (d.get("condition") or d.get("phrase") or "").title()
                    forecast_lines.append(f"• `{day_name}`: {lo}°C–{hi}°C · {cond}")

            forecast_text = "\n".join(forecast_lines)

            return (
                f"🌦️ *ATMOSPHERIC TELEMETRY: {place.upper()}{country}*\n"
                f"🌡️ *Temperature:* `{temp}°C` (Feels like `{feels}°C`)\n"
                f"🌤️ *Conditions:* `{condition}`\n"
                f"💧 *Humidity:* `{humidity}%` · 💨 *Wind:* `{wind} km/h`\n"
                f"☔ *Precipitation:* `{precip} mm` · *Rain Chance:* `{rain_pct}%`"
                f"{forecast_text}"
                f"{stale_warning}"
            )
    except Exception as e:
        print(f"[Telegram weather error: {e}]")
    return "🌦️ Weather telemetry is currently offline or unreachable."


def handle_standup_remote() -> str:
    """Return current goals from Daily Standup Coach."""
    try:
        import coach
        status = coach.get_standup_status()
        active = status.get("active_goals", [])
        if not active:
            return "🎯 *No active goals currently registered for today, Sir.*\nUse `/help` or voice to set goals."
        
        lines = [f"🎯 *DAILY STANDUP GOALS ({len(active)} Registered):*"]
        for g in active:
            mark = "✅" if g.get("completed") else "⬜"
            lines.append(f"{mark} *Goal {g.get('id')}:* {g.get('text')}")
        return "\n".join(lines)
    except Exception as e:
        return f"🎯 Standup data error: {e}"


def handle_research_remote(client: TelegramClient, chat_id: str | int, topic: str):
    """Execute autonomous deep research in background and upload PDF to Telegram."""
    client.send_message(
        chat_id,
        f"🔬 *Autonomous Deep Research Activated*\n"
        f"📌 *Topic:* `{topic}`\n"
        f"🔍 Scouting ArXiv, Wikipedia, and global search engines... Please stand by.",
    )
    bus.activity(f"Telegram Remote Research: {topic}", "pending")

    def _worker():
        try:
            import researcher
            dossier = researcher.conduct_deep_research(topic, depth="quick")
            pdf_path = dossier.get("pdf_path")
            md_path = dossier.get("md_path")

            client.send_message(
                chat_id,
                f"✅ *Research Complete: {topic}*\n\n"
                f"📊 *Summary:* {dossier.get('executive_summary', '')[:400]}...\n\n"
                f"📄 Uploading full publication dossier below:",
            )

            if pdf_path and os.path.exists(pdf_path):
                client.send_document(chat_id, pdf_path, caption=f"📄 Research Dossier: {topic}")
            elif md_path and os.path.exists(md_path):
                client.send_document(chat_id, md_path, caption=f"📝 Research Notes: {topic}")

            bus.activity(f"Telegram Remote Research delivered: {topic}", "ok")
        except Exception as e:
            client.send_message(chat_id, f"⚠️ *Research task failed:* `{e}`")
            bus.activity(f"Telegram Remote Research failed: {e}", "fail")

    threading.Thread(target=_worker, daemon=True).start()


def handle_tribune_remote(client: TelegramClient, chat_id: str | int):
    """Compile today's newspaper edition and upload PDF to Telegram."""
    client.send_message(
        chat_id,
        "📰 *Accessing The RON World Tribune Editorial Bureau...*\n"
        "Harvesting 50+ global sources and compiling publication broadsheet. Stand by.",
    )
    bus.activity("Telegram Remote: Publishing The RON World Tribune", "pending")

    def _worker():
        try:
            import tribune
            edition = tribune.build_today_tribune(force_refresh=False, generate_pdf_doc=True)
            pdf_path = edition.get("pdf_path")
            script = edition.get("broadcast_script_en", "")

            # Send 4-quadrant executive summary
            client.send_message(
                chat_id,
                f"📰 *The RON World Tribune — Edition {edition.get('edition_id')}*\n"
                f"_{edition.get('date_formatted')}_\n\n"
                f"★ *3-MINUTE EXECUTIVE SUMMARY* ★\n"
                f"• *Geopolitics:* {edition.get('sections', {}).get('world', [{}])[0].get('title', 'Global stability nominal')}\n"
                f"• *Tech & AI:* {edition.get('sections', {}).get('tech', [{}])[0].get('title', 'Advancements reported')}\n"
                f"• *Local Bureau:* {edition.get('weather_short', 'Sirajganj Bureau')}\n\n"
                f"📄 Uploading broadsheet PDF document below:",
            )

            if pdf_path and os.path.exists(pdf_path):
                client.send_document(
                    chat_id,
                    pdf_path,
                    caption=f"📰 The RON World Tribune ({edition.get('edition_id')})",
                )
            bus.activity("Telegram Remote: Tribune PDF delivered", "ok")
        except Exception as e:
            client.send_message(chat_id, f"⚠️ *Failed to compile Tribune:* `{e}`")
            bus.activity(f"Telegram Remote Tribune failed: {e}", "fail")

    threading.Thread(target=_worker, daemon=True).start()


def handle_docintel_upload_remote(client: TelegramClient, chat_id: str | int, file_id: str, file_name: str):
    """Process incoming document upload from Telegram, parse, and return 5-point executive digest."""
    client.send_message(
        chat_id,
        f"📄 *Ingesting Document:* `{file_name}`\n"
        f"⏳ Processing text, parsing structural data, and running autonomous executive synthesis... Stand by.",
    )
    bus.activity(f"Telegram Doc Ingestion: {file_name}", "pending")

    def _worker():
        try:
            import docintel
            upload_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "telegram_docs")
            os.makedirs(upload_dir, exist_ok=True)
            local_path = os.path.join(upload_dir, f"{int(time.time())}_{file_name}")

            dl_ok = client.download_file(file_id, local_path)
            if not dl_ok or not os.path.exists(local_path):
                client.send_message(chat_id, f"⚠️ *Failed to download document:* `{file_name}` from Telegram servers.")
                return

            # Ingest and synthesize
            doc_rec = docintel.parse_document(file_path=local_path, filename=file_name)
            digest = docintel.generate_executive_digest(doc_rec["id"])

            takeaways = digest.get("takeaways", [])
            takeaway_lines = "\n".join([f"• {t}" for t in takeaways[:5]]) or "• Analysis complete."
            crit_data = digest.get("critical_data", "None detected")
            premise = digest.get("premise", "")
            spoken = digest.get("spoken_debrief", "")

            msg_text = (
                f"📑 *DOCUMENT INTELLIGENCE REPORT*\n"
                f"📄 *File:* `{file_name}` ({doc_rec.get('pages', 1)} pages, {doc_rec.get('table_count', 0)} tables)\n\n"
                f"🎯 *Core Premise:*\n_{premise}_\n\n"
                f"★ *TOP 5 ACTIONABLE TAKEAWAYS:*\n"
                f"{takeaway_lines}\n\n"
                f"📊 *Critical Metrics & Obligations:*\n`{crit_data}`\n\n"
                f"💬 *Ask questions anytime using:* `/doc <question>`\n"
                f"📊 *Export tables using:* `/doctables`"
            )
            client.send_message(chat_id, msg_text)
            bus.activity(f"Telegram Doc Ingested: {file_name}", "ok")
        except Exception as e:
            client.send_message(chat_id, f"⚠️ *Document analysis failed:* `{e}`")
            bus.activity(f"Telegram Doc Ingestion failed: {e}", "fail")

    threading.Thread(target=_worker, daemon=True).start()


def handle_docintel_query_remote(client: TelegramClient, chat_id: str | int, query: str):
    """Execute Q&A or math calculations against active document via Telegram."""
    client.send_message(chat_id, f"🔍 *Querying Document Intelligence:* `{query}`...")
    
    def _worker():
        try:
            import docintel
            active = docintel.get_active_document()
            if not active:
                client.send_message(chat_id, "⚠️ *No document currently loaded.* Please upload a PDF or .docx first!")
                return

            ans_res = docintel.ask_document(query, active.get("id"))
            citations = ans_res.get("citations", [])
            cite_str = " | ".join(citations) if citations else "Full text analysis"
            ans_text = ans_res.get("answer", "No answer found.")

            rep = (
                f"📑 *DOCUMENT INTELLIGENCE ANSWER*\n"
                f"📄 *Source:* `{active.get('filename')}` (Ref: {cite_str})\n\n"
                f"{ans_text}"
            )
            client.send_message(chat_id, rep)
        except Exception as e:
            client.send_message(chat_id, f"⚠️ *Document query failed:* `{e}`")

    threading.Thread(target=_worker, daemon=True).start()


def handle_doctables_remote(client: TelegramClient, chat_id: str | int):
    """Export extracted tables to CSV and send via Telegram."""
    try:
        import docintel
        active = docintel.get_active_document()
        if not active:
            client.send_message(chat_id, "⚠️ *No document currently loaded.* Please upload a PDF first!")
            return

        exported = docintel.export_tables_to_csv(active.get("id"))
        if not exported:
            client.send_message(chat_id, f"⚠️ No structured tables detected in `{active.get('filename')}`.")
            return

        client.send_message(chat_id, f"📊 Found {len(exported)} table(s). Uploading CSV spreadsheets below:")
        for tbl in exported:
            csv_path = tbl.get("path")
            if csv_path and os.path.exists(csv_path):
                client.send_document(chat_id, csv_path, caption=f"📊 {tbl.get('filename')} (Page {tbl.get('page')})")
    except Exception as e:
        client.send_message(chat_id, f"⚠️ *Failed to export tables:* `{e}`")


def handle_live_remote(client: TelegramClient, chat_id: str | int, duration_sec: int = 5):
    """Record real-time ambient room audio from workstation microphone and transmit as a voice note."""
    try:
        duration = max(2, min(int(duration_sec or 5), 30))
    except (ValueError, TypeError):
        duration = 5

    client.send_message(
        chat_id,
        f"🎙️ *Audio Surveillance Active*\n"
        f"Recording `{duration}` seconds of live ambient audio from workstation microphone... Stand by.",
    )
    bus.activity(f"Telegram Live Audio: {duration}s", "pending")

    def _worker():
        audio_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "telegram_audio")
        os.makedirs(audio_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        wav_path = os.path.join(audio_dir, f"live_{timestamp}.wav")

        try:
            import wave
            import pyaudio

            mic_idx = None
            try:
                import voice
                mic_idx = getattr(voice, "MIC_INDEX", None)
            except Exception:
                pass

            p = pyaudio.PyAudio()
            if mic_idx is not None:
                try:
                    dev_info = p.get_device_info_by_index(mic_idx)
                    if dev_info.get("maxInputChannels", 0) <= 0:
                        mic_idx = None
                except Exception:
                    mic_idx = None

            rate = 16000
            chunk = 1024
            open_kwargs = {
                "format": pyaudio.paInt16,
                "channels": 1,
                "rate": rate,
                "input": True,
                "frames_per_buffer": chunk,
            }
            if mic_idx is not None:
                open_kwargs["input_device_index"] = mic_idx

            stream = p.open(**open_kwargs)
            frames = []
            total_chunks = int(rate / chunk * duration)
            for _ in range(total_chunks):
                data = stream.read(chunk, exception_on_overflow=False)
                frames.append(data)

            stream.stop_stream()
            stream.close()
            p.terminate()

            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(rate)
                wf.writeframes(b"".join(frames))

            ts_disp = datetime.datetime.now().strftime("%I:%M:%S %p")
            caption = f"🎙️ Live Audio Feed · {duration}s · {ts_disp}"
            sent = client.send_voice(chat_id, wav_path, caption=caption, duration=duration)
            if not sent:
                sent = client.send_audio(chat_id, wav_path, caption=caption)

            if sent:
                bus.activity(f"Telegram Live Audio sent ({duration}s)", "ok")
            else:
                client.send_message(chat_id, "⚠️ *Failed to transmit voice recording to Telegram.*")
                bus.activity("Telegram Live Audio delivery failed", "fail")
        except Exception as e:
            print(f"[telegram_bridge] Live audio recording error: {e}")
            client.send_message(chat_id, f"⚠️ *Live audio recording failed:* `{e}`")
            bus.activity(f"Telegram Live Audio error: {e}", "fail")
        finally:
            # Keep only the last 5 audio captures to avoid disk bloat
            try:
                files = sorted(
                    [os.path.join(audio_dir, f) for f in os.listdir(audio_dir) if f.startswith("live_")],
                    key=os.path.getmtime,
                )
                for old in files[:-5]:
                    try:
                        os.remove(old)
                    except Exception:
                        pass
            except Exception:
                pass

    threading.Thread(target=_worker, daemon=True).start()



# ---------------------------------------------------------------------------
# RON-Share Remote Storage & Access (/share, \share)
# ---------------------------------------------------------------------------

def get_ron_share_dir() -> str:
    """Return the absolute path to Documents/RON-Share folder, ensuring it exists."""
    try:
        import tools
        return tools.get_ron_share_dir()
    except Exception:
        home = os.path.expanduser("~")
        share_dir = os.path.join(home, "Documents", "RON-Share")
        os.makedirs(share_dir, exist_ok=True)
        return share_dir


def sanitize_filename(filename: str) -> str:
    """Clean filename, remove illegal Windows characters and prevent directory traversal."""
    if not filename:
        return f"file_{int(time.time())}.bin"
    name = os.path.basename(filename.strip())
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = name.strip(". ")
    return name or f"file_{int(time.time())}.bin"


def get_unique_filepath(directory: str, filename: str) -> str:
    """Return an available file path in directory. If filename exists, append (1), (2), etc."""
    base, ext = os.path.splitext(filename)
    dest = os.path.join(directory, filename)
    counter = 1
    while os.path.exists(dest):
        dest = os.path.join(directory, f"{base} ({counter}){ext}")
        counter += 1
    return dest


def format_bytes(size: int) -> str:
    """Format bytes to human-readable string (KB, MB, GB)."""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    else:
        return f"{size / (1024 * 1024 * 1024):.2f} GB"


def get_file_icon(filename: str) -> str:
    """Return an emoji icon based on file extension."""
    ext = os.path.splitext(filename)[1].lower()
    if ext in (".pdf",):
        return "📄"
    elif ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"):
        return "🖼️"
    elif ext in (".mp4", ".mkv", ".avi", ".mov", ".webm"):
        return "🎬"
    elif ext in (".mp3", ".wav", ".ogg", ".flac", ".m4a"):
        return "🎵"
    elif ext in (".zip", ".rar", ".7z", ".tar", ".gz"):
        return "📦"
    elif ext in (".py", ".js", ".html", ".css", ".json", ".cpp", ".java", ".c", ".ts"):
        return "💻"
    elif ext in (".xlsx", ".xls", ".csv"):
        return "📊"
    elif ext in (".docx", ".doc", ".txt", ".md", ".rtf"):
        return "📝"
    elif ext in (".exe", ".msi", ".bat", ".cmd", ".ps1"):
        return "⚙️"
    return "📁"


def handle_share_upload_remote(
    client: TelegramClient,
    chat_id: str | int,
    file_id: str,
    original_name: str,
    file_type: str = "document",
):
    """Download a file sent via Telegram directly into Documents/RON-Share."""
    share_dir = get_ron_share_dir()
    clean_name = sanitize_filename(original_name)

    client.send_message(
        chat_id,
        f"📥 *Receiving File for RON-Share:*\n`{clean_name}`\n"
        f"⏳ Storing directly to PC `Documents\\RON-Share`... Stand by.",
    )
    bus.activity(f"RON-Share: Receiving {clean_name}", "pending")

    def _worker():
        try:
            target_path = get_unique_filepath(share_dir, clean_name)
            saved_filename = os.path.basename(target_path)

            ok = client.download_file(file_id, target_path)
            if not ok or not os.path.exists(target_path):
                client.send_message(chat_id, f"⚠️ *Failed to store file:* `{clean_name}` from Telegram.")
                bus.activity(f"RON-Share: Download failed for {clean_name}", "fail")
                return

            size_bytes = os.path.getsize(target_path)
            size_str = format_bytes(size_bytes)
            icon = get_file_icon(saved_filename)
            ts = datetime.datetime.now().strftime("%I:%M %p")

            msg = (
                f"✅ *FILE SAVED TO RON-SHARE*\n"
                f"─────────────────────────────\n"
                f"{icon} *Name:* `{saved_filename}`\n"
                f"⚖️ *Size:* `{size_str}`\n"
                f"📂 *Location:* `Documents\\RON-Share`\n"
                f"⏱️ *Time:* `{ts}`\n"
                f"─────────────────────────────\n"
                f"• Access/Download: `/share get {saved_filename}`\n"
                f"• View all files: `/share list`\n"
                f"• Open on PC: `/share open`"
            )
            client.send_message(chat_id, msg)
            bus.activity(f"File stored in RON-Share: {saved_filename} ({size_str})", "ok")
        except Exception as e:
            print(f"[telegram_bridge] RON-Share upload error: {e}")
            client.send_message(chat_id, f"⚠️ *Error saving file to RON-Share:* `{e}`")
            bus.activity(f"RON-Share save error: {e}", "fail")

    threading.Thread(target=_worker, daemon=True).start()


def handle_share_command(client: TelegramClient, chat_id: str | int, args: str):
    r"""Process /share and \share commands: list, get, open, delete."""
    arg_str = (args or "").strip()
    share_dir = get_ron_share_dir()

    # Subcommand: /share open
    if arg_str.lower() in ("open", "open folder", "explore"):
        try:
            import subprocess
            subprocess.Popen(["explorer.exe", share_dir], shell=False)
            client.send_message(
                chat_id,
                f"📂 *Opened RON-Share folder on PC desktop, Sir.*\n`{share_dir}`",
            )
            bus.activity("RON-Share opened in Explorer", "ok")
        except Exception as e:
            client.send_message(chat_id, f"⚠️ *Could not open folder on PC:* `{e}`")
        return

    # Helper to get sorted files
    def _get_files():
        if not os.path.exists(share_dir):
            return []
        entries = []
        for f in os.listdir(share_dir):
            p = os.path.join(share_dir, f)
            if os.path.isfile(p):
                entries.append((f, os.path.getmtime(p), os.path.getsize(p)))
        # Sort newest first
        entries.sort(key=lambda x: x[1], reverse=True)
        return entries

    files = _get_files()

    # Subcommand: /share delete <target>
    del_m = re.match(r"^(?:delete|del|remove|rm)\s+(.+)$", arg_str, re.IGNORECASE)
    if del_m:
        del_target = del_m.group(1).strip()
        selected_file = None
        if del_target.isdigit():
            idx = int(del_target) - 1
            if 0 <= idx < len(files):
                selected_file = files[idx][0]
        else:
            for f_name, _, _ in files:
                if f_name.lower() == del_target.lower().strip("\"'"):
                    selected_file = f_name
                    break
        if selected_file:
            try:
                os.remove(os.path.join(share_dir, selected_file))
                client.send_message(chat_id, f"🗑️ *Deleted from RON-Share:* `{selected_file}`")
                bus.activity(f"Deleted from RON-Share: {selected_file}", "ok")
            except Exception as e:
                client.send_message(chat_id, f"⚠️ *Failed to delete:* `{e}`")
        else:
            client.send_message(chat_id, f"⚠️ *File not found to delete:* `{del_target}`")
        return

    # Subcommand: /share get <index or name> or /share download <index or name>
    get_m = re.match(r"^(?:get|download|send)\s+(.+)$", arg_str, re.IGNORECASE)
    target = None
    if get_m:
        target = get_m.group(1).strip()
    elif arg_str and arg_str.lower() not in ("list", "status", "files", "all", "help"):
        target = arg_str

    if target:
        # Check if target is a number (1-based index)
        selected_file = None
        if target.isdigit():
            idx = int(target) - 1
            if 0 <= idx < len(files):
                selected_file = files[idx][0]
            else:
                client.send_message(chat_id, f"⚠️ *Invalid file index:* `{target}`. Total files: {len(files)}. Type `/share list`.")
                return
        else:
            target_clean = target.lower().strip("\"'")
            for f_name, _, _ in files:
                if f_name.lower() == target_clean:
                    selected_file = f_name
                    break
            if not selected_file:
                for f_name, _, _ in files:
                    if target_clean in f_name.lower():
                        selected_file = f_name
                        break

        if not selected_file:
            client.send_message(
                chat_id,
                f"⚠️ *File not found:* `{target}`.\n"
                f"Use `/share list` to view all available files in `RON-Share`.",
            )
            return

        file_path = os.path.join(share_dir, selected_file)
        if not os.path.exists(file_path):
            client.send_message(chat_id, f"⚠️ File `{selected_file}` does not exist on disk.")
            return

        file_size = os.path.getsize(file_path)
        icon = get_file_icon(selected_file)
        client.send_message(
            chat_id,
            f"📤 *Uploading from PC:* {icon} `{selected_file}` ({format_bytes(file_size)})... Stand by.",
        )
        bus.activity(f"RON-Share uploading: {selected_file}", "pending")

        def _upload_worker():
            try:
                ext = os.path.splitext(selected_file)[1].lower()
                sent = False
                caption = f"{icon} {selected_file} ({format_bytes(file_size)})"

                # If image and under 10MB, send as photo
                if ext in (".jpg", ".jpeg", ".png", ".webp") and file_size < 10 * 1024 * 1024:
                    sent = client.send_photo(chat_id, file_path, caption=caption)
                elif ext in (".mp3", ".wav", ".ogg", ".flac", ".m4a") and file_size < 50 * 1024 * 1024:
                    sent = client.send_audio(chat_id, file_path, caption=caption)

                # Default / fallback to send_document
                if not sent:
                    sent = client.send_document(chat_id, file_path, caption=caption)

                if sent:
                    bus.activity(f"RON-Share uploaded: {selected_file}", "ok")
                else:
                    client.send_message(chat_id, f"⚠️ *Failed to transmit:* `{selected_file}` to Telegram.")
                    bus.activity(f"RON-Share upload failed: {selected_file}", "fail")
            except Exception as e:
                print(f"[telegram_bridge] Upload worker error: {e}")
                client.send_message(chat_id, f"⚠️ *Failed to upload file:* `{e}`")
                bus.activity(f"RON-Share upload error: {e}", "fail")

        threading.Thread(target=_upload_worker, daemon=True).start()
        return

    # Default: List all files (/share, \share, /share list)
    if not files:
        client.send_message(
            chat_id,
            f"📂 *RON-SHARE REPOSITORY*\n"
            f"📍 *Location:* `Documents\\RON-Share`\n\n"
            f"ℹ️ *Folder is currently empty.*\n\n"
            f"💡 *How to add files:*\n"
            f"• Send any file (document, photo, audio, video, zip) with caption `/share`\n"
            f"• Any file you send will be stored here on your PC!\n\n"
            f"• To open on PC desktop: `/share open`",
        )
        return

    total_bytes = sum(f[2] for f in files)
    total_str = format_bytes(total_bytes)

    lines = [
        f"📂 *RON-SHARE REPOSITORY*",
        f"📍 *Location:* `Documents\\RON-Share`",
        f"📊 *Files:* `{len(files)}` · *Total Size:* `{total_str}`",
        f"─────────────────────────────",
    ]

    disp_limit = 20
    for i, (fname, mtime, fsize) in enumerate(files[:disp_limit], 1):
        dt_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        icon = get_file_icon(fname)
        sz_str = format_bytes(fsize)
        lines.append(f"`{i:2d}.` {icon} `{fname}`\n    └─ `{sz_str}` · `{dt_str}`")

    if len(files) > disp_limit:
        lines.append(f"_...and {len(files) - disp_limit} more file(s)_")

    lines.append(f"─────────────────────────────")
    lines.append(f"📥 *Download to phone:* `/share get <number or name>`")
    lines.append(f"🖥️ *Open on PC desktop:* `/share open`")
    lines.append(f"📤 *Add files:* Send any file with `/share` in caption")

    client.send_message(chat_id, "\n".join(lines))


# ---------------------------------------------------------------------------
# General Intent & Message Dispatcher
# ---------------------------------------------------------------------------

def dispatch_telegram_message(client: TelegramClient, message: Dict[str, Any], cfg: Dict[str, Any]):
    """Process an incoming message from Telegram."""
    chat = message.get("chat", {})
    chat_id = str(chat.get("id", "")).strip()
    raw_text = str(message.get("text", "")).strip()
    raw_caption = str(message.get("caption", "")).strip()
    text = raw_text or raw_caption
    sender_name = message.get("from", {}).get("first_name", "Commander")

    if not chat_id:
        return

    # Update state
    with _state_lock:
        _bot_info["last_activity"] = time.time()
        _bot_info["last_command"] = text[:40]

    # Security check: Check if sender is authorized
    allowed = [str(c).strip() for c in cfg.get("allowed_chat_ids", [])]
    
    # 1. Pairing command (/pair <PIN>)
    pair_match = re.match(r"^/pair(?:\s+([A-Za-z0-9\-]+))?$", text, re.IGNORECASE)
    if pair_match:
        input_pin = (pair_match.group(1) or "").strip()
        expected_pin = cfg.get("pair_pin", "RON-7701").strip()
        if input_pin == expected_pin:
            save_allowed_chat_id(chat_id)
            if chat_id not in cfg["allowed_chat_ids"]:
                cfg["allowed_chat_ids"].append(chat_id)
            client.send_message(
                chat_id,
                f"✅ *Uplink Established (Pairing Successful)!*\n\n"
                f"Welcome, {sender_name}. Your Telegram ID (`{chat_id}`) is now securely paired with your RON terminal.\n\n"
                f"Type `/help` to view all remote command capabilities.",
            )
            bus.activity(f"Telegram Uplink paired with {sender_name} (ID: {chat_id})", "ok")
            bus.telegram(connected=True, authorized_count=len(cfg["allowed_chat_ids"]))
            return
        else:
            client.send_message(chat_id, "⛔ *Invalid Pairing PIN (Incorrect pairing PIN).* Access denied.")
            bus.activity(f"Failed Telegram pairing attempt from ID: {chat_id}", "fail")
            return

    # 2. Authorization enforcement
    if chat_id not in allowed:
        client.send_message(
            chat_id,
            "⛔ *Access Denied*\n"
            "This RON desktop terminal is paired exclusively to an authorized commander.\n\n"
            "If this is your terminal, pair using:\n`/pair <PIN>`",
        )
        bus.activity(f"Unauthorized Telegram access rejected from ID: {chat_id}", "fail")
        return

    # Check for /share trigger in text or caption
    has_share_trigger = bool(
        re.search(r"^[/\\]?share\b", raw_caption, re.IGNORECASE) or
        re.search(r"^[/\\]?share\b", raw_text, re.IGNORECASE) or
        raw_caption.lower().startswith("/share") or
        raw_caption.lower().startswith(r"\share") or
        raw_text.lower().startswith("/share") or
        raw_text.lower().startswith(r"\share") or
        "share" in raw_caption.lower()
    )

    # Authorized user attachments:
    # 1. Document attachment ingestion (PDF, DOCX, CSV, TXT, ZIP, etc.)
    doc_attachment = message.get("document")
    if doc_attachment:
        file_id = doc_attachment.get("file_id")
        file_name = doc_attachment.get("file_name", f"document_{int(time.time())}.bin")
        if file_id:
            ext = os.path.splitext(file_name)[1].lower()
            if has_share_trigger or ext not in (".pdf", ".docx", ".doc", ".csv"):
                handle_share_upload_remote(client, chat_id, file_id, file_name, file_type="document")
            else:
                handle_docintel_upload_remote(client, chat_id, file_id, file_name)
            return

    # 2. Photo attachment ingestion
    photo_attachment = message.get("photo")
    if photo_attachment and isinstance(photo_attachment, list) and len(photo_attachment) > 0:
        best_photo = photo_attachment[-1]
        file_id = best_photo.get("file_id")
        if file_id:
            ts_name = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"photo_{ts_name}.jpg"
            handle_share_upload_remote(client, chat_id, file_id, file_name, file_type="photo")
            return

    # 3. Video attachment ingestion
    video_attachment = message.get("video")
    if video_attachment:
        file_id = video_attachment.get("file_id")
        ts_name = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = video_attachment.get("file_name") or f"video_{ts_name}.mp4"
        if file_id:
            handle_share_upload_remote(client, chat_id, file_id, file_name, file_type="video")
            return

    # 4. Audio attachment ingestion
    audio_attachment = message.get("audio")
    if audio_attachment:
        file_id = audio_attachment.get("file_id")
        ts_name = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = audio_attachment.get("file_name") or f"audio_{ts_name}.mp3"
        if file_id:
            handle_share_upload_remote(client, chat_id, file_id, file_name, file_type="audio")
            return

    # 5. Voice note attachment with /share caption
    voice_attachment = message.get("voice")
    if voice_attachment and has_share_trigger:
        file_id = voice_attachment.get("file_id")
        ts_name = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"voice_{ts_name}.ogg"
        if file_id:
            handle_share_upload_remote(client, chat_id, file_id, file_name, file_type="voice")
            return

    bus.activity(f"Telegram Command: '{text[:30]}'", "pending")
    low = text.lower()

    # Help / Start
    if low in ("/start", "/help", "help"):
        client.send_message(
            chat_id,
            f"🤖 *RON POCKET UPLINK — COMMAND DIRECTORY*\n"
            f"Welcome back, {sender_name}. You have full remote authority over your desktop.\n\n"
            f"• 📂 `/share` or `\\share` — Access all files in PC's `Documents\\RON-Share`\n"
            f"• 📥 `/share get <number or filename>` — Download any file from PC to phone\n"
            f"• 🖥️ `/share open` — Open RON-Share folder on PC desktop\n"
            f"• 📎 *Send any file with `/share`* — Stores directly in PC's RON-Share folder\n"
            f"• 📊 `/status` or *\"status report\"* — Telemetry, CPU, RAM, Battery\n"
            f"• 📸 `/screenshot` or *\"send screenshot\"* — Desktop screenshot\n"
            f"• 🎙️ `/live [seconds]` or `\\live` — Listen to live ambient room audio (2–30s)\n"
            f"• 🔬 `/research <topic>` — Autonomous web research & PDF delivery\n"
            f"• 📰 `/tribune` or *\"send newspaper\"* — The RON World Tribune PDF\n"
            f"• 🔒 `/lock` — Remotely lock Windows workstation\n"
            f"• 🛡️ `/netscan` or *\"scan network\"* — Cyber Watchdog Wi-Fi radar & open ports\n"
            f"• 📑 `/doc <query>` — Ask questions, extract clauses, calculate from active document\n"
            f"• 📊 `/doctables` — Extract all tables from document into Excel CSV\n"
            f"• 📎 *Upload any PDF / Docx / CSV* — Autonomous 5-point executive digest & voice summary\n"
            f"• 🌦️ `/weather` — Real-time atmospheric conditions\n"
            f"• 🎯 `/standup` or `/goals` — Daily Standup active goals\n"
            f"• 💬 Send any conversational request, calculation, or question directly!\n",
        )
        return

    # RON-Share commands (/share, \share, /share list, /share get ..., /share open)
    share_m = re.match(r"^[/\\]?share(?:\s+(.*))?$", text, re.IGNORECASE)
    if share_m or low in ("share", "ron share", "ron-share", "shared files", "share files"):
        share_arg = (share_m.group(1) or "").strip() if share_m else ""
        handle_share_command(client, chat_id, share_arg)
        return

    # Status
    if low in ("/status", "status", "status report", "system status", "health"):
        rep = handle_status_command()
        client.send_message(chat_id, rep)
        return

    # Screenshot
    if low in ("/screenshot", "screenshot", "send screenshot", "screen"):
        handle_screenshot_command(client, chat_id)
        return

    # Live Ambient Microphone Audio (/live, \live, /live 10, etc.)
    live_m = re.match(r"^[/\\]?live(?:\s+(\d+))?$", low)
    if live_m or low in ("live audio", "listen live", "record live", "room audio", "record audio"):
        secs = int(live_m.group(1)) if (live_m and live_m.group(1)) else 5
        handle_live_remote(client, chat_id, secs)
        return

    # Lock
    if low in ("/lock", "lock", "lock pc", "lock workstation"):
        rep = handle_lock_command()
        client.send_message(chat_id, rep)
        return

    # Tribune Newspaper
    if low in ("/tribune", "tribune", "send newspaper", "newspaper", "today's newspaper"):
        handle_tribune_remote(client, chat_id)
        return

    # Deep Research
    res_m = re.match(r"^(?:/research|research)\s+(.+)$", text, re.IGNORECASE)
    if res_m:
        topic = res_m.group(1).strip()
        handle_research_remote(client, chat_id, topic)
        return

    # Weather (/weather, \weather, weather in Dhaka, etc.)
    weather_m = re.match(r"^[/\\]?weather(?:\s+(?:in\s+)?(.+))?$", low)
    if weather_m or low in ("what's the weather", "current weather"):
        loc = weather_m.group(1).strip() if (weather_m and weather_m.group(1)) else ""
        rep = handle_weather_remote(loc)
        client.send_message(chat_id, rep)
        return

    # Standup / Goals
    if low in ("/standup", "/goals", "standup", "goals", "what are my goals"):
        rep = handle_standup_remote()
        client.send_message(chat_id, rep)
        return

    # Cyber Watchdog Network Radar
    if low in ("/netscan", "/radar", "netscan", "scan network", "scan wifi", "network radar"):
        client.send_message(chat_id, "🛡️ *Cyber Watchdog Perimeter Sweep Initiated...*\nProbing subnet devices and workstation port vulnerabilities. Stand by.")
        try:
            import netradar
            scan_res = netradar.scan_network(fast=True)
            rep = netradar.format_radar_telegram_report(scan_res)
            client.send_message(chat_id, rep)
        except Exception as e:
            client.send_message(chat_id, f"⚠️ *Perimeter scan failed:* `{e}`")
        return

    # Document Intelligence Q&A & Calculations
    doc_m = re.match(r"^(?:/doc|doc)\s+(.+)$", text, re.IGNORECASE)
    if doc_m:
        q = doc_m.group(1).strip()
        handle_docintel_query_remote(client, chat_id, q)
        return

    # Document Intelligence Tables Export
    if low in ("/doctables", "doctables", "export tables", "extract tables"):
        handle_doctables_remote(client, chat_id)
        return

    # Conversational & LLM fallback
    # Execute through main.py LLM / tool pipeline
    try:
        import main
        bus.set_state(bus.EXECUTING, f"TELEGRAM · {text[:30].upper()}")
        
        # Check if matched by direct modules
        reply = None
        mem_cmd = main.extract_memory(text)
        if mem_cmd:
            reply = main.handle_memory(mem_cmd)
        
        if not reply:
            c_cmd = main.extract_coach(text)
            if c_cmd:
                reply = main.handle_coach(c_cmd)

        if not reply:
            mail_cmd = main.extract_send_email(text)
            if mail_cmd:
                reply = main.handle_send_email(mail_cmd)

        if not reply:
            trib_cmd = main.extract_tribune(text)
            if trib_cmd:
                if trib_cmd.get("action") in ("open", "generate"):
                    handle_tribune_remote(client, chat_id)
                    return
                else:
                    reply = main.handle_tribune(trib_cmd)

        if not reply:
            # Query LLM directly via main conversation pipeline
            reply = main.client.chat.completions.create(
                model=main.MODEL,
                messages=[
                    {"role": "system", "content": main.SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                max_tokens=600,
            ).choices[0].message.content.strip()

        client.send_message(chat_id, reply or "Command acknowledged, Sir.")
        bus.activity(f"Telegram replied to: '{text[:25]}'", "ok")
    except Exception as e:
        client.send_message(chat_id, f"⚠️ *Error processing request:* `{e}`")
        bus.activity(f"Telegram execution error: {e}", "fail")


# ---------------------------------------------------------------------------
# Background Daemon Loop
# ---------------------------------------------------------------------------

def _telegram_worker(cfg: Dict[str, Any]):
    """Background polling worker for Telegram Bot updates."""
    token = cfg.get("bot_token", "").strip()
    if not token:
        print("[telegram_bridge] Telegram bot_token not set. Pocket Uplink is dormant.")
        bus.telegram(enabled=False, connected=False, status="No token provided")
        return

    client = TelegramClient(token)
    me = client.get_me()

    if not me.get("ok"):
        err = me.get("error", "Unknown error")
        print(f"[telegram_bridge] Failed to connect to Telegram Bot: {err}")
        bus.telegram(enabled=True, connected=False, status=f"Auth error: {err}")
        return

    bot_user = me["result"]
    username = bot_user.get("username", "UnknownBot")
    print(f"[telegram_bridge] Pocket Uplink connected to Telegram as @{username}")

    with _state_lock:
        _bot_info["enabled"] = True
        _bot_info["connected"] = True
        _bot_info["username"] = username
        _bot_info["bot_id"] = bot_user.get("id")
        _bot_info["authorized_users"] = cfg.get("allowed_chat_ids", [])

    bus.telegram(
        enabled=True,
        connected=True,
        bot_username=f"@{username}",
        authorized_count=len(_bot_info["authorized_users"]),
    )
    bus.activity(f"Telegram Pocket Uplink active (@{username})", "ok")

    offset = None
    while not _stop_event.is_set():
        try:
            updates = client.get_updates(offset=offset, timeout=20)
            for u in updates:
                offset = u["update_id"] + 1
                msg = u.get("message") or u.get("edited_message")
                if msg:
                    dispatch_telegram_message(client, msg, cfg)
        except Exception as e:
            if not _stop_event.is_set():
                time.sleep(2.0)

    print("[telegram_bridge] Telegram daemon stopped.")
    bus.telegram(connected=False, status="Offline")


def start_telegram_daemon() -> bool:
    """Start the Telegram Pocket Uplink background thread."""
    global _daemon_thread
    if _daemon_thread and _daemon_thread.is_alive():
        return True

    cfg = load_telegram_config()
    if not cfg.get("enabled", True):
        return False

    _stop_event.clear()
    _daemon_thread = threading.Thread(target=_telegram_worker, args=(cfg,), daemon=True)
    _daemon_thread.start()
    return True


def stop_telegram_daemon():
    """Stop the background polling thread."""
    _stop_event.set()


def get_status() -> Dict[str, Any]:
    """Return live status of the Telegram bridge for HUD and API."""
    with _state_lock:
        return dict(_bot_info)
