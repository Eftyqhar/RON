"""Work & Gaming "Protocols" (One-Command Workspace Automation) for RON.

Orchestrates multi-step workstation automation routines on a single voice
command:
- Volume leveling & muting (via `volume.py`)
- Application launching (via `tools.open_app`)
- Distraction app closing (via `taskkill`)
- Developer & workspace URLs (via `tools.open_website`)
- Focus audio / music (via `tools.play_youtube`)
- Windows Power Scheme switching (`powercfg`)
- Workstation locking (`ctypes.windll.user32.LockWorkStation`)
- Event publishing to HUD (`bus.protocol`)

Two rules inherited from the rest of RON:
1. Never raises into the caller. Any failed application or setting is noted
   gracefully without aborting the rest of the sequence.
2. Nothing at import time. Settings from `config.json` are read lazily.
"""

import ctypes
import json
import os
import subprocess
import threading
import time

import bus
import sfx
import tools
import volume

# Standard Windows Power Schemes
POWER_PLANS = {
    "high_performance": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
    "balanced": "381b4222-f694-41f0-9685-ff5bb560df2e",
    "power_saver": "a1841308-3541-4fab-bc81-f71556f20b4a",
}

DEFAULT_PROTOCOLS = {
    "work": {
        "id": "work",
        "name": "WORK PROTOCOL",
        "description": "Prime development workspace, set focus volume, open repositories and lofi audio",
        "volume": 35,
        "launch": ["vs code", "terminal"],
        "urls": ["https://github.com"],
        "close": ["steam.exe", "discord.exe"],
        "music": "lofi hip hop radio beats to relax/study to",
        "power_plan": "balanced",
        "spoken_en": "Work protocol initiated, Sir. Development environment primed, volume balanced, and focus music engaged.",
        "spoken_bn": "ওয়ার্ক প্রোটোকল চালু করা হয়েছে, স্যার। ডেভেলপমেন্ট এনভায়রনমেন্ট প্রস্তুত এবং ভলিউম সেট করা হয়েছে।",
    },
    "gaming": {
        "id": "gaming",
        "name": "GAMING PROTOCOL",
        "description": "Switch power plan to High Performance, unmute audio, and launch gaming clients",
        "volume": 75,
        "launch": ["steam", "discord"],
        "close": ["winword.exe", "excel.exe", "powerpnt.exe"],
        "power_plan": "high_performance",
        "spoken_en": "Gaming protocol engaged, Sir. Switching power profile to High Performance, unmuting communications, and launching gaming clients. Good luck out there.",
        "spoken_bn": "গেমিং প্রোটোকল সক্রিয় করা হয়েছে, স্যার। হাই পারফরম্যান্স পাওয়ার মোড ও গেমিং ক্লায়েন্ট চালু করা হয়েছে।",
    },
    "lockdown": {
        "id": "lockdown",
        "name": "PROTOCOL ZERO",
        "description": "Emergency lockdown: instantly mute all audio and lock the Windows workstation",
        "volume_mute": True,
        "lock_pc": True,
        "spoken_en": "Protocol Zero engaged. Workstation locked, Sir.",
        "spoken_bn": "প্রোটোকল জিরো কার্যকর করা হয়েছে। ওয়ার্কস্টেশন লক করা হয়েছে, স্যার।",
    },
    "sleep": {
        "id": "sleep",
        "name": "SLEEP PROTOCOL",
        "description": "Lower audio, clear active workspaces, and prepare workstation for night rest",
        "volume": 10,
        "spoken_en": "Sleep protocol initiated. Powering down active audio and preparing for rest. Goodnight, Sir.",
        "spoken_bn": "স্লিপ প্রোটোকল সক্রিয় করা হয়েছে। শুভরাত্রি, স্যার।",
    },
    "study": {
        "id": "study",
        "name": "STUDY PROTOCOL",
        "description": "Quiet environment, notepad scratchpad, and ambient study music",
        "volume": 25,
        "launch": ["notepad"],
        "music": "classical music for studying mozart",
        "spoken_en": "Study protocol initiated, Sir. Quiet audio environment prepared and ambient music running.",
        "spoken_bn": "স্টাডি প্রোটোকল শুরু হয়েছে, স্যার। শান্ত পরিবেশ ও স্টাডি মিউজিক চালু করা হয়েছে।",
    },
}


def _load_config_protocols() -> dict:
    """Read user-defined protocol overrides or extensions from config.json."""
    protocols = dict(DEFAULT_PROTOCOLS)
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            custom = cfg.get("protocols") or {}
            for k, v in custom.items():
                proto_key = k.lower().strip()
                if isinstance(v, dict):
                    merged = dict(protocols.get(proto_key, {}))
                    merged.update(v)
                    merged["id"] = proto_key
                    if "name" not in merged:
                        merged["name"] = f"{proto_key.upper()} PROTOCOL"
                    protocols[proto_key] = merged
        except Exception as e:
            print(f"[protocol] Could not load custom protocols from config.json: {e}")
    return protocols


