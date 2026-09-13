"""Speech in (Google STT) and speech out (Windows SAPI).

Both paths publish to `bus` so the HUD can mirror what the microphone and the
speaker are actually doing. With no UI attached every bus call is a no-op, so the
plain `python main.py` console flow is unchanged.
"""

import array
import asyncio
import io
import math
import os
import re
import sys
import threading
import time

import edge_tts
import pygame
import speech_recognition as sr
import win32com.client

import bus

# Neural Voice Configuration (Edge-TTS)
DEFAULT_VOICE = os.environ.get("RON_VOICE", "en-GB-RyanNeural")  # Sophisticated British JARVIS voice
BANGLA_VOICE = os.environ.get("RON_VOICE_BN", "bn-BD-PradeepNeural")  # Natural Bengali male voice
USE_NEURAL_TTS = os.environ.get("RON_NEURAL_TTS", "1") != "0"

_current_lang = "en"


def set_language(lang: str):
    """Set language mode: 'en' for English or 'bn' for Bangla."""
    global _current_lang
    _current_lang = "bn" if str(lang).lower().startswith("b") else "en"


def get_language() -> str:
    return _current_lang

_pygame_initialized = False
_pygame_lock = threading.Lock()


def _init_pygame():
    global _pygame_initialized
    with _pygame_lock:
        if not _pygame_initialized:
            try:
                pygame.mixer.init()
                _pygame_initialized = True
            except Exception as e:
                print(f"[Pygame mixer init error: {e}]")


def _play_audio_bytes(data: bytes, stop_event: threading.Event = None) -> bool:
    """Play in-memory audio bytes via pygame mixer."""
    try:
        _init_pygame()
        bio = io.BytesIO(data)
        pygame.mixer.music.load(bio)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            if stop_event and stop_event.is_set():
                pygame.mixer.music.stop()
                break
            time.sleep(0.04)
        return True
    except Exception as e:
        print(f"[Audio playback error: {e}]")
        return False


def _speak_neural(text: str) -> bool:
    """Synthesize and speak text using high-fidelity Neural Edge-TTS."""
    if not USE_NEURAL_TTS:
        return False
    try:
        # Detect if text contains Bengali characters and switch voice dynamically
        is_bangla = bool(re.search(r"[\u0980-\u09FF]", text))
        voice = BANGLA_VOICE if is_bangla else DEFAULT_VOICE

        async def _synth():
            comm = edge_tts.Communicate(text, voice)
            buf = bytearray()
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    buf.extend(chunk["data"])
            return bytes(buf)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                audio_bytes = ex.submit(asyncio.run, _synth()).result(timeout=15)
        else:
            audio_bytes = asyncio.run(_synth())

        if not audio_bytes:
            return False

        return _play_audio_bytes(audio_bytes)
    except Exception as e:
        print(f"[Neural TTS fallback to SAPI: {e}]")
        return False


speaker = win32com.client.Dispatch("SAPI.SpVoice")

# SAPI objects are apartment-threaded: the COM pointer created on the import
# thread cannot be used from the HUD's request threads. Each thread therefore
# gets its own voice, created after CoInitialize on that thread.
_tls = threading.local()


def _voice():
    v = getattr(_tls, "voice", None)
    if v is not None:
        return v
    if threading.current_thread() is threading.main_thread():
        v = speaker
    else:
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
        v = win32com.client.Dispatch("SAPI.SpVoice")
    _tls.voice = v
    return v


# ---------------------------------------------------------------- amplitude ---
# Peak 16-bit sample value. RMS is divided by a fraction of this rather than the
# full range because ordinary speech sits far below clipping; dividing by 32768
# would leave the waveform almost flat.
_RMS_FULL_SCALE = 3200.0
_LEVEL_INTERVAL = 0.05  # seconds; ~20 updates/sec is plenty for a 60fps HUD


def _rms(chunk: bytes) -> float:
    """Root-mean-square of a signed 16-bit PCM buffer, as a 0..1 fraction.

    Python 3.13 removed `audioop` (PEP 594), so this is computed by hand. At one
    pass over ~1k samples per chunk the cost is irrelevant next to the network
    round-trip that follows.
    """
    usable = len(chunk) - (len(chunk) % 2)
    if usable <= 0:
        return 0.0
    samples = array.array('h')
    samples.frombytes(chunk[:usable])
    if not samples:
        return 0.0
    total = 0
    for s in samples:
        total += s * s
    rms = math.sqrt(total / len(samples))
    return min(1.0, rms / _RMS_FULL_SCALE)


class _LevelTap:
    """Transparent proxy over the mic stream that reports real input level.

    SpeechRecognition has no hook for live audio, but `Recognizer.listen` pulls
    every chunk through `source.stream.read`. Wrapping that one method gives the
    HUD genuine microphone amplitude instead of a decorative animation, and the
    bytes are handed back untouched so recognition is unaffected.
    """

    def __init__(self, inner):
        self._inner = inner
        self._last = 0.0

    def read(self, size, *args, **kwargs):
        data = self._inner.read(size, *args, **kwargs)
        now = time.monotonic()
        if now - self._last >= _LEVEL_INTERVAL:
            self._last = now
            bus.level(_rms(data))
        return data

    def __getattr__(self, name):
        # close(), and anything else SpeechRecognition reaches for on teardown.
        return getattr(self._inner, name)


