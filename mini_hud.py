"""R.O.N. Global Floating Mini-HUD Desktop Widget & System Tray Bar

A futuristic, borderless, transparent holographic Arc Reactor desktop companion:
- Floats on top of all windows (games, code editor, browser).
- Draggable with left-click; right-click context menu.
- Rotating animated rings, pulsating core, and real-time audio reactivity.
- Audio visualizer waveforms that react in real-time to microphone & speech.
- Mini CPU/RAM/Disk circular gauges (Iron Man Arc Reactor style) that glow red when CPU usage spikes over 85%.
- Clickable quick-actions toolbar (Mic, Screenshot, Weather, Tribune, Protocol, Lock, Web HUD).
- Live on-screen floating notification toasts on top of all games and apps.
- Dynamic color adaptation: Electric Cyan (English) / Emerald Green (Bangla) / Crimson (Overload/Error).
- Slide-out quick holographic command entry summoned via click or Global Hotkey (Ctrl + Shift + Space).
- System tray icon for background operation and taskbar controls.
"""

import ctypes
from ctypes import wintypes
import datetime
import math
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from typing import Any, Dict, Optional
import webbrowser
import win32con

import bus
import voice
from tray import TrayManager

# Optional system & imaging libraries
try:
    import psutil
except ImportError:
    psutil = None

try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None

# Windows API for Global Hotkey
user32 = ctypes.windll.user32
HOTKEY_ID = 101
MODIFIERS = win32con.MOD_CONTROL | win32con.MOD_SHIFT
VK_KEY = win32con.VK_SPACE

# Transparency Colorkey
TRANS_BG = "#000001"

# Color Palettes
PALETTES = {
    "en": {
        "accent": "#38E1F0",
        "bright": "#C4F8FF",
        "dim": "#15424D",
        "glow": "#091D24",
        "glass": "#06131C",
        "text": "#D6F7FF",
    },
    "bn": {
        "accent": "#00E676",
        "bright": "#B9F6CA",
        "dim": "#084D28",
        "glow": "#042614",
        "glass": "#04170E",
        "text": "#D7FFE8",
    },
    "error": {
        "accent": "#FF4A5E",
        "bright": "#FFD2D7",
        "dim": "#52161D",
        "glow": "#260B0E",
        "glass": "#1A0608",
        "text": "#FFE6E8",
    },
    "alert": {
        "accent": "#FF1E40",
        "bright": "#FFA0B0",
        "dim": "#660D1A",
        "glow": "#33060D",
        "glass": "#1A0307",
        "text": "#FFDEE4",
    },
}