def set_power_plan(plan_name: str) -> bool:
    """Set the Windows active power scheme (high_performance, balanced, power_saver)."""
    guid = POWER_PLANS.get(plan_name.lower().strip())
    if not guid:
        return False
    try:
        subprocess.run(["powercfg", "/setactive", guid],
                       capture_output=True, check=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        return True
    except Exception as e:
        print(f"[protocol] Failed to switch power plan: {e}")
        return False


def close_application(proc_name: str) -> bool:
    """Gracefully or forcefully terminate a process by executable name."""
    if not proc_name:
        return False
    try:
        clean_name = proc_name.strip()
        if not clean_name.lower().endswith(".exe"):
            clean_name = f"{clean_name}.exe"
        cmd = ["taskkill", "/IM", clean_name, "/F"]
        subprocess.run(cmd, capture_output=True, check=False,
                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        return True
    except Exception as e:
        print(f"[protocol] Failed to close {proc_name}: {e}")
        return False


def lock_workstation() -> bool:
    """Lock the Windows desktop session immediately."""
    try:
        if os.name == "nt":
            return bool(ctypes.windll.user32.LockWorkStation())
        return False
    except Exception as e:
        print(f"[protocol] Failed to lock workstation: {e}")
        return False


def execute_protocol(protocol_name: str, lang: str = "en") -> dict:
    """Execute a full protocol sequence by name.
    
    Returns a dict with:
      - 'ok': bool
      - 'id': str
      - 'name': str
      - 'steps': list of {'action': str, 'detail': str, 'ok': bool}
      - 'spoken': str
    """
    protocols = _load_config_protocols()
    key = (protocol_name or "").lower().strip()

    # Match aliases
    if key in ("zero", "stealth", "lockdown", "lock"):
        key = "lockdown"
    elif key in ("code", "coding", "dev", "work"):
        key = "work"
    elif key in ("game", "gaming", "play"):
        key = "gaming"
    elif key in ("night", "sleep", "goodnight"):
        key = "sleep"

    proto = protocols.get(key)
    if not proto:
        err_msg = f"Unknown protocol '{protocol_name}', Sir." if lang != "bn" else f"স্যার, '{protocol_name}' নামে কোনো প্রোটোকল পাওয়া যায়নি।"
        return {
            "ok": False,
            "id": key,
            "name": (protocol_name or "UNKNOWN").upper(),
            "steps": [],
            "spoken": err_msg,
        }

    p_name = proto.get("name", f"{key.upper()} PROTOCOL")
    steps = []

    # Announce initiation on bus
    bus.set_state(bus.EXECUTING, f"INITIATING {p_name}")
    bus.activity(f"Starting {p_name}", "pending")
    bus.protocol(id=key, name=p_name, status="executing", steps=[])

    # Cinematic Sci-Fi Audio FX: Iron Man servo for operational protocols, lockdown klaxon for Protocol Zero
    if key == "lockdown":
        sfx.play("lockdown")
    else:
        sfx.play("servo")

    # 1. Volume adjustment
    if proto.get("volume_mute"):
        try:
            volume.mute()
            steps.append({"action": "AUDIO", "detail": "Muted system audio", "ok": True})
        except Exception as e:
            steps.append({"action": "AUDIO", "detail": f"Failed to mute: {e}", "ok": False})
    elif "volume" in proto:
        target_vol = int(proto["volume"])
        try:
            volume.unmute()
            volume.set_level(target_vol)
            steps.append({"action": "AUDIO", "detail": f"Volume set to {target_vol}%", "ok": True})
        except Exception as e:
            steps.append({"action": "AUDIO", "detail": f"Failed to set volume: {e}", "ok": False})

    # 2. Power Plan
    if "power_plan" in proto:
        plan = proto["power_plan"]
        ok = set_power_plan(plan)
        steps.append({"action": "POWER", "detail": f"Switched to {plan.replace('_', ' ').title()}", "ok": ok})

    # 3. Close Distraction Apps
    for app in proto.get("close", []):
        ok = close_application(app)
        steps.append({"action": "CLOSE", "detail": f"Terminated {app}", "ok": ok})

    # 4. Launch Primary Apps
    for app in proto.get("launch", []):
        try:
            tools.open_app(app)
            steps.append({"action": "LAUNCH", "detail": f"Started {app}", "ok": True})
        except Exception as e:
            steps.append({"action": "LAUNCH", "detail": f"Failed {app}: {e}", "ok": False})

    # 5. Open Workspace URLs
    for url in proto.get("urls", []):
        try:
            tools.open_website(url)
            steps.append({"action": "BROWSER", "detail": f"Opened {url}", "ok": True})
        except Exception as e:
            steps.append({"action": "BROWSER", "detail": f"Failed {url}: {e}", "ok": False})

    # 6. Ambient Focus Music
    if "music" in proto:
        query = proto["music"]
        try:
            tools.play_youtube(query)
            steps.append({"action": "MUSIC", "detail": f"Playing {query}", "ok": True})
        except Exception as e:
            steps.append({"action": "MUSIC", "detail": f"Music failed: {e}", "ok": False})

    # 7. Lock PC (if requested)
    if proto.get("lock_pc"):
        steps.append({"action": "SECURITY", "detail": "Workstation locked", "ok": True})
        # Broadcast completed frame before locking
        bus.protocol(id=key, name=p_name, status="done", steps=steps)
        # Lock in a brief thread so HUD message can be dispatched first
        threading.Timer(0.3, lock_workstation).start()
    else:
        bus.protocol(id=key, name=p_name, status="done", steps=steps)

    spoken = proto.get(f"spoken_{lang}") or proto.get("spoken_en") or f"{p_name} has been engaged, Sir."
    bus.activity(f"{p_name} completed", "ok")

    return {
        "ok": True,
        "id": key,
        "name": p_name,
        "steps": steps,
        "spoken": spoken,
    }


def hud_payload(result: dict) -> dict:
    """Return flat JSON-serialisable payload for the HUD."""
    return {
        "ok": result.get("ok", False),
        "id": result.get("id", ""),
        "name": result.get("name", "PROTOCOL"),
        "steps": result.get("steps", []),
        "spoken": result.get("spoken", ""),
    }
