import speech_recognition as sr
import win32com.client

speaker = win32com.client.Dispatch("SAPI.SpVoice")

def speak(text: str):
    print(f"Ron: {text}")
    try:
        speaker.Speak(text)
    except Exception as e:
        print(f"[TTS Error: {e}]")

# Set this to the microphone index you want to use (run test_mic.py to see list)
MIC_INDEX = 1  # WO Mic Device


def _open_microphone():
    """Open MIC_INDEX, falling back to the system default if it is gone."""
    try:
        return sr.Microphone(device_index=MIC_INDEX)
    except Exception as e:
        print(f"[Mic index {MIC_INDEX} unavailable ({e}); falling back to the "
              f"default device. Run 'python test_mic.py' to list indices.]")
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

            audio = r.listen(source, timeout=timeout, phrase_time_limit=phrase_limit)
            print("Processing...")

            text = r.recognize_google(audio)
            print(f"You: {text}")
            return text.lower().strip()

    except sr.WaitTimeoutError:
        print("[Timeout - no speech detected]")
        return ""
    except sr.UnknownValueError:
        print("[Could not understand]")
        return ""
    except sr.RequestError as e:
        print(f"[API error: {e}]")
        speak("Speech service is unavailable.")
        return ""
    except Exception as e:
        print(f"[Mic error: {e}]")
        return ""
