"""Windows System Tray integration for R.O.N.

Provides a persistent presence in the taskbar notification area next to the clock.
Shows real-time status and quick controls for:
- Mini-HUD floating widget visibility
- Language switching (English / বাংলা)
- Microphone mute / unmute
- Opening the Full Holographic Web HUD
- System shutdown
"""

import threading
import webbrowser
from PIL import Image, ImageDraw
import pystray
from pystray import MenuItem as item, Menu

import bus
import voice


def create_reactor_icon(color: tuple = (56, 225, 240), size: int = 64) -> Image.Image:
    """Generate a crisp, multi-ring Arc Reactor icon dynamically."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    center = size // 2
    r_outer = size // 2 - 2
    r_mid = size // 3
    r_core = size // 6

    # Dark halo backing
    draw.ellipse(
        [center - r_outer, center - r_outer, center + r_outer, center + r_outer],
        fill=(10, 20, 28, 220),
        outline=color,
        width=2,
    )

    # Segmented middle ring
    draw.ellipse(
        [center - r_mid, center - r_mid, center + r_mid, center + r_mid],
        outline=(*color[:3], 180),
        width=2,
    )

    # Pulsing core
    draw.ellipse(
        [center - r_core, center - r_core, center + r_core, center + r_core],
        fill=color,
        outline=(255, 255, 255, 240),
        width=1,
    )

    # 4 tick marks
    t_len = 4
    draw.line([(center, 2), (center, 2 + t_len)], fill=color, width=2)
    draw.line([(center, size - 2 - t_len), (center, size - 2)], fill=color, width=2)
    draw.line([(2, center), (2 + t_len, center)], fill=color, width=2)
    draw.line([(size - 2 - t_len, center), (size - 2, center)], fill=color, width=2)

    return img


class TrayManager:
    """Manages the lifecycle and menus of the R.O.N. System Tray Icon."""

    def __init__(self, toggle_mini_hud_callback=None, shutdown_callback=None):
        self.toggle_mini_hud = toggle_mini_hud_callback
        self.shutdown_callback = shutdown_callback
        self._icon = None
        self._thread = None
        self._running = False

    def _get_icon_image(self) -> Image.Image:
        lang = bus.get_language()
        if lang == "bn":
            # Emerald green for Bangla
            return create_reactor_icon((0, 230, 118))
        # Electric cyan for English
        return create_reactor_icon((56, 225, 240))

    def _build_menu(self) -> Menu:
        def on_toggle_hud(icon, it):
            if self.toggle_mini_hud:
                self.toggle_mini_hud()

        def set_lang_en(icon, it):
            voice.set_language("en")
            bus.set_language("en")
            self.refresh_icon()

        def set_lang_bn(icon, it):
            voice.set_language("bn")
            bus.set_language("bn")
            self.refresh_icon()

        def on_toggle_mic(icon, it):
            try:
                import main
                if main.voice_enabled.is_set():
                    main.voice_enabled.clear()
                    bus.meta(mic_ok=False)
                    bus.activity("Microphone muted from Tray", "info")
                else:
                    main.voice_enabled.set()
                    bus.meta(mic_ok=True)
                    bus.activity("Microphone unmuted from Tray", "ok")
            except Exception:
                pass

        def on_open_web_hud(icon, it):
            webbrowser.open("http://127.0.0.1:8765")

        def on_exit(icon, it):
            self.stop()
            if self.shutdown_callback:
                self.shutdown_callback()

        def is_lang_en(it):
            return bus.get_language() == "en"

        def is_lang_bn(it):
            return bus.get_language() == "bn"

        def is_mic_active(it):
            try:
                import main
                return main.voice_enabled.is_set()
            except Exception:
                return True

        def on_toggle_startup(icon, it):
            try:
                import startup_manager
                new_state = startup_manager.toggle_startup()
                msg = "Enabled silent Windows auto-startup" if new_state else "Disabled Windows auto-startup"
                bus.activity(msg, "ok")
            except Exception as e:
                bus.activity(f"Startup toggle error: {e}", "fail")

        def is_startup_on(it):
            try:
                import startup_manager
                return startup_manager.is_startup_enabled()
            except Exception:
                return False

        return Menu(
            item("Toggle Mini-HUD", on_toggle_hud, default=True),
            Menu.SEPARATOR,
            item("Language: English", set_lang_en, checked=is_lang_en, radio=True),
            item("Language: বাংলা (Bangla)", set_lang_bn, checked=is_lang_bn, radio=True),
            Menu.SEPARATOR,
            item("Microphone Active", on_toggle_mic, checked=is_mic_active),
            item("Start with Windows (Silent)", on_toggle_startup, checked=is_startup_on),
            item("Open Full Web HUD", on_open_web_hud),
            Menu.SEPARATOR,
            item("Exit R.O.N.", on_exit),
        )

    def start(self):
        """Start the system tray icon in a dedicated daemon thread."""
        if self._running:
            return
        self._running = True

        def _run():
            img = self._get_icon_image()
            self._icon = pystray.Icon(
                "RON_Tray",
                img,
                "R.O.N. AI Assistant",
                menu=self._build_menu(),
            )
            self._icon.run()

        self._thread = threading.Thread(target=_run, daemon=True, name="TrayThread")
        self._thread.start()

    def refresh_icon(self):
        """Update the tray icon graphic (e.g. when language shifts)."""
        if self._icon:
            try:
                self._icon.icon = self._get_icon_image()
            except Exception:
                pass

    def stop(self):
        """Stop and dismantle the tray icon."""
        self._running = False
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
