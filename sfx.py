"""Cinematic Stark Sci-Fi Audio FX Engine for RON.

Delivers high-tech acoustic and mechanical sound design:
- Holographic Hum: Resonant low-frequency pulse when opening the HUD.
- Iron Man Suit Servo: Mechanical pitch sweep when engaging Work/Gaming protocols.
- Sci-Fi Sonar Ping: High-Q radar chime when NetRadar detects active or rogue devices.
- Protocol Lockdown: Heavy dual-tone tactical security clamp for Protocol Zero.
- Interface Click: Micro-chirp for instant command acknowledgement.

Hardware footprint:
- 0% GPU requirement.
- Procedural audio synthesis via Python standard library (math, struct, wave, io).
- Non-blocking playback via pygame.mixer.Sound (independent of TTS pygame.mixer.music).
"""

import io
import math
import struct
import threading
import time
import wave
from typing import Dict, Optional

try:
    import pygame
except ImportError:
    pygame = None

# Global state
_mixer_initialized = False
_mixer_lock = threading.Lock()
_sound_cache: Dict[str, "pygame.mixer.Sound"] = {}
_cache_lock = threading.Lock()
_master_volume = 0.7
_is_muted = False
_last_played_times: Dict[str, float] = {}


# ---------------------------------------------------------------------------
# Mixer Initialization
# ---------------------------------------------------------------------------

def init_mixer() -> bool:
    """Safely initialize pygame.mixer with sufficient dedicated channels."""
    global _mixer_initialized
    if pygame is None:
        return False
    with _mixer_lock:
        if _mixer_initialized and pygame.mixer.get_init():
            return True
        try:
            if not pygame.mixer.get_init():
                # 44.1kHz, 16-bit signed, 2 channels (stereo), 1024 buffer for low latency
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
            # Ensure at least 16 mixing channels for concurrent sound effects
            pygame.mixer.set_num_channels(16)
            _mixer_initialized = True
            return True
        except Exception as e:
            print(f"[sfx] Warning: Failed to initialize audio mixer: {e}")
            return False


# ---------------------------------------------------------------------------
# Procedural Audio Generators (100% CPU, In-Memory WAV Synthesis)
# ---------------------------------------------------------------------------

def _write_wav_mono(samples: list, sample_rate: int = 44100) -> bytes:
    """Helper to convert a list of 16-bit signed integer samples to WAV bytes."""
    buffer = bytearray()
    for s in samples:
        clamped = max(-32767, min(32767, int(s)))
        buffer.extend(struct.pack("<h", clamped))

    bio = io.BytesIO()
    with wave.open(bio, "wb") as wav:
        wav.setnchannels(1)  # Mono
        wav.setsampwidth(2)  # 16-bit
        wav.setframerate(sample_rate)
        wav.writeframes(buffer)
    return bio.getvalue()


def generate_hud_hum_bytes() -> bytes:
    """Synthesize a subtle, resonant holographic drone / boot hum.
    
    Sound Design:
    - 75 Hz sub-bass carrier with 8 Hz amplitude pulsation (AM modulation).
    - Second harmonic at 150 Hz and subtle shimmer at 450 Hz.
    - Smooth 0.25s exponential fade-in and gentle 0.45s decay over 1.25s total.
    """
    sr = 44100
    duration = 1.25
    total = int(sr * duration)
    samples = []

    f_base = 75.0
    f_am = 8.0

    for i in range(total):
        t = i / sr
        # Smooth envelope
        if t < 0.25:
            env = (t / 0.25) ** 1.5
        elif t > 0.8:
            env = max(0.0, 1.0 - ((t - 0.8) / 0.45) ** 1.2)
        else:
            env = 1.0

        # AM pulse
        amp_mod = 0.75 + 0.25 * math.sin(2.0 * math.pi * f_am * t)

        # Carrier wave + resonant harmonics
        w1 = math.sin(2.0 * math.pi * f_base * t)
        w2 = 0.35 * math.sin(2.0 * math.pi * (f_base * 2.0) * t)
        w3 = 0.12 * math.sin(2.0 * math.pi * (f_base * 6.0) * t)

        val = (w1 + w2 + w3) * amp_mod * env * 16000.0
        samples.append(val)

    return _write_wav_mono(samples, sr)


