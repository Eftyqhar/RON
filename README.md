<div align="center">

# 🤖 Ron — Personal AI Voice Assistant

**A JARVIS-inspired voice assistant for Windows that listens, thinks, and acts.**

[![Python](https://img.shields.io/badge/Python-3.8+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?style=for-the-badge&logo=windows&logoColor=white)](https://www.microsoft.com/windows)
[![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Active-success?style=for-the-badge)]()

*Built by [Ifteqhar](https://github.com/ifteqhar) — Your always-on desktop companion.*

</div>

---

## 📖 Overview

**Ron** is a fully voice-controlled, JARVIS-style personal AI assistant that runs on your Windows desktop. Speak naturally — Ron listens, understands your intent, and either responds conversationally **or** executes a tool action (opening apps, visiting websites, playing YouTube videos, generating PDFs) without you touching the keyboard.

It combines real-time speech recognition (Google Speech-to-Text), offline text-to-speech (Windows SAPI), and a function-calling LLM (`Qwen3.5-397B-A17B`) routed through an OpenAI-compatible endpoint.

> *"Ron is online. How can I help you, Sir?"*

---

## ✨ Features

| Feature | Description |
|---|---|
| 🎙️ **Voice Input** | Hands-free speech recognition via microphone |
| 🗣️ **Voice Output** | Natural offline TTS using Windows SAPI |
| 🧠 **LLM Brain** | `Qwen3.5-397B-A17B` via OpenAI-compatible API |
| 🛠️ **Function Calling** | AI emits JSON tool calls; assistant executes them |
| 🎵 **Play YouTube** | *"Play Believer by Imagine Dragons"* → instant playback |
| 🌐 **Open Websites** | Smart URL resolution via DuckDuckGo + known-site dictionary |
| 📁 **Open Folders/Drives** | *"Open D drive"* or *"Open projects folder"* |
| 🖥️ **Launch Apps** | Notepad, Chrome, VS Code, Spotify, WhatsApp, and more |
| 📄 **Generate PDFs** | *"Create a PDF named notes with ..."* → saved to Documents |
| 🔁 **Continuous Listening** | Always-on loop with smart shutdown phrases |
| 🧩 **Modular Design** | Cleanly split into `main`, `voice`, and `tools` modules |

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    User (Voice)                          │
└────────────────────────┬────────────────────────────────┘
                         ▼
              ┌──────────────────────┐
              │   voice.py (STT)     │  ◄── Microphone
              │   Google Speech API  │
              └──────────┬───────────┘
                         ▼
              ┌──────────────────────┐
              │     main.py          │
              │  Intent Dispatcher   │
              └──┬──────────┬────────┘
                 │          │
        (Direct) │          │ (Conversational)
                 ▼          ▼
   ┌──────────────────┐   ┌──────────────────────┐
   │   tools.py       │   │  LLM (Qwen3.5 via    │
   │ • play_youtube   │   │   hcnsec.cn API)     │
   │ • open_website   │   │  Returns JSON tool   │
   │ • open_app       │   │  call OR text reply  │
   │ • generate_pdf   │   └──────────┬───────────┘
   └────────┬─────────┘              │
            ▼                        ▼
       ┌────────────────────────────────────┐
       │     voice.py (TTS — Windows SAPI)   │ ──► Speaker
       └────────────────────────────────────┘
```

---

## 🚀 Quick Start

### Prerequisites

- **OS:** Windows 10 / 11
- **Python:** 3.8 or higher
- **Microphone:** Any working input device (built-in, USB, or virtual like *WO Mic*)
- **API Key:** An OpenAI-compatible key from [hcnsec.cn](https://hcnsec.cn) (or swap in your own endpoint)

### 1. Clone the Repository

```bash
git clone https://github.com/ifteqhar/ron-ai-assistant.git
cd ron-ai-assistant
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

> **Note (Windows):** If `pyaudio` fails to install, grab the precompiled wheel matching your Python version from [PyAudio wheels](https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio), then `pip install <wheel-file>`.

### 3. Configure Your Microphone

Run the mic test and pick the correct device index:

```bash
python test_mic.py
```

Update `MIC_INDEX` in `voice.py` to match the working device.

### 4. Set Your API Key

Open `main.py` and replace the placeholder:

```python
client = OpenAI(
    api_key="YOUR_API_KEY",                 # ← paste your key here
    base_url="https://api.hcnsec.cn/v1"
)
```

### 5. Launch Ron

**Option A — Direct:**
```bash
python main.py
```

**Option B — Batch file:**
Double-click `run_ron.bat`

**Option C — Voice activation on boot:**
Place a shortcut to `run_ron.bat` in `shell:startup`.

---

## 🎤 Voice Commands

Ron understands natural language. Here are examples:

### 🌐 Open Websites
| Say This | What Happens |
|---|---|
| *"Visit Facebook"* | Opens facebook.com |
| *"Go to GitHub"* | Opens github.com |
| *"Open reddit"* | DuckDuckGo search → top result |
| *"Take me to ifteqhar.dev"* | Smart URL resolution |

### 🖥️ Launch Apps
| Say This | What Happens |
|---|---|
| *"Open Chrome"* | Launches Chrome |
| *"Open VS Code"* | Launches VS Code |
| *"Open Spotify"* | Launches Spotify (MS Store) |
| *"Open Notepad"* | Launches Notepad |

### 🎵 Media
| Say This | What Happens |
|---|---|
| *"Play Lo-fi beats"* | Plays matching YouTube video |
| *"Play Despacito"* | Plays music on YouTube |

### 📁 Files & Folders
| Say This | What Happens |
|---|---|
| *"Open D drive"* | Opens File Explorer at D:\ |
| *"Open projects folder"* | Opens projects folder |
| *"Create a PDF named notes with Hello world"* | Saves PDF to Documents |

### ⚙️ System
| Say This | What Happens |
|---|---|
| *"Goodbye"* / *"Shut down"* / *"Sleep"* | Exits gracefully |

---

## 📁 Project Structure

```
ron-ai-assistant/
├── main.py              # 🧠 Entry point — intent dispatcher & LLM loop
├── voice.py             # 🎙️ STT (Google) + TTS (Windows SAPI)
├── tools.py             # 🛠️ Tool implementations (apps, web, PDF, YouTube)
├── requirements.txt     # 📦 Python dependencies
├── run_ron.bat          # 🪟 Windows launcher script
├── test_mic.py          # 🎤 Microphone discovery & test utility
├── test_api.py          # 🔌 API connectivity tester
├── test_anthropic.py    # 🔌 Anthropic models compatibility test
├── test_endpoint.py     # 🔌 Endpoint variant test
├── test_raw.py          # 🔌 Raw HTTP test
└── README.md            # 📖 You are here
```

---

## ⚙️ Configuration

### Customizing the Model

Edit `main.py`:

```python
MODEL = "Qwen3.5-397B-A17B"   # Default
# MODEL = "DeepSeek-V4-Flash" # Alternative (test with test_api.py)
```

### Customizing the System Prompt

Edit `SYSTEM_PROMPT` in `main.py` to change Ron's personality, add new tools, or modify behavior. Ron is currently configured to:
- Refer to itself as **Ron**
- Address the user as **Sir** or **Ifteqhar**
- Emit **JSON-only** responses when a tool action is required
- Stay concise and never be verbose

### Adding New Tools

1. Implement the function in `tools.py`
2. Add its JSON schema to `SYSTEM_PROMPT` in `main.py`
3. Add a dispatch branch in `process_command()` in `main.py`

### Adding Known Websites

Edit the `KNOWN_SITES` dictionary in `main.py`:

```python
KNOWN_SITES = {
    "your_site": "https://yoursite.com",
    ...
}
```

---

## 🔌 Tested Models

| Model | Status | Notes |
|---|---|---|
| `Qwen3.5-397B-A17B` | ✅ Default | Best balance of speed and quality |
| `DeepSeek-V4-Flash` | ✅ Works | Tested via `test_api.py` |
| `claude-3-5-sonnet` | ⚠️ Endpoint-dependent | Tested via `test_anthropic.py` |

Run any test script to verify connectivity with your API key:

```bash
python test_api.py
```

---

## 🛠️ Tech Stack

- **[OpenAI Python SDK](https://github.com/openai/openai-python)** — LLM client (compatible mode)
- **[SpeechRecognition](https://github.com/Uberi/speech_recognition)** — STT via Google
- **[PyAudio](https://people.csail.mit.edu/hubert/pyaudio/)** — Microphone stream
- **[pywin32 (SAPI)](https://github.com/mhammond/pywin32)** — Native Windows TTS
- **[pywhatkit](https://github.com/Ankit404butfound/PyWhatKit)** — YouTube playback
- **[fpdf2](https://github.com/py-pdf/fpdf2)** — PDF generation
- **[duckduckgo_search](https://github.com/deedy5/duckduckgo_search)** — Fallback URL resolution

---

## 🐛 Troubleshooting

<details>
<summary><b>🎤 Microphone not picking up audio</b></summary>

- Run `python test_mic.py` to enumerate devices.
- Adjust `MIC_INDEX` in `voice.py`.
- Increase `MIC_INDEX` if you're using a virtual mic like WO Mic.
- Check Windows microphone privacy settings (Settings → Privacy → Microphone).

</details>

<details>
<summary><b>🔑 API errors / quota issues</b></summary>

- Verify your key at the [hcnsec.cn dashboard](https://hcnsec.cn).
- Run `python test_api.py` to confirm connectivity and quota.
- Ron will speak in Chinese if it detects quota errors: *"我的月度token配额已不足。"*

</details>

<details>
<summary><b>🔇 No audio output</b></summary>

- Confirm Windows SAPI voices are installed (`Settings → Time & Language → Language → Speech`).
- The fallback in `voice.py` prints text to console if TTS fails.

</details>

<details>
<summary><b>🌐 Website won't open</b></summary>

- Add the site to `KNOWN_SITES` in `main.py` for instant resolution.
- Otherwise Ron falls back to a DuckDuckGo search.

</details>

---

## 🗺️ Roadmap

- [ ] Wake-word detection (*"Hey Ron"*) — eliminate the listening loop
- [ ] Cross-platform support (Linux/macOS TTS via `pyttsx3`)
- [ ] Long-term memory with vector store
- [ ] Plugin system for custom user tools
- [ ] GUI overlay with live waveform visualization
- [ ] Calendar, email, and reminder integrations

---

## 🤝 Contributing

Contributions, issues, and feature requests are welcome!

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## 📜 License

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for more information.

---

## 👤 Author

**Ifteqhar**

- GitHub: [@ifteqhar](https://github.com/ifteqhar)
- Project: [ron-ai-assistant](https://github.com/ifteqhar/ron-ai-assistant)

---

## 🙏 Acknowledgments

- Inspired by **J.A.R.V.I.S.** from the *Iron Man* universe
- Powered by [Qwen3.5](https://qwenlm.github.io/) and the OpenAI-compatible API ecosystem
- Speech recognition by [Google Cloud Speech](https://cloud.google.com/speech-to-text)

---

<div align="center">

**If Ron helps you, drop a ⭐ on the repo — it means a lot.**

*Built with ❤️ in Bangladesh 🇧🇩*

</div>