class FloatingMiniHUD:
    """The floating frameless Iron Man Arc Reactor desktop widget."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("RON Mini-HUD")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", TRANS_BG)
        self.root.configure(bg=TRANS_BG)

        # Positioning: bottom-right above taskbar
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.orb_size = 116
        self.collapsed_w = 116
        self.win_h = 116
        self.expanded_w = 560
        self.x = sw - self.collapsed_w - 30
        self.y = sh - self.win_h - 60
        self.is_expanded = False

        self.root.geometry(f"{self.collapsed_w}x{self.win_h}+{self.x}+{self.y}")

        # State and animation variables
        self.lang = bus.get_language()
        self.state = "idle"
        self.detail = "ALL SYSTEMS NOMINAL"
        self.level = 0.0
        self.target_level = 0.0
        self.angle_ticks = 0.0
        self.angle_ring1 = 0.0
        self.angle_ring2 = 0.0
        self.wave_phase = 0.0
        self.caption = "Ready for command"

        # System Telemetry Gauges (Arc Reactor Style)
        self.cpu_pct = 0.0
        self.target_cpu = 0.0
        self.ram_pct = 0.0
        self.target_ram = 0.0
        self.disk_pct = 0.0
        self._cpu_alert_active = False

        # Live Notification System
        self.notif_active = False
        self.notif_title = ""
        self.notif_msg = ""
        self.notif_level = "info"
        self.notif_expire = 0.0
        self._notif_lock = threading.Lock()

        # Workspace Protocol State tracking
        self.current_protocol_idx = 0
        self.protocols = ["normal", "work", "gaming"]

        # Drag tracking
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._has_dragged = False

        # Build UI layout
        self._build_widgets()

        # Context Menu
        self._build_context_menu()

        # Bindings
        self._bind_events()

        # Tray Manager
        self.tray = TrayManager(
            toggle_mini_hud_callback=self.toggle_visibility_from_tray,
            shutdown_callback=self.shutdown,
        )
        self.tray.start()

        # Background threads: Event Bus Subscriber, Hotkey & Telemetry
        self._running = True
        self._sub_thread = threading.Thread(target=self._bus_listener, daemon=True)
        self._sub_thread.start()

        self._hotkey_thread = threading.Thread(target=self._hotkey_listener, daemon=True)
        self._hotkey_thread.start()

        self._telemetry_thread = threading.Thread(target=self._telemetry_worker, daemon=True)
        self._telemetry_thread.start()

        # Start animation frame loop
        self.root.after(30, self._render_frame)

    def _get_palette(self):
        if self.state == "error" or (self.cpu_pct >= 85.0 and (int(self.angle_ticks) % 20 > 10)):
            return PALETTES["alert"]
        if self.state == "error":
            return PALETTES["error"]
        return PALETTES.get(self.lang, PALETTES["en"])

    def _build_widgets(self):
        # Master container
        self.container = tk.Frame(self.root, bg=TRANS_BG)
        self.container.pack(fill=tk.BOTH, expand=True)

        # Left: Arc Reactor Orb Canvas (116x116)
        self.canvas = tk.Canvas(
            self.container,
            width=self.collapsed_w,
            height=self.win_h,
            bg=TRANS_BG,
            highlightthickness=0,
            cursor="hand2",
        )
        self.canvas.pack(side=tk.LEFT, fill=tk.Y)

        # Right: Slide-out Holographic Command & Telemetry Frame
        self.bar_frame = tk.Frame(self.container, bg=TRANS_BG, padx=8, pady=4)

        # 1. Top Strip: Status header + Live Telemetry Badges
        self.top_strip = tk.Frame(self.bar_frame, bg=TRANS_BG)
        self.top_strip.pack(fill=tk.X, pady=(0, 2))

        self.status_lbl = tk.Label(
            self.top_strip,
            text="R.O.N. CORE · STANDBY · LANG: EN",
            font=("Segoe UI", 8, "bold"),
            bg=TRANS_BG,
            fg=PALETTES["en"]["accent"],
            anchor="w",
        )
        self.status_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Live Telemetry Badges container
        self.badge_box = tk.Frame(self.top_strip, bg=TRANS_BG)
        self.badge_box.pack(side=tk.RIGHT)

        self.cpu_badge = tk.Label(
            self.badge_box,
            text="CPU 0%",
            font=("Segoe UI", 7, "bold"),
            bg="#06131C",
            fg=PALETTES["en"]["accent"],
            padx=4,
            pady=1,
            relief=tk.FLAT,
        )
        self.cpu_badge.pack(side=tk.LEFT, padx=2)

        self.ram_badge = tk.Label(
            self.badge_box,
            text="RAM 0%",
            font=("Segoe UI", 7, "bold"),
            bg="#06131C",
            fg=PALETTES["en"]["bright"],
            padx=4,
            pady=1,
            relief=tk.FLAT,
        )
        self.ram_badge.pack(side=tk.LEFT, padx=2)

        self.disk_badge = tk.Label(
            self.badge_box,
            text="DSK 0%",
            font=("Segoe UI", 7, "bold"),
            bg="#06131C",
            fg="#8BA3AF",
            padx=4,
            pady=1,
            relief=tk.FLAT,
        )
        self.disk_badge.pack(side=tk.LEFT, padx=2)

        # 2. Live Notification Toast Banner (Hidden until event occurs)
        self.notif_frame = tk.Frame(
            self.bar_frame,
            bg="#0B1F2D",
            padx=6,
            pady=2,
            relief=tk.FLAT,
        )
        self.notif_icon_lbl = tk.Label(
            self.notif_frame,
            text="⚡",
            font=("Segoe UI", 8),
            bg="#0B1F2D",
            fg=PALETTES["en"]["bright"],
        )
        self.notif_icon_lbl.pack(side=tk.LEFT, padx=(0, 4))

        self.notif_text_lbl = tk.Label(
            self.notif_frame,
            text="",
            font=("Segoe UI", 8, "bold"),
            bg="#0B1F2D",
            fg="#C4F8FF",
            anchor="w",
            cursor="hand2",
        )
        self.notif_text_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.notif_close_btn = tk.Label(
            self.notif_frame,
            text="✕",
            font=("Segoe UI", 7, "bold"),
            bg="#0B1F2D",
            fg="#8BA3AF",
            cursor="hand2",
        )
        self.notif_close_btn.pack(side=tk.RIGHT, padx=(4, 0))
        self.notif_close_btn.bind("<Button-1>", lambda e: self._dismiss_notification())
        self.notif_text_lbl.bind("<Button-1>", lambda e: self._dismiss_notification())

        # 3. Holographic Input pill container
        self.input_border = tk.Frame(
            self.bar_frame,
            bg=PALETTES["en"]["accent"],
            padx=1,
            pady=1,
        )
        self.input_border.pack(fill=tk.X, pady=(2, 2))

        self.cmd_entry = tk.Entry(
            self.input_border,
            font=("Segoe UI", 10),
            bg=PALETTES["en"]["glass"],
            fg=PALETTES["en"]["bright"],
            insertbackground=PALETTES["en"]["bright"],
            relief=tk.FLAT,
        )
        self.cmd_entry.pack(fill=tk.X, ipady=2, padx=4)

        # 4. Clickable Holographic Quick-Actions Toolbar
        self.qa_frame = tk.Frame(self.bar_frame, bg=TRANS_BG)
        self.qa_frame.pack(fill=tk.X, pady=(2, 2))

        qa_actions = [
            ("🎙️ Mic", self._qa_toggle_mic),
            ("📸 Snap", self._qa_screenshot),
            ("🌦️ Weather", self._qa_weather),
            ("📰 News", self._qa_tribune),
            ("⚡ Protocol", self._qa_protocol),
            ("🔒 Lock", self._qa_lock),
            ("🌐 HUD", self._qa_web_hud),
        ]

        self.qa_buttons = []
        pal = PALETTES["en"]
        for label, cmd_fn in qa_actions:
            btn = tk.Label(
                self.qa_frame,
                text=label,
                font=("Segoe UI", 7, "bold"),
                bg="#081621",
                fg=pal["bright"],
                padx=5,
                pady=2,
                cursor="hand2",
                relief=tk.FLAT,
            )
            btn.pack(side=tk.LEFT, padx=1, expand=True, fill=tk.X)
            btn.bind("<Button-1>", lambda e, fn=cmd_fn: fn())
            btn.bind("<Enter>", lambda e, b=btn: b.config(bg="#15424D", fg="#FFFFFF"))
            btn.bind("<Leave>", lambda e, b=btn: b.config(bg="#081621", fg=self._get_palette()["bright"]))
            self.qa_buttons.append(btn)

        # 5. Live subtitle / caption text
        self.caption_lbl = tk.Label(
            self.bar_frame,
            text="Ready for command (press Enter to execute)",
            font=("Segoe UI", 8),
            bg=TRANS_BG,
            fg="#8BA3AF",
            anchor="w",
        )
        self.caption_lbl.pack(fill=tk.X, pady=(1, 0))

    def _build_context_menu(self):
        self.menu = tk.Menu(self.root, tearoff=0, bg="#0A1620", fg="#C4F8FF", activebackground="#15424D")
        self.menu.add_command(label="Toggle Command Bar", command=self.toggle_command_bar)
        self.menu.add_separator()
        self.menu.add_command(label="English Mode", command=self._set_english_mode)
        self.menu.add_command(label="বাংলা Mode (Bangla)", command=self._set_bangla_mode)
        self.menu.add_separator()
        self.menu.add_command(label="Toggle Microphone", command=self._toggle_mic)
        self.menu.add_command(label="Capture Desktop (Snap)", command=self._qa_screenshot)
        self.menu.add_command(label="Lock Workstation", command=self._qa_lock)

        def _toggle_startup():
            try:
                import startup_manager
                new_state = startup_manager.toggle_startup()
                msg = "Enabled silent Windows auto-startup" if new_state else "Disabled Windows auto-startup"
                self.show_notification("WINDOWS STARTUP", msg, "ok" if new_state else "info", 3.0)
            except Exception as e:
                self.show_notification("STARTUP ERROR", str(e), "alert", 3.0)

        self.menu.add_command(label="Start with Windows (Silent)", command=_toggle_startup)
        self.menu.add_command(label="Open Full Web HUD", command=lambda: webbrowser.open("http://127.0.0.1:8765"))
        self.menu.add_separator()
        self.menu.add_command(label="Hide Widget", command=self.hide)
        self.menu.add_command(label="Exit R.O.N.", command=self.shutdown)

    def _bind_events(self):
        # Orb drag & click
        self.canvas.bind("<Button-1>", self._on_mouse_down)
        self.canvas.bind("<B1-Motion>", self._on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
        self.canvas.bind("<Button-3>", self._on_right_click)

        # Command entry
        self.cmd_entry.bind("<Return>", self._on_submit_cmd)
        self.cmd_entry.bind("<Escape>", lambda e: self.collapse_command_bar())

    def _on_mouse_down(self, event):
        self._drag_start_x = event.x_root - self.x
        self._drag_start_y = event.y_root - self.y
        self._has_dragged = False

    def _on_mouse_drag(self, event):
        self._has_dragged = True
        self.x = event.x_root - self._drag_start_x
        self.y = event.y_root - self._drag_start_y
        w = self.expanded_w if self.is_expanded else self.collapsed_w
        self.root.geometry(f"{w}x{self.win_h}+{self.x}+{self.y}")

    def _on_mouse_up(self, event):
        if not self._has_dragged:
            # Click without dragging toggles the command bar
            self.toggle_command_bar()

    def _on_right_click(self, event):
        self.menu.tk_popup(event.x_root, event.y_root)

    def toggle_command_bar(self):
        if self.is_expanded:
            self.collapse_command_bar()
        else:
            self.expand_command_bar()

    def expand_command_bar(self):
        self.is_expanded = True
        self.bar_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.root.geometry(f"{self.expanded_w}x{self.win_h}+{self.x}+{self.y}")
        self.cmd_entry.focus_set()

    def collapse_command_bar(self):
        self.is_expanded = False
        self.bar_frame.pack_forget()
        self.root.geometry(f"{self.collapsed_w}x{self.win_h}+{self.x}+{self.y}")

    def toggle_visibility_from_tray(self):
        self.root.after(0, self._toggle_vis)

    def _toggle_vis(self):
        if self.root.winfo_viewable():
            self.root.withdraw()
        else:
            self.root.deiconify()
            self.root.attributes("-topmost", True)

    def hide(self):
        self.root.withdraw()

    def _set_english_mode(self):
        voice.set_language("en")
        bus.set_language("en")
        self.tray.refresh_icon()
        self.show_notification("LANGUAGE MODE", "Switched to English (en-GB Neural JARVIS)", "info", 3.0)

    def _set_bangla_mode(self):
        voice.set_language("bn")
        bus.set_language("bn")
        self.tray.refresh_icon()
        self.show_notification("ভাষা মোড", "বাংলা মোড সক্রিয় করা হয়েছে (bn-BD Neural)", "info", 3.0)

    def _toggle_mic(self):
        try:
            import main
            if main.voice_enabled.is_set():
                main.voice_enabled.clear()
                bus.meta(mic_ok=False)
                self.show_notification("MICROPHONE", "Audio Input Muted (Dormant)", "info", 2.5)
            else:
                main.voice_enabled.set()
                bus.meta(mic_ok=True)
                self.show_notification("MICROPHONE", "Audio Input Active (Listening)", "ok", 2.5)
        except Exception:
            pass

    # ── Quick Actions ───────────────────────────────────────────────────────

    def _qa_toggle_mic(self):
        self._toggle_mic()

    def _qa_screenshot(self):
        self.caption_lbl.config(text="📸 Capturing desktop screenshot...")
        def _worker():
            try:
                snap_dir = os.path.join(os.path.expanduser("~"), "Pictures")
                os.makedirs(snap_dir, exist_ok=True)
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                snap_path = os.path.join(snap_dir, f"RON_Screen_{ts}.png")

                captured = False
                if ImageGrab:
                    try:
                        im = ImageGrab.grab()
                        im.save(snap_path)
                        captured = True
                    except Exception:
                        pass

                if captured and os.path.exists(snap_path):
                    self.show_notification("SCREEN CAPTURED", f"Saved to Pictures/RON_Screen_{ts}.png", "ok", 4.0)
                    self.root.after(0, lambda: self.caption_lbl.config(text=f"📸 Saved: RON_Screen_{ts}.png"))
                else:
                    self.show_notification("SCREEN CAPTURE", "Failed to capture desktop", "alert", 3.0)
            except Exception as e:
                self.show_notification("CAPTURE ERROR", str(e), "alert", 3.0)

        threading.Thread(target=_worker, daemon=True).start()

    def _qa_weather(self):
        self.caption_lbl.config(text="🌦️ Fetching live weather telemetry...")
        def _worker():
            try:
                import weather
                obs = weather.observe()
                if obs and "temp" in obs:
                    temp = round(float(obs.get("temp", 0)))
                    cond = (obs.get("condition") or "Clear").title()
                    place = obs.get("place", "Local")
                    msg = f"{temp}°C · {cond} ({place})"
                    self.show_notification("WEATHER TELEMETRY", msg, "info", 4.5)
                    self.root.after(0, lambda: self.caption_lbl.config(text=f"🌦️ {msg}"))
                else:
                    self.show_notification("WEATHER", "Weather telemetry unavailable", "alert", 3.0)
            except Exception as e:
                self.show_notification("WEATHER ERROR", str(e), "alert", 3.0)

        threading.Thread(target=_worker, daemon=True).start()

    def _qa_tribune(self):
        self.caption_lbl.config(text="📰 Accessing The RON World Tribune...")
        def _worker():
            try:
                import tribune
                edition = tribune.build_today_tribune(generate_pdf_doc=True)
                pdf = edition.get("pdf_path")
                if pdf and os.path.exists(pdf):
                    os.startfile(pdf)
                    self.show_notification("RON TRIBUNE", f"Edition {edition.get('edition_id')} opened in PDF reader", "ok", 4.0)
                else:
                    self.show_notification("RON TRIBUNE", "Broadsheet compiled successfully", "ok", 3.5)
            except Exception as e:
                self.show_notification("TRIBUNE ERROR", str(e), "alert", 3.0)

        threading.Thread(target=_worker, daemon=True).start()

    def _qa_protocol(self):
        self.current_protocol_idx = (self.current_protocol_idx + 1) % len(self.protocols)
        prot = self.protocols[self.current_protocol_idx]
        self.caption_lbl.config(text=f"⚡ Activating {prot.upper()} Protocol...")
        def _worker():
            try:
                import protocol
                rep = protocol.activate_protocol(prot)
                self.show_notification("PROTOCOL ACTIVATED", f"{prot.upper()} Protocol now active", "ok", 3.5)
                self.root.after(0, lambda: self.caption_lbl.config(text=f"⚡ {prot.upper()} Protocol engaged"))
            except Exception as e:
                self.show_notification("PROTOCOL ERROR", str(e), "alert", 3.0)

        threading.Thread(target=_worker, daemon=True).start()

    def _qa_lock(self):
        try:
            ctypes.windll.user32.LockWorkStation()
            self.show_notification("WORKSTATION LOCKED", "Security perimeter engaged", "ok", 3.0)
        except Exception as e:
            self.show_notification("LOCK FAILED", str(e), "alert", 3.0)

    def _qa_web_hud(self):
        webbrowser.open("http://127.0.0.1:8765")
        self.show_notification("HOLOGRAPHIC HUD", "Opening Full Browser Web HUD...", "info", 2.5)

    # ── Notification System ─────────────────────────────────────────────────

    def show_notification(self, title: str, message: str, level: str = "info", duration: float = 4.0):
        """Display a floating holographic notification on top of all games and apps."""
        with self._notif_lock:
            self.notif_active = True
            self.notif_title = title.upper()
            self.notif_msg = message
            self.notif_level = level
            self.notif_expire = time.time() + duration

        self.root.after(0, self._render_notification_state)

    def _dismiss_notification(self):
        with self._notif_lock:
            self.notif_active = False
            self.notif_expire = 0.0
        self.root.after(0, self._render_notification_state)

    def _render_notification_state(self):
        if not self.is_expanded:
            return

        if self.notif_active and time.time() < self.notif_expire:
            icon = "⚡" if self.notif_level == "alert" else ("✅" if self.notif_level == "ok" else "🔔")
            bg_color = "#2B090F" if self.notif_level == "alert" else "#082333"
            fg_color = "#FFA0B0" if self.notif_level == "alert" else "#C4F8FF"

            self.notif_frame.config(bg=bg_color)
            self.notif_icon_lbl.config(text=icon, bg=bg_color, fg=fg_color)
            self.notif_text_lbl.config(
                text=f"{self.notif_title}: {self.notif_msg[:55]}",
                bg=bg_color,
                fg=fg_color,
            )
            self.notif_close_btn.config(bg=bg_color)
            if not self.notif_frame.winfo_ismapped():
                self.notif_frame.pack(fill=tk.X, before=self.input_border, pady=(1, 3))
        else:
            if self.notif_frame.winfo_ismapped():
                self.notif_frame.pack_forget()

    # ── Telemetry Worker Thread ─────────────────────────────────────────────

    def _telemetry_worker(self):
        """Background thread updating CPU, RAM, and Disk metrics."""
        while self._running:
            try:
                if psutil:
                    cpu = psutil.cpu_percent(interval=1.0)
                    ram = psutil.virtual_memory().percent
                    self.target_cpu = float(cpu)
                    self.target_ram = float(ram)

                    try:
                        disk = psutil.disk_usage("C:").percent
                        self.disk_pct = float(disk)
                    except Exception:
                        self.disk_pct = 0.0

                    # CPU Spike alert over 85%
                    if self.target_cpu >= 85.0 and not self._cpu_alert_active:
                        self._cpu_alert_active = True
                        self.show_notification(
                            "CORE OVERLOAD ALERT",
                            f"CPU spiked to {int(self.target_cpu)}%! Heavy load active.",
                            level="alert",
                            duration=5.0,
                        )
                    elif self.target_cpu < 80.0:
                        self._cpu_alert_active = False
                else:
                    time.sleep(2.0)
            except Exception:
                time.sleep(2.0)

    def _on_submit_cmd(self, event):
        cmd = self.cmd_entry.get().strip()
        if not cmd:
            return
        self.cmd_entry.delete(0, tk.END)
        self.caption_lbl.config(text=f"Executing: {cmd}")

        def _exec():
            try:
                import main
                main.process_command(cmd)
            except Exception as e:
                self.root.after(0, lambda: self.caption_lbl.config(text=f"Error: {e}"))

        threading.Thread(target=_exec, daemon=True).start()

    # ── Arc Reactor Rendering Loop ──────────────────────────────────────────

    def _render_frame(self):
        if not self._running:
            return

        # Smooth audio level & telemetry interpolation
        self.level += (self.target_level - self.level) * 0.25
        self.cpu_pct += (self.target_cpu - self.cpu_pct) * 0.12
        self.ram_pct += (self.target_ram - self.ram_pct) * 0.12
        self.wave_phase = (self.wave_phase + 0.16) % (2 * math.pi)

        lvl = max(0.0, min(1.0, self.level))
        cpu_val = max(0.0, min(100.0, self.cpu_pct))
        ram_val = max(0.0, min(100.0, self.ram_pct))

        # Advance rotation angles based on energy state
        spin_rate = 2.8 if self.state in ("listening", "speaking", "thinking") else (2.0 if cpu_val >= 85.0 else 1.0)
        self.angle_ticks = (self.angle_ticks + 0.4 * spin_rate) % 360
        self.angle_ring1 = (self.angle_ring1 + 1.8 * spin_rate) % 360
        self.angle_ring2 = (self.angle_ring2 - 2.2 * spin_rate) % 360

        pal = self._get_palette()
        accent = pal["accent"]
        bright = pal["bright"]
        dim = pal["dim"]

        self.canvas.delete("all")
        cx, cy = 58, 58

        # 1. Dark circular backing with subtle reactor border
        self.canvas.create_oval(
            cx - 52, cy - 52, cx + 52, cy + 52,
            fill="#060E15", outline=dim, width=1.5,
        )

        # 2. Mini Circular Arc Gauges (Iron Man Arc Reactor Style)
        r_gauge = 50
        # Background dimmed tracks
        self.canvas.create_arc(
            cx - r_gauge, cy - r_gauge, cx + r_gauge, cy + r_gauge,
            start=15, extent=110, outline=dim, width=2, style=tk.ARC,
        )
        self.canvas.create_arc(
            cx - r_gauge, cy - r_gauge, cx + r_gauge, cy + r_gauge,
            start=195, extent=110, outline=dim, width=2, style=tk.ARC,
        )

        # Active CPU Arc (Top-Right: 15° to 125°)
        # When CPU >= 85%, pulses warning Crimson Red!
        cpu_extent = max(3, (cpu_val / 100.0) * 110)
        if cpu_val >= 85.0:
            cpu_color = "#FF1E40" if (int(self.angle_ticks) % 16 > 8) else "#FF667A"
            cpu_width = 3.5
        elif cpu_val >= 70.0:
            cpu_color = "#FFAA00"
            cpu_width = 2.5
        else:
            cpu_color = accent
            cpu_width = 2.5

        self.canvas.create_arc(
            cx - r_gauge, cy - r_gauge, cx + r_gauge, cy + r_gauge,
            start=15, extent=cpu_extent, outline=cpu_color, width=cpu_width, style=tk.ARC,
        )

        # Active RAM Arc (Bottom-Left: 195° to 305°)
        ram_extent = max(3, (ram_val / 100.0) * 110)
        ram_color = "#FF1E40" if ram_val >= 85.0 else ("#FFAA00" if ram_val >= 70.0 else bright)
        ram_width = 3.0 if ram_val >= 85.0 else 2.5
        self.canvas.create_arc(
            cx - r_gauge, cy - r_gauge, cx + r_gauge, cy + r_gauge,
            start=195, extent=ram_extent, outline=ram_color, width=ram_width, style=tk.ARC,
        )

        # 3. Audio Visualizer Spectrum Waveform (24 Radial Dancing Equalizer Bars)
        r_wave_base = 38
        num_bars = 24
        for i in range(num_bars):
            deg = math.radians(i * (360 / num_bars) + self.angle_ticks * 0.15)
            # Harmonic frequency oscillation
            harm = math.sin(i * 0.75 + self.wave_phase) * 0.5 + 0.5
            bar_height = (lvl * 10.5 * harm) + (math.sin(self.wave_phase * 0.8 + i) * 1.5 if lvl < 0.05 else 0)
            bar_height = max(0.5, min(9.0, bar_height))

            x1 = cx + r_wave_base * math.cos(deg)
            y1 = cy + r_wave_base * math.sin(deg)
            x2 = cx + (r_wave_base + bar_height) * math.cos(deg)
            y2 = cy + (r_wave_base + bar_height) * math.sin(deg)

            bar_color = bright if (lvl > 0.05 or i % 4 == 0) else dim
            self.canvas.create_line(x1, y1, x2, y2, fill=bar_color, width=1.5 if lvl > 0.05 else 1.0)

        # 4. Outer Rotating Segmented Ring (4 arcs)
        r1 = 33
        for i in range(4):
            start_deg = (self.angle_ring1 + i * 90) % 360
            self.canvas.create_arc(
                cx - r1, cy - r1, cx + r1, cy + r1,
                start=start_deg, extent=54,
                outline=accent, width=2, style=tk.ARC,
            )

        # 5. Middle Counter-Rotating Ring (3 arcs)
        r2 = 25
        for i in range(3):
            start_deg = (self.angle_ring2 + i * 120) % 360
            self.canvas.create_arc(
                cx - r2, cy - r2, cx + r2, cy + r2,
                start=start_deg, extent=75,
                outline=bright, width=1.5, style=tk.ARC,
            )

        # 6. Oscilloscope Undulating Wave Ribbon Loop
        wave_pts = []
        for p in range(16):
            rad = math.radians(p * 22.5)
            w_disp = math.sin(p * 2 + self.wave_phase) * (lvl * 5.5 + 1.2)
            r_osc = 18 + w_disp
            wave_pts.append(cx + r_osc * math.cos(rad))
            wave_pts.append(cy + r_osc * math.sin(rad))
        # Close loop
        wave_pts.extend(wave_pts[:2])
        self.canvas.create_line(wave_pts, fill=bright, width=1.0, smooth=True)

        # 7. Arc Reactor Core Sphere
        # If CPU overload (>=85%), core turns glowing Warning Red!
        r_core = 11 + (lvl * 4.0 if self.state in ("speaking", "listening") else math.sin(self.wave_phase) * 1.0)
        core_fill = "#FF1E40" if (cpu_val >= 85.0 and (int(self.angle_ticks) % 12 > 6)) else accent
        core_outline = "#FFFFFF" if cpu_val >= 85.0 else bright
        self.canvas.create_oval(
            cx - r_core, cy - r_core, cx + r_core, cy + r_core,
            fill=core_fill, outline=core_outline, width=1.5,
        )

        # Micro fusion center dot
        self.canvas.create_oval(
            cx - 3.5, cy - 3.5, cx + 3.5, cy + 3.5,
            fill="#FFFFFF", outline="",
        )

        # Update telemetry badges & notifications if expanded
        if self.is_expanded:
            self._update_telemetry_badges(cpu_val, ram_val)
            self._update_bar_theme(pal)
            self._render_notification_state()

        self.root.after(30, self._render_frame)

    def _update_telemetry_badges(self, cpu: float, ram: float):
        # CPU Badge color coding
        cpu_color = "#FF4A5E" if cpu >= 85 else ("#FFB300" if cpu >= 70 else PALETTES["en"]["accent"])
        self.cpu_badge.config(text=f"CPU {int(cpu)}%", fg=cpu_color)

        # RAM Badge color coding
        ram_color = "#FF4A5E" if ram >= 85 else ("#FFB300" if ram >= 70 else PALETTES["en"]["bright"])
        self.ram_badge.config(text=f"RAM {int(ram)}%", fg=ram_color)

        # Disk Badge
        self.disk_badge.config(text=f"DSK {int(self.disk_pct)}%")

    def _update_bar_theme(self, pal):
        accent = pal["accent"]
        lang_str = "BN (বাংলা)" if self.lang == "bn" else "EN"
        state_str = self.state.upper()
        self.status_lbl.config(
            text=f"R.O.N. · {state_str} · LANG: {lang_str}",
            fg=accent,
        )
        self.input_border.config(bg=accent)
        self.cmd_entry.config(
            bg=pal["glass"],
            fg=pal["bright"],
            insertbackground=pal["bright"],
        )
        for b in self.qa_buttons:
            b.config(fg=pal["bright"])

    # ── Event Bus Listener ──────────────────────────────────────────────────

    def _bus_listener(self):
        q = bus.subscribe()
        while self._running:
            try:
                msg = q.get(timeout=1.0)
                kind = msg.get("kind")
                if kind == "level":
                    self.target_level = float(msg.get("level", 0.0))
                elif kind == "state":
                    self.state = msg.get("state", "idle")
                    self.detail = msg.get("detail", "")
                elif kind == "language":
                    self.lang = msg.get("language", "en")
                    self.tray.refresh_icon()
                elif kind == "transcript":
                    entry = msg.get("entry", {})
                    speaker = entry.get("speaker", "")
                    text = entry.get("text", "")
                    if text:
                        prefix = "You: " if speaker == "user" else "Ron: "
                        self.caption = prefix + text[:80]
                        self.root.after(0, lambda: self.caption_lbl.config(text=self.caption))
                elif kind == "activity":
                    text = msg.get("text", "")
                    status = msg.get("status", "info")
                    if text and status in ("ok", "fail", "alert"):
                        level = "alert" if status == "fail" else "ok"
                        self.show_notification("RON EVENT", text, level=level, duration=3.5)
                elif kind == "reminder":
                    text = msg.get("text", "Reminder alert")
                    self.show_notification("REMINDER ALERT", text, level="alert", duration=6.0)
                elif kind == "telegram":
                    if msg.get("connected"):
                        self.show_notification("TELEGRAM UPLINK", "Mobile pocket uplink active", level="ok", duration=3.0)
                elif kind == "snapshot":
                    self.lang = msg.get("language", "en")
                    self.state = msg.get("state", "idle")
                    self.tray.refresh_icon()
            except Exception:
                pass

    # ── Windows Global Hotkey Listener (Ctrl + Shift + Space) ───────────────

    def _hotkey_listener(self):
        if not user32.RegisterHotKey(None, HOTKEY_ID, MODIFIERS, VK_KEY):
            print("[Mini-HUD] Warning: Global hotkey Ctrl+Shift+Space could not be registered.")
            return

        try:
            msg = wintypes.MSG()
            while self._running:
                if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE = 1
                    if msg.message == win32con.WM_HOTKEY and msg.wParam == HOTKEY_ID:
                        # Hotkey triggered: summon and expand command bar
                        self.root.after(0, self._on_hotkey_triggered)
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                time.sleep(0.04)
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)

    def _on_hotkey_triggered(self):
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self.expand_command_bar()

    def shutdown(self):
        """Clean shutdown."""
        self._running = False
        self.tray.stop()
        try:
            self.root.destroy()
        except Exception:
            pass


def main():
    hud = FloatingMiniHUD()
    try:
        hud.root.mainloop()
    except KeyboardInterrupt:
        hud.shutdown()


if __name__ == "__main__":
    main()