def generate_servo_bytes() -> bytes:
    """Synthesize an Iron Man suit mechanical servo / repulsor spin-up.
    
    Sound Design:
    - Exponential frequency sweep rising from 160 Hz to 860 Hz over 0.55s.
    - High-frequency metallic mechanical overtone (3.2x) with phase modulation.
    - Crisp tail finish simulating mechanical latching.
    """
    sr = 44100
    duration = 0.58
    total = int(sr * duration)
    samples = []

    f_start = 160.0
    f_end = 860.0

    phase = 0.0
    for i in range(total):
        t = i / sr
        progress = t / duration

        # Exponential pitch curve
        freq = f_start * ((f_end / f_start) ** (progress ** 1.3))
        phase += 2.0 * math.pi * freq / sr

        # Envelope: quick attack, sustained whine, sharp mechanical cutoff
        if progress < 0.08:
            env = progress / 0.08
        elif progress > 0.88:
            env = (1.0 - progress) / 0.12
        else:
            env = 1.0

        # Primary sweep + metallic servo buzz
        servo_buzz = 0.3 * math.sin(phase * 3.1415) * (1.0 - progress * 0.4)
        click_spike = 0.15 * math.sin(phase * 8.0) if progress > 0.82 else 0.0

        val = (math.sin(phase) + servo_buzz + click_spike) * env * 18000.0
        samples.append(val)

    return _write_wav_mono(samples, sr)


def generate_sonar_ping_bytes() -> bytes:
    """Synthesize a crystalline sci-fi radar sonar ping with reverberant echo.
    
    Sound Design:
    - Initial sharp impulse ping at 1450 Hz with fast exponential decay.
    - Harmonic shimmer at 2175 Hz (1.5x fifth).
    - Second delayed echo pulse at +0.22s at 1800 Hz simulating a radar return lock.
    """
    sr = 44100
    duration = 0.85
    total = int(sr * duration)
    samples = []

    f1 = 1450.0
    f2 = 1800.0
    delay_samples = int(sr * 0.22)

    for i in range(total):
        t = i / sr
        # Primary ping
        env1 = math.exp(-6.5 * t)
        val1 = (math.sin(2.0 * math.pi * f1 * t) +
                0.4 * math.sin(2.0 * math.pi * (f1 * 1.5) * t)) * env1

        # Echo return ping
        val2 = 0.0
        if i >= delay_samples:
            t_echo = (i - delay_samples) / sr
            env2 = math.exp(-8.0 * t_echo) * 0.45
            val2 = (math.sin(2.0 * math.pi * f2 * t_echo) +
                    0.25 * math.sin(2.0 * math.pi * (f2 * 1.33) * t_echo)) * env2

        val = (val1 + val2) * 20000.0
        samples.append(val)

    return _write_wav_mono(samples, sr)


def generate_lockdown_bytes() -> bytes:
    """Synthesize a tactical security clamp / alert for Protocol Zero.
    
    Sound Design:
    - Dual heavy tactical square-modulated pulse descending from 440 Hz to 180 Hz.
    - Low-pass dampened industrial impact transient.
    """
    sr = 44100
    duration = 0.65
    total = int(sr * duration)
    samples = []

    for i in range(total):
        t = i / sr
        progress = t / duration
        freq = 440.0 - (260.0 * (progress ** 0.8))
        env = math.exp(-4.5 * t)

        # Gritty industrial wave
        w1 = math.sin(2.0 * math.pi * freq * t)
        w2 = 0.45 * math.sin(2.0 * math.pi * (freq * 0.5) * t)
        sub = 0.3 * math.sin(2.0 * math.pi * 90.0 * t)

        val = (w1 + w2 + sub) * env * 21000.0
        samples.append(val)

    return _write_wav_mono(samples, sr)


def generate_click_bytes() -> bytes:
    """Synthesize a subtle, futuristic interface tap / click."""
    sr = 44100
    duration = 0.06
    total = int(sr * duration)
    samples = []

    for i in range(total):
        t = i / sr
        freq = 2400.0 - 1200.0 * (t / duration)
        env = math.exp(-60.0 * t)
        val = math.sin(2.0 * math.pi * freq * t) * env * 14000.0
        samples.append(val)

    return _write_wav_mono(samples, sr)


