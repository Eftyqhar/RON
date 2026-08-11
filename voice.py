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

def listen(timeout=5, phrase_limit=10) -> str:
    r = sr.Recognizer()
    r.energy_threshold = 50
    r.pause_threshold = 0.8
    r.dynamic_energy_threshold = False

    try:
        with sr.Microphone(device_index=MIC_INDEX) as source:
            r.adjust_for_ambient_noise(source, duration=0.3)
            print(f"Listening... (threshold: {r.energy_threshold:.1f})")

            audio = r.listen(source, timeout=timeout, phrase_time_limit=phrase_limit)
            print("Processing...")

            text = r.recognize_google(audio)
            print(f"You: {text}")
            return text.lower().strip()

    except sr.WaitTimeoutError:
        print("[Timeout]")
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
