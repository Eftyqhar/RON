# startup_manager.py — Windows Auto-Startup Manager for R.O.N.
# Enables or disables silent background launch of RON on Windows system startup.

import os
import sys

STARTUP_DIR = os.path.join(
    os.environ.get("APPDATA", ""),
    "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
)
STARTUP_FILE = os.path.join(STARTUP_DIR, "RON_Silent_Startup.vbs")
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


def is_startup_enabled() -> bool:
    """Check if RON is currently registered in Windows Startup."""
    return os.path.exists(STARTUP_FILE)


def enable_startup() -> bool:
    """Install a silent background launcher into Windows Startup."""
    try:
        os.makedirs(STARTUP_DIR, exist_ok=True)
        safe_dir = PROJECT_DIR.replace("\\", "\\\\")
        vbs_content = f'''Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "{PROJECT_DIR}"
WshShell.Run "pythonw.exe run_mini_hud.py", 0, False
'''
        with open(STARTUP_FILE, "w", encoding="utf-8") as f:
            f.write(vbs_content)
        print(f"[startup_manager] Enabled silent startup: {STARTUP_FILE}")
        return True
    except Exception as e:
        print(f"[startup_manager] Failed to enable startup: {e}")
        return False


def disable_startup() -> bool:
    """Remove RON from Windows Startup."""
    try:
        if os.path.exists(STARTUP_FILE):
            os.remove(STARTUP_FILE)
            print("[startup_manager] Disabled silent startup.")
            return True
        return True
    except Exception as e:
        print(f"[startup_manager] Failed to disable startup: {e}")
        return False


def toggle_startup() -> bool:
    """Toggle between enabled and disabled."""
    if is_startup_enabled():
        disable_startup()
        return False
    else:
        enable_startup()
        return True


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower().strip()
        if cmd in ("enable", "on", "install"):
            enable_startup()
        elif cmd in ("disable", "off", "uninstall", "remove"):
            disable_startup()
        elif cmd in ("status", "check"):
            print("Enabled" if is_startup_enabled() else "Disabled")
    else:
        new_state = toggle_startup()
        print(f"Startup is now: {'ENABLED' if new_state else 'DISABLED'}")
