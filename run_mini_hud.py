"""Launcher for R.O.N. with the Global Floating Mini-HUD & System Tray.

Runs the voice loop in the background while displaying the transparent,
frameless Arc Reactor desktop widget and system tray icon.
"""

import os
import sys
import threading

# Auto-hide console window on Windows if launched via console/bat
if sys.platform == "win32":
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd and os.environ.get("RON_SHOW_CONSOLE", "0") != "1":
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass

import bus
import history
import main
from mini_hud import FloatingMiniHUD


def main_launcher():
    print("=" * 60)
    print("  R.O.N. — Global Floating Mini-HUD & System Tray")
    print("=" * 60)
    print("• Floating Arc Reactor widget: Left-click to expand/collapse")
    print("• Drag & drop anywhere on your screens to reposition")
    print("• Global Hotkey: Press [Ctrl + Shift + Space] from ANY app to summon")
    print("• Right-click widget or system tray icon for quick controls")
    print("=" * 60)

    # Start voice loop on background thread
    voice_thread = threading.Thread(
        target=main.run_voice_loop,
        kwargs={"greet": True},
        daemon=True,
        name="VoiceLoopThread",
    )
    voice_thread.start()

    # Create and run Mini-HUD on main GUI thread
    hud = FloatingMiniHUD()

    def on_voice_shutdown():
        main.shutdown_event.wait()
        hud.shutdown()

    threading.Thread(target=on_voice_shutdown, daemon=True).start()

    try:
        hud.root.mainloop()
    except KeyboardInterrupt:
        print("\n[Mini-HUD interrupted]")
    finally:
        main.shutdown_event.set()
        hud.shutdown()
        history.close()
        print("[R.O.N. Mini-HUD offline]")


if __name__ == "__main__":
    main_launcher()
