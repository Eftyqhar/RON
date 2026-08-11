import speech_recognition as sr

print("=== Microphone Test ===\n")
print("Available microphones:")
for i, name in enumerate(sr.Microphone.list_microphone_names()):
    print(f"  [{i}] {name}")

# ← Change this index to test different mics
MIC_INDEX = 2  # WO Mic Device

print(f"\nTesting mic index [{MIC_INDEX}]: {sr.Microphone.list_microphone_names()[MIC_INDEX]}")
print("Speak something now!\n")

r = sr.Recognizer()
r.energy_threshold = 50
r.dynamic_energy_threshold = False

with sr.Microphone(device_index=MIC_INDEX) as source:
    r.adjust_for_ambient_noise(source, duration=0.3)
    print(f"Energy threshold after adjustment: {r.energy_threshold:.1f}")
    print("Listening for 5 seconds...")

    try:
        audio = r.listen(source, timeout=5, phrase_time_limit=10)
        print("Got audio! Recognizing...")
        text = r.recognize_google(audio)
        print(f"\n✓ SUCCESS — You said: {text}")
        print(f"\nUse MIC_INDEX = {MIC_INDEX} in voice.py")
    except sr.WaitTimeoutError:
        print("✗ No speech detected. Try a different MIC_INDEX.")
    except sr.UnknownValueError:
        print("✗ Could not understand. Try speaking louder.")
    except Exception as e:
        print(f"✗ Error: {e}")