def generate_diagnostic_bytes() -> bytes:
    """Synthesize Stark Diagnostic Laser Sweep: Capacitor Whine + Suit Servo Chime.
    
    Sound Design:
    - 0.00s - 0.42s: High-energy electromagnetic capacitor charging whine,
      exponential frequency sweep rising from 220 Hz to 2800 Hz with harmonic sheen.
    - 0.38s - 0.92s: Iron Man suit mechanical servo spin-up and resonance latch
      descending from 950 Hz to 380 Hz with metallic phase harmonics.
    - 0.90s - 1.20s: Crisp dual-frequency digital calibration chime (1760 Hz + 2640 Hz)
      confirming all telemetry systems calibrated and locked.
    """
    sr = 44100
    duration = 1.20
    total = int(sr * duration)
    samples = []

    phase_cap = 0.0
    phase_servo = 0.0

    for i in range(total):
        t = i / sr
        val = 0.0

        # Phase 1: Capacitor whine charging up (0.0s - 0.45s)
        if t <= 0.45:
            p1 = t / 0.45
            # Exponential rising pitch curve (220 Hz -> 2800 Hz)
            freq_cap = 220.0 * ((2800.0 / 220.0) ** (p1 ** 1.5))
            phase_cap += 2.0 * math.pi * freq_cap / sr
            # Amplitude envelope: quick swell, then handoff to servo
            env1 = math.sin(p1 * math.pi) if p1 > 0.8 else min(1.0, p1 * 8.0)
            whine = (math.sin(phase_cap) + 
                     0.35 * math.sin(phase_cap * 2.0) + 
                     0.15 * math.sin(phase_cap * 3.0)) * env1
            val += whine * 14000.0

        # Phase 2: Iron Man Suit Servo Latch (0.35s - 0.95s)
        if 0.35 <= t <= 0.95:
            t_s = t - 0.35
            dur_s = 0.60
            p2 = t_s / dur_s
            freq_servo = 950.0 - (570.0 * (p2 ** 0.85))
            phase_servo += 2.0 * math.pi * freq_servo / sr
            # Envelope: smooth attack crossover, mechanical decay
            env2 = math.sin(p2 * math.pi)
            servo = (math.sin(phase_servo) + 
                     0.4 * math.sin(phase_servo * 2.5) +
                     0.2 * math.sin(phase_servo * 4.0)) * env2
            val += servo * 16000.0

        # Phase 3: Digital Calibration Lock Chime (0.88s - 1.20s)
        if t >= 0.88:
            t_c = t - 0.88
            env3 = math.exp(-12.0 * t_c)
            chime1 = math.sin(2.0 * math.pi * 1760.0 * t_c)
            chime2 = 0.5 * math.sin(2.0 * math.pi * 2640.0 * t_c)
            val += (chime1 + chime2) * env3 * 15000.0

        samples.append(val)

    return _write_wav_mono(samples, sr)


# ---------------------------------------------------------------------------
# Sound Cache & Playback Management
# ---------------------------------------------------------------------------

GENERATORS = {
    "hud_hum": generate_hud_hum_bytes,
    "servo": generate_servo_bytes,
    "sonar": generate_sonar_ping_bytes,
    "lockdown": generate_lockdown_bytes,
    "click": generate_click_bytes,
    "diagnostic": generate_diagnostic_bytes,
}


def _get_or_create_sound(sound_name: str) -> Optional["pygame.mixer.Sound"]:
    """Retrieve pre-synthesized Sound object or generate and cache it."""
    if not init_mixer():
        return None

    with _cache_lock:
        if sound_name in _sound_cache:
            return _sound_cache[sound_name]

        generator = GENERATORS.get(sound_name)
        if not generator:
            return None

        try:
            wav_bytes = generator()
            bio = io.BytesIO(wav_bytes)
            sound = pygame.mixer.Sound(bio)
            _sound_cache[sound_name] = sound
            return sound
        except Exception as e:
            print(f"[sfx] Error generating sound '{sound_name}': {e}")
            return None


def preload_all():
    """Pre-synthesize all sci-fi sound effects in background thread."""
    def _worker():
        for name in GENERATORS:
            _get_or_create_sound(name)
    threading.Thread(target=_worker, daemon=True).start()


def play(sound_name: str, volume: Optional[float] = None, debounce_s: float = 0.0) -> bool:
    """Play a procedural sci-fi sound effect asynchronously.
    
    Args:
        sound_name: 'hud_hum', 'servo', 'sonar', 'lockdown', 'click'
        volume: Optional volume override (0.0 to 1.0). Defaults to _master_volume.
        debounce_s: If > 0, ignore triggers if this sound was played within debounce_s.
    
    Returns:
        True if sound successfully dispatched to a pygame channel.
    """
    global _last_played_times

    if _is_muted:
        return False

    now = time.monotonic()
    if debounce_s > 0:
        last = _last_played_times.get(sound_name, 0.0)
        if now - last < debounce_s:
            return False
        _last_played_times[sound_name] = now

    try:
        sound = _get_or_create_sound(sound_name)
        if not sound:
            return False
        vol = _master_volume if volume is None else max(0.0, min(1.0, float(volume)))
        sound.set_volume(vol)
        # Find an available channel and play immediately (native non-blocking)
        channel = pygame.mixer.find_channel()
        if channel:
            channel.play(sound)
        else:
            sound.play()
        return True
    except Exception as e:
        print(f"[sfx] Playback failure for '{sound_name}': {e}")
        return False


def set_master_volume(volume: float):
    """Set global SFX master volume (0.0 to 1.0)."""
    global _master_volume
    _master_volume = max(0.0, min(1.0, float(volume)))


def set_muted(muted: bool):
    """Mute or unmute all SFX audio."""
    global _is_muted
    _is_muted = bool(muted)