def _speech_envelope(stop: threading.Event):
    """Drive the waveform while SAPI talks.

    SAPI exposes no output-amplitude API, so unlike the microphone path this
    envelope is synthesised: a syllable-rate wobble with pauses, which reads as
    speech on the visualiser. It is presentation only -- nothing depends on it.
    """
    t = 0.0
    while not stop.wait(0.05):
        t += 0.05
        syllable = 0.5 + 0.5 * math.sin(t * 11.0)
        phrase = 0.55 + 0.45 * math.sin(t * 1.7 + 0.6)
        bus.level(max(0.05, min(1.0, syllable * phrase * 0.9)))
    bus.level(0.0)


# -------------------------------------------------------------------- speak ---

_speak_lock = threading.Lock()


def speak(text: str):
    with _speak_lock:
        text = str(text)
        try:
            print(f"Ron: {text}")
        except UnicodeEncodeError:
            safe_enc = getattr(sys.stdout, "encoding", "utf-8") or "utf-8"
            print(f"Ron: {text}".encode(safe_enc, errors="replace").decode(safe_enc, errors="replace"))
        bus.transcript("ron", text)
        previous = bus.get_state()
        bus.set_state(bus.SPEAKING, text[:160])

        stop = threading.Event()
        envelope = threading.Thread(target=_speech_envelope, args=(stop,), daemon=True)
        envelope.start()
        try:
            # High-fidelity Neural Edge-TTS with dynamic language selection (JARVIS British / Bengali)
            # Automatically falls back to Windows SAPI if offline or unavailable
            success = _speak_neural(text)
            if not success:
                _voice().Speak(text)
        except Exception as e:
            print(f"[TTS Error: {e}]")
            bus.activity(f"Speech output failed: {e}", "fail")
            bus.meta(audio_ok=False)
        finally:
            stop.set()
            envelope.join(timeout=0.5)
            bus.level(0.0)
            # Returning to LISTENING would be a lie -- the mic loop announces that
            # itself on its next pass -- so speaking settles back to idle. A fault is
            # the one thing that must survive its own spoken explanation, otherwise
            # the HUD goes green again while the error is still being read out.
            bus.set_state(bus.ERROR if previous == bus.ERROR else bus.IDLE)


# Set this to the microphone index you want to use (run test_mic.py to see list)
MIC_INDEX = 1  # WO Mic Device


def mic_name(index=None):
    """Human-readable name of the configured input device, for the HUD."""
    idx = MIC_INDEX if index is None else index
    try:
        names = sr.Microphone.list_microphone_names()
        if 0 <= idx < len(names):
            return names[idx] or f"DEVICE {idx}"
    except Exception:
        pass
    return f"DEVICE {idx}"


def _open_microphone():
    """Open MIC_INDEX, falling back to the system default if it is gone."""
    try:
        return sr.Microphone(device_index=MIC_INDEX)
    except Exception as e:
        print(f"[Mic index {MIC_INDEX} unavailable ({e}); falling back to the "
              f"default device. Run 'python test_mic.py' to list indices.]")
        bus.meta(mic_ok=False, mic_device="DEFAULT (FALLBACK)")
        return sr.Microphone()


def listen(timeout=5, phrase_limit=10) -> str:
    r = sr.Recognizer()
    r.pause_threshold = 0.8
    r.dynamic_energy_threshold = False

    try:
        with _open_microphone() as source:
            # adjust_for_ambient_noise() *overwrites* energy_threshold, so setting
            # it before this call has no effect at all. Clamp afterwards instead:
            # a noisy calibration sample can leave the bar so high that ordinary
            # speech never trips it, which looks exactly like a dead microphone.
            r.adjust_for_ambient_noise(source, duration=0.5)
            r.energy_threshold = max(50.0, min(r.energy_threshold, 1500.0))
            print(f"Listening... (threshold: {r.energy_threshold:.1f})")
            bus.meta(mic_ok=True, threshold=round(r.energy_threshold, 1))
            bus.set_state(bus.LISTENING, "AWAITING VOICE INPUT")

            # Tap installed after calibration so the HUD is not driven by the
            # ambient-noise sample.
            source.stream = _LevelTap(source.stream)

            audio = r.listen(source, timeout=timeout, phrase_time_limit=phrase_limit)
            print("Processing...")
            bus.level(0.0)
            bus.set_state(bus.THINKING, "TRANSCRIBING AUDIO")

            stt_lang = "bn-BD" if _current_lang == "bn" else "en-US"
            try:
                text = r.recognize_google(audio, language=stt_lang)
            except sr.UnknownValueError:
                alt_lang = "en-US" if stt_lang == "bn-BD" else "bn-BD"
                text = r.recognize_google(audio, language=alt_lang)

            try:
                print(f"You: {text}")
            except UnicodeEncodeError:
                safe_enc = getattr(sys.stdout, "encoding", "utf-8") or "utf-8"
                print(f"You: {text}".encode(safe_enc, errors="replace").decode(safe_enc, errors="replace"))
            return text.lower().strip()

    except sr.WaitTimeoutError:
        print("[Timeout - no speech detected]")
        bus.set_state(bus.IDLE)
        return ""
    except sr.UnknownValueError:
        print("[Could not understand]")
        bus.activity("Audio not recognised", "info")
        bus.set_state(bus.IDLE)
        return ""
    except sr.RequestError as e:
        print(f"[API error: {e}]")
        bus.activity(f"Speech service unavailable: {e}", "fail")
        bus.set_state(bus.ERROR, "SPEECH SERVICE UNAVAILABLE")
        speak("Speech service is unavailable.")
        return ""
    except Exception as e:
        print(f"[Mic error: {e}]")
        bus.meta(mic_ok=False)
        bus.activity(f"Microphone error: {e}", "fail")
        bus.set_state(bus.ERROR, "MICROPHONE ERROR")
        return ""
    finally:
        bus.level(0.0)
