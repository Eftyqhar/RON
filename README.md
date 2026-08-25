<div align="center">

# 🤖 Ron — Personal AI Voice Assistant

**A JARVIS-inspired voice assistant for Windows that listens, thinks, and acts.**

[![Python](https://img.shields.io/badge/Python-3.8+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?style=for-the-badge&logo=windows&logoColor=white)](https://www.microsoft.com/windows)
[![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Active-success?style=for-the-badge)]()

*Built by [Ifteqhar]([https://github.com/ifteqhar](https://github.com/Eftyqhar)) — Your always-on desktop companion.*

</div>

---

## 📖 Overview

**Ron** is a fully voice-controlled, JARVIS-style personal AI assistant that runs on your Windows desktop. Speak naturally — Ron listens, understands your intent, and either responds conversationally **or** executes a tool action (opening apps, visiting websites, playing YouTube videos, generating PDFs, reading you the weather, telling you the time and the date) without you touching the keyboard.

It combines real-time speech recognition (Google Speech-to-Text), offline text-to-speech (Windows SAPI), and a function-calling LLM (`kat-coder-pro-v2.5`) routed through an OpenAI-compatible endpoint.

> *"Ron is online. How can I help you, Sir?"*

---

## ✨ Features

| Feature | Description |
|---|---|
| 🎙️ **Voice Input** | Hands-free speech recognition via microphone |
| 🗣️ **Voice Output** | Natural offline TTS using Windows SAPI |
| 🧠 **LLM Brain** | `kat-coder-pro-v2.5` via OpenAI-compatible API |
| 🛠️ **Function Calling** | AI emits JSON tool calls; assistant executes them |
| 🎵 **Play YouTube** | *"Play Believer by Imagine Dragons"* → instant playback |
| 🌐 **Open Websites** | Smart URL resolution via DuckDuckGo + known-site dictionary |
| 📁 **Open Folders/Drives** | *"Open D drive"* or *"Open projects folder"* |
| 🖥️ **Launch Apps** | Notepad, Chrome, VS Code, Spotify, WhatsApp, and more |
| 📄 **Generate PDFs** | *"Create a PDF about black holes"* → a formatted 3–5 page report in Documents |
| 🌦️ **Live Weather** | Spoken conditions and forecast, plus an always-on HUD panel — **no API key needed** |
| 📡 **Internet Speed** | *"Check my internet speed"* → ping, download and upload measured live, with a cyberpunk HUD overlay — **no API key needed** |
| ⏱️ **Timer** | *"Set a timer for 2 minutes"* → countdown running on the HUD with a live clock and progress bar — **no API key needed** |
| 🔊 **Volume Control** | *"Set volume to 50"* / *"mute"* / *"volume up 10"* → system master volume changed via Windows Core Audio — **no API key needed** |
| 🕰️ **Time & Three Calendars** | Bangladesh Standard Time, with the date in English, Bangla and Hijri |
| 🔁 **Continuous Listening** | Always-on loop with smart shutdown phrases |
| 💾 **Persistent Memory** | Every exchange saved to a local SQLite file; recent turns recalled on startup |
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
   │   tools.py       │   │  LLM (kat-coder via  │
   │ • play_youtube   │   │   hcnsec.cn API)     │
   │ • open_website   │   │  Returns JSON tool   │
   │ • open_app       │   │  call OR text reply  │
   │ • generate_pdf   │   └──────────┬───────────┘
   ├──────────────────┤              │
   │   weather.py     │              │
   │ • Open-Meteo     │              │
   │ • no API key     │              │
   ├──────────────────┤              │
   │   clock.py       │              │
   │ • BST clock      │              │
   │ • 3 calendars    │              │
   │ • offline        │              │
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
git clone https://github.com/Eftyqhar/RON.git
cd RON
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

**Option D — Text mode (no microphone):**
```bash
python main.py "create a pdf about black holes"
```
Runs a single command through the full pipeline — useful for testing when no
mic is attached.

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

### 📄 Documents
| Say This | What Happens |
|---|---|
| *"Create a PDF about black holes"* | Writes a formatted 3–5 page report, saves to Documents, opens it |
| *"Make me a report on the French Revolution"* | Same, with a title, headings, and bullet points |
| *"Create a PDF named notes with Hello world"* | Saves your exact dictated text |

Document generation is a **two-stage** process: Ron's assistant turn returns just
the topic, then a second dedicated writer turn produces the full 1200–2000 word
Markdown body. That separation is deliberate — the assistant persona is told to
stay terse, so asking it to inline a whole report into one JSON field produced
empty PDFs.

### ⚙️ System
| Say This | What Happens |
|---|---|
| *"Goodbye"* / *"Shut down"* / *"Sleep"* | Exits gracefully |

### 🌦️ Weather
| Say This | What Happens |
|---|---|
| *"What's the weather?"* | Current conditions for your configured city |
| *"Will it rain today?"* | Chance of rain for the rest of today, plus umbrella advice |
| *"What's the weather in Tokyo?"* | Any city, spoken inline |
| *"What's the forecast for tomorrow?"* | Tomorrow's high, low and rain chance |
| *"What's the forecast this week?"* | A three-day outlook |
| *"How hot is it?"* / *"What's the humidity?"* | Same reading, different phrasing |

See the **🌦️ Weather** section below for the full details.

### 🕰️ Date & Time
| Say This | What Happens |
|---|---|
| *"What's the current time?"* | The time in Bangladesh Standard Time — **and the date** |
| *"What time is it?"* / *"Tell me the time"* | Same answer, different phrasing |
| *"What's today's date?"* / *"What day is it?"* | The English date |
| *"What's the Bangla date?"* | *"the 9th of Bhadro, 1433"* |
| *"What's the Arabic date?"* / *"What's the Hijri date?"* | *"the 10th of Rabi al-Awwal, 1448"* |
| *"Give me the date in all three calendars"* | All of them in one answer |

See the **🕰️ Date & Time** section below for the full details.

---

## 🧠 Conversation Memory

Ron keeps a durable record of every conversation in a local SQLite database,
`history.db`, in the project folder. Nothing is uploaded anywhere — this is a
plain file on your disk, written with Python's built-in `sqlite3`, so there is no
extra dependency to install.

Two things use it:

- **Continuity across restarts.** On startup Ron replays the most recent
  `REPLAY_TURNS` (20) exchanges back into the LLM context, so it still knows what
  you were talking about yesterday.
- **Scrollback in the HUD.** Scroll up in the CONVERSATION panel and earlier
  sessions load a page at a time, served from `/api/history`.

```bash
python main.py "remember that my favourite planet is saturn"
python main.py "what is my favourite planet"      # recalls it from history.db
```

### What is stored

| Table | Contents |
|---|---|
| `sessions` | One row per run: start, end, model, hostname |
| `turns` | Every exchange — `user` and `ron`, with a timestamp |
| `events` | Tool calls and status lines, mirroring the HUD activity feed |

Turns are captured in `bus.py`, which every code path already publishes to, so
the record includes the folder and website commands Ron routes directly without
consulting the LLM.

### Inspecting it

```bash
sqlite3 history.db "SELECT role, text FROM turns ORDER BY id DESC LIMIT 20"
sqlite3 history.db "SELECT id, started_at, model FROM sessions"
```

### Controls

| Setting | Effect |
|---|---|
| `REPLAY_TURNS` in `main.py` | How many past exchanges are recalled at startup (20) |
| `MAX_CONTEXT_TURNS` in `main.py` | Hard ceiling on the live context sent to the LLM (40) |
| `set RON_REPLAY=0` | Start with a blank context; still records |
| `python ui_server.py --no-replay` | The same, for the HUD |
| `set RON_DB=D:\somewhere\ron.db` | Store the database elsewhere |

To wipe the record, close Ron and delete `history.db` (plus any `-wal` / `-shm`
files beside it). A fresh one is created on the next run.

> **Privacy.** `history.db` contains every word you have said to Ron, including
> anything picked up while the microphone was open. It is listed in
> `.gitignore` so it is never committed — keep it that way if you fork this repo.

---

## 🌦️ Weather

Ron reports live conditions and a short forecast, both spoken and on the HUD.
There is **no API key to obtain and nothing to sign up for** — the data comes from
[Open-Meteo](https://open-meteo.com/), which is free for non-commercial use and
needs no authentication. Requests go out through Python's built-in
`urllib.request`, so `requirements.txt` is unchanged.

```
You:  RON, what's the weather?
Ron:  Current weather in Sirajganj is 29 degrees Celsius with partly cloudy
      conditions. Humidity is 78 percent and wind speed is 12 kilometers per hour.

You:  RON, will it rain today?
Ron:  There is a 20 percent chance of rain in Sirajganj today, Sir. The heaviest
      risk is around 4 p.m. I would not bother with an umbrella.
```

Try it without a microphone:

```bash
python main.py "what's the weather"
python main.py "will it rain today"
python main.py "what's the weather in tokyo"
python main.py "what's the forecast for this week"

python weather.py            # all four answers at once, no LLM involved
python weather.py "Tokyo"    # the same for any city
```

### How it is routed

Weather questions are matched **before** Ron consults the LLM, in the same way
folder and website commands already are. That has two useful consequences:

- **It costs no tokens**, and it keeps working when your API key is rejected or
  your quota is exhausted. Weather is the one thing in Ron that does not depend on
  the language model at all.
- **The numbers are never invented.** An LLM asked about the weather will happily
  make up a temperature. `SYSTEM_PROMPT` now forbids that outright and gives the
  model a `get_weather` tool instead, which catches phrasings the direct matcher
  misses (*"do I need a jacket?"*) and routes them through the same code.

### On the HUD

Panel **05 — WEATHER** sits under SYSTEM MONITOR in the left column: the current
temperature and condition with a matching glyph, the city and the "feels like"
figure, humidity, wind, today's rain chance, and a five-slot strip of the next
twelve hours three hours apart, each slot carrying its own small glyph.

```
┌─ WEATHER ─────────── 05 ─┐
│  ☁  29°                  │
│     PARTLY CLOUDY        │
│  SIRAJGANJ · FEELS 33°   │
│  ───────────────────     │
│  HUMIDITY          78%   │
│  WIND         12 KM/H    │
│  RAIN TODAY        20%   │
│  ───────────────────     │
│  15   18   21   00   03  │
│  29°  28°  27°  26°  25° │
└──────────────────────────┘
```

`ui_server.py` refreshes it every `WEATHER_INTERVAL` (600 s) in a background
thread, publishing once immediately at startup so the panel is populated before a
browser has finished loading. Asking a weather question also refreshes it, so the
panel and the spoken answer never disagree. On a short viewport the strip and the
RAIN TODAY row are hidden first, so the panel degrades rather than overflowing.

### Configuration

| Setting | Effect |
|---|---|
| `DEFAULT_LOCATION` in `weather.py` | The city used when nothing else is set (`Sirajganj, Bangladesh`) |
| `set RON_LOCATION=Dhaka` | Override the location for this run |
| `set RON_UNITS=imperial` | °F and mph instead of °C and km/h |
| `python ui_server.py --location "Dhaka"` | The same as `RON_LOCATION`, for the HUD |
| `python ui_server.py --no-weather` | Skip the poller entirely; panel 05 shows OFFLINE |
| `CACHE_TTL` in `weather.py` | How long a reading is reused before refetching (600 s) |
| `WEATHER_INTERVAL` in `ui_server.py` | How often the HUD poller refreshes (600 s) |

Include the country or region when a city name is ambiguous —
`RON_LOCATION=Springfield, Illinois` resolves the way you meant. Saying a city
out loud (*"what's the weather in Tokyo"*) overrides the configured location for
that one answer only.

### Conditions

Open-Meteo reports WMO weather codes; `weather.py` maps them to spoken text and a
HUD glyph:

| Codes | Spoken as | Glyph |
|---|---|---|
| 0 | clear | ☀ / 🌙 (day and night variants) |
| 1, 2 | mainly clear, partly cloudy | ⛅ |
| 3 | overcast | ☁ |
| 45, 48 | fog, freezing fog | 🌫 |
| 51–57 | light / steady / heavy / freezing drizzle | 🌧 |
| 61–67 | light / steady / heavy / freezing rain | 🌧 |
| 71–77 | light / steady / heavy snow, snow grains | 🌨 |
| 80–82 | light / plain / violent showers | 🌧 |
| 85, 86 | snow showers | 🌨 |
| 95–99 | thunderstorm, with hail | ⛈ |

Anything unrecognised reads as *"unsettled"* rather than failing.

### When the network is down

Weather never takes Ron down. A dead connection, a DNS failure, a rate limit or
malformed JSON all end the same way:

- A reading up to `CACHE_TTL` old is reused, so repeated questions cost one
  request per ten minutes.
- If a refresh fails but Ron has an older reading, it is served with *"That
  reading is a little old, Sir — the service is not answering."* — a twenty-minute-old
  temperature beats an apology.
- With nothing cached at all, Ron says *"I could not reach the weather service,
  Sir."* An unresolvable city gets a different line — *"I could not find Atlantis
  on the map, Sir."* — because those are different problems with different fixes.
- The HUD panel dims and reads OFFLINE rather than leaving last hour's numbers on
  screen pretending to be live.

---

## 🕰️ Date & Time

Ask for the time and Ron gives you the time **and** the date, because that is
almost always what you actually wanted:

```
You:  RON, what's the current time?
Ron:  "The time is 4:32 p.m. Bangladesh Standard Time, Sir.
       Today is Monday, the 24th of August 2026."

You:  RON, what's the Bangla date?
Ron:  "In the Bangla calendar it is the 9th of Bhadro, 1433, Sir."

You:  RON, what's the Arabic date?
Ron:  "In the Hijri calendar it is the 10th of Rabi al-Awwal, 1448, Sir.
       That is the tabular reckoning, Sir — the sighted date may differ by a day."

You:  RON, give me the date in all three calendars.
Ron:  "Today is Monday, the 24th of August 2026, Sir. In the Bangla calendar that
       is the 9th of Bhadro, 1433, and in the Hijri calendar the 10th of
       Rabi al-Awwal, 1448. That is the tabular reckoning, Sir — the sighted date
       may differ by a day."
```

Everything is computed locally in `clock.py`. No network, no API key, no extra
package — so this works with the Wi-Fi off and with a rejected API key.

```bash
python main.py "what is the current time"
python main.py "what's the date in bangla"
python main.py "what's the hijri date"

python clock.py            # every answer at once, no mic and no LLM
python clock.py --check     # verify the calendar arithmetic
```

### The three calendars

| Calendar | Say | Example | Accuracy |
|---|---|---|---|
| **English** (Gregorian) | *"the date"* — the default | *Monday, the 24th of August 2026* | Exact |
| **Bangla** (Bangabda) | *"bangla"*, *"bengali"*, *"bangladeshi"* | *the 9th of Bhadro, 1433* | Exact |
| **Arabic** (Hijri) | *"arabic"*, *"hijri"*, *"islamic"*, *"lunar"* | *the 10th of Rabi al-Awwal, 1448* | ± 1 day — see below |

**Bangla** follows the **2019 Bangla Academy revision** used in Bangladesh, in
which Pohela Boishakh is always **14 April**: the first six months have 31 days,
the next five have 30, and Falgun has 29 — 30 when the February that follows it is
a leap February. That last rule is the whole mechanism keeping the new year pinned
to 14 April forever. Note that the **West Bengal** calendar differs; this one is
Bangladesh's.

**Hijri** is the *tabular* (so-called Kuwaiti) civil Islamic calendar, computed
arithmetically from the Julian Day Number. It is an approximation by nature: the
date observed in Bangladesh is fixed by **moon sighting** through the Islamic
Foundation and can fall a day either side of the arithmetic. Ron says so out loud
rather than presenting the figure as settled — that caveat is attached to the Hijri
answer only, since the other two are exact.

`python clock.py --check` proves the arithmetic against six independently known
dates, then walks Bangla years 1400–1499 asserting Boishakh 1 lands on 14 April
with no gap or overlap at the year boundary, then converts every single day from
1990 to 2089 and checks each one lands in a real month.

### The time

Bangladesh Standard Time, **UTC+6**, spoken as a clock rather than as digits —
*"4:32 p.m."*, not *"16:32"*, which Windows SAPI would read as two separate
numbers. On the hour it says *"twelve noon"* and *"midnight"*.

The offset is a **fixed** `UTC+6` rather than a `zoneinfo` lookup of
`Asia/Dhaka`, deliberately: on Windows `zoneinfo` needs the separate `tzdata`
package and **raises** when the database is missing. Bangladesh has observed no
daylight saving since 2009, so a constant offset is both exactly correct and
impossible to break.

### Configuration

| Setting | Where | Default | Notes |
|---|---|---|---|
| `TZ_OFFSET_HOURS` | `clock.py` | `6.0` | Bangladesh Standard Time |
| `RON_TZ_OFFSET` | environment | — | Hours, e.g. `5.5`. Clamped to ±14; junk falls back to the default rather than raising |
| `RON_TZ_NAME` | environment | — | What Ron calls the zone out loud. Set `RON_TZ_OFFSET` without this and it just says *"local time"* |

```bash
set RON_TZ_OFFSET=5.5
set RON_TZ_NAME=India Standard Time
python clock.py
```

### How it is routed

Like weather, this never reaches the LLM. `extract_datetime()` in `main.py`
classifies the question and `clock.py` answers it, so a *"what time is it"* costs
**zero tokens** and keeps working when the API key is rejected or the quota is
spent. The model is still handed a `get_datetime` tool for phrasings the patterns
miss, and the prompt forbids it from stating a time or a date itself — it has no
clock, and left to itself it will invent one.

The clock is checked **after** weather, on purpose. *"What's the temperature
today"* carries a calendar-ish word and is plainly a weather question, so letting
weather answer first keeps that true without either pattern needing to know about
the other. Two word boundaries do the rest of the work quietly: `\btime\b` does not
match *"uptime"* and `\bday\b` does not match *"today"*, which is how *"what is the
uptime"* and *"will it rain today"* stay out of this route entirely.

`python test_clock.py` pins all of it — the conversions, the phrasing, and a
routing table with the negatives spelled out: *"play time after time"*, *"what time
is the meeting"*, *"set a timer"* and *"is that file up to date"* must all stay away
from the clock.

---

## 🔍 File Search

Ron can hunt through your disks for files and folders:

```
You:  RON, find my python projects
Ron:  "I found 3 matches for "python projects", Sir.
       The first is python_projects, in C:\Users\you\Documents."

You:  RON, find all pdf files
Ron:  "I found 12 matches for PDF files, Sir. The first is report.pdf,
       in D:\Backup\python_projects."

You:  RON, locate report.pdf
Ron:  "I found one match for "report.pdf": report.pdf,
       in D:\Backup\python_projects."
```

Every search also opens the **DISK SEARCH overlay** on the HUD — a full-screen
console with a live scanline, falling particle FX and streaming counters while
the walk runs, then the result list. **Click any result** and Explorer opens with
it selected (folders open directly). **Esc closes** the overlay.

### How it searches

The traversal lives in `finder.py`: a *bounded* breadth-first `os.scandir` walk —
not a naive `rglob("*")` over the whole drive, which follows junctions into loops
and grinds through `C:\Windows` for minutes. It prunes system/cache directories
(`Windows`, `$Recycle.Bin`, `node_modules`, `__pycache__`, `AppData`…), refuses to
cross junctions/symlinks so it cannot loop, caps at 60 results, stops after 8
seconds, and limits depth to 12. Permission errors mid-walk are swallowed; the
search keeps going.

| Mode | Triggered by | Matches |
|---|---|---|
| `keyword` | *"find my python projects"* | substring in file or folder names |
| `extension` | *"find all pdf files"*, *"search *.docx"* | every file with that extension |
| `filename` | *"locate report.pdf"* | exact file name |
| `folder` | *"find the downloads folder"* | folders whose name contains it |

Spoken words map too — *"music"* → `.mp3`, *"word"* → `.docx`, *"python"* → `.py`,
and so on. Routing is pattern-based like weather and the clock: **zero tokens**,
and it works with the API key rejected. Phrasings aimed elsewhere (*"search
youtube for…"*, *"find out"*) never reach the disk walk.

```bash
python main.py "find my python projects"
python finder.py "*.pdf"                 # CLI smoke test, no mic and no LLM
python finder.py --folder "Documents"
```

### Configuration

| Setting | Where | Default | Notes |
|---|---|---|---|
| `RON_SEARCH_ROOTS` | environment | all fixed drives | Where to look. Semicolon- or comma-separated, e.g. `F:\Projects;D:\Docs` |
| `MAX_RESULTS` / `TIME_BUDGET` / `MAX_DEPTH` | `finder.py` | 60 / 8s / 12 | The bounds that keep a search snappy |

> ⚠️ Clicking a result asks the HUD server (`/api/open`) to reveal that path on
> disk. The server validates that the path is absolute and exists, but this
> endpoint can open arbitrary existing folders — which is fine only because the
> server binds to `127.0.0.1`. Never expose it beyond loopback.

---

## 📡 Internet Speed

Ron can measure your internet connection — ping, download and upload — and show
the numbers on the HUD in a full-screen cyberpunk console while it speaks the
result. No `speedtest-cli`, no signup, no API key: it uses Cloudflare's public
speed-test endpoints over the standard library.

```
You:  RON, check my internet speed.
Ron:  Your internet speed, Sir: download is 142.5 megabits per second,
      while upload is 58.3 megabits per second, while ping is 12.4
      milliseconds -- that is excellent, Sir.
```

### Trigger phrases

| Say | What happens |
|---|---|
| *"Check my internet speed"* | Runs the full test |
| *"What about my internet speed?"* | Same |
| *"How fast is my connection / internet / network?"* | Same |
| *"Speed test"* | Same |
| *"Ping my internet"* | Same |
| *"Test my bandwidth"* | Same |
| *"What is my network speed?"* | Same |
| *"Check my connection performance"* | Same |

Or ask the LLM: *"What's my internet speed?"* → it emits `get_internet_speed`.

### How it measures

`netspeed.py` hits three Cloudflare public endpoints with `urllib.request`
(stdlib only — no new dependency):

| Phase | Endpoint | Method |
|---|---|---|
| Ping | `https://1.1.1.1` | HEAD (4 samples, median reported) |
| Download | `https://speed.cloudflare.com/__down?bytes=5000000` | GET 5 MB |
| Upload | `https://speed.cloudflare.com/__up` | POST 2 MB |

The single HTTP seam (`_fetch`) is injectable, so `test_netspeed.py` stubs it
and runs fully offline. The module inherits the same two rules as `weather.py`:
**nothing raises into the caller**, and **nothing happens at import time**.

### On the HUD

A full-screen overlay opens the moment the test starts (`status="scanning"`),
so a browser that connects mid-test is caught up. It shows:

* a **phase indicator** — PING / DOWNLOAD / UPLOAD — with a progress bar;
* three live stat cells — ping (ms), download (Mbps), upload (Mbps);
* an elapsed timer;
* a particle field that runs only while the test is live and stops on `done`.

The overlay is closed with **Esc** or the × button. A stale `done` snapshot
does not pop the overlay open in a late-connecting browser; only a live
`scanning` frame does.

### Configuration

| Setting | Where | Default | Notes |
|---|---|---|---|
| `DOWNLOAD_BYTES` / `UPLOAD_BYTES` | `netspeed.py` | 5 MB / 2 MB | Transferred per phase; smaller = faster, less accurate on fast links |
| `PING_SAMPLES` | `netspeed.py` | 4 | Number of HEAD samples; median is reported |
| `_HTTP_TIMEOUT` | `netspeed.py` | 12 s | Per-phase ceiling |

### When the network is down

- A dead link, a DNS failure or a timeout: `run()` returns `ok=False` with a
  plain error string; `describe()` apologises; the HUD shows **TEST FAILED**
  in amber. Nothing propagates to the voice loop.
- If ping alone fails but download works (or vice versa), the test still
  reports the numbers it got — only a total silence across ping *and*
  download flips `ok` to `False`.

```bash
python main.py "check my internet speed"   # full pipeline
python netspeed.py                         # CLI smoke test, no mic and no LLM
python netspeed.py --probe                 # raw JSON from the live endpoints
```

---

## ⏱️ Timer

Ron can set a countdown timer from a spoken phrase and render it on the HUD
as a full-screen cyberpunk console — a large live countdown, a progress bar,
elapsed time, and a particle field that runs while the timer ticks. No API key
and no extra dependency: the engine is a stdlib background thread that
publishes progress through the event bus.

```
You:  RON, set a timer for 2 minutes.
Ron:  Timer set for 2m.
... [countdown ticks on the HUD] ...
Ron:  Your 2m timer is up, Sir.
```

### Trigger phrases

The direct regex route (`main.py`) catches these without involving the LLM, so
even a mic-less invocation like `python main.py "set a timer for 2 minutes"`
works:

| Say | What happens |
|---|---|
| *"Set a timer for 2 minutes / 30 seconds / 1 hour"* | Arms a countdown of that duration |
| *"2 minute timer"* / *"30 second countdown"* | Same |
| *"Timer for 10 minutes"* | Same |

Or ask the LLM: *"Set a timer for five minutes"* → it emits `set_timer` with
`{"duration": "5 minutes"}`.

### How it counts

`timer.py` runs a daemon thread that ticks once a second, shrinking
`remaining_sec` and publishing a `running` frame on each tick. When the
countdown reaches zero it publishes a terminal `done` frame and the assistant
speaks. A fault in the background loop is swallowed and surfaced as an `error`
frame rather than being allowed to escape the thread. Only one timer is live at
a time — starting a new one cancels the previous. The module follows the same
two rules as `weather.py` and `netspeed.py`: **nothing raises into the
caller**, and **nothing happens at import time**.

### States

| `status` | Meaning |
|---|---|
| `set` | Accepted, countdown armed (published synchronously before the thread starts) |
| `running` | Tick update; `remaining_sec` shrinks toward zero |
| `done` | Elapsed; the assistant speaks and the overlay alerts |
| `cancelled` | The user stopped it before it elapsed |
| `error` | A fault in the background loop |

### On the HUD

A full-screen overlay opens the moment the timer is armed (`status="set"`), so
a browser that connects mid-countdown is caught up. It shows:

* a **large countdown readout** — `MM:SS` (or `HH:MM:SS` when ≥ 1 h), updated
  each tick;
* a **progress bar** with the percentage remaining;
* **elapsed** and **duration** stat cells;
* a **particle field** that runs only while the timer is `set`/`running` and
  stops on `done`/`cancelled`.

The overlay is closed with **Esc** or the × button. A stale `done` or
`cancelled` snapshot does not pop the overlay open in a late-connecting
browser; only a live `set`/`running` frame does.

### When the timer elapses

Two things happen at once so the user notices even away from the screen:

- **Sound** — the backend plays a rising four-tone alarm through
  `winsound.Beep` (Windows stdlib, no dependency); on POSIX it falls back to
  the `beep` utility. The browser overlay also emits three repeating beeps
  via the Web Audio API and flashes the panel border in amber.
- **Speech** — the assistant says *"Your 2m timer is up, Sir."*

The alarm stops the moment the overlay is closed.

### Configuration

There are no tunables — the duration comes entirely from the spoken request.
The duration label is rendered as `45s`, `2m`, `1h 30m` etc.

### When the timer is cancelled

Saying *"cancel the timer"* (LLM → `set_timer` with a cancellation intent) or
closing the HUD overlay stops the background thread and publishes a
`cancelled` frame; the overlay greys out and the assistant confirms.

```bash
python main.py "set a timer for 2 minutes"  # full pipeline
python main.py "30 second timer"            # direct regex route, no LLM
```

---

## 🔊 Volume Control

Ron can change the system master volume directly — set an absolute level, nudge
it up or down, or mute/unmute — and confirm the new level in speech and on the
HUD. No third-party package: it talks to the Windows Core Audio API
(`IAudioEndpointVolume`) through `ctypes` and the standard library.

```
You:  RON, set volume to 50.
Ron:  Volume set to 50 percent, Sir.

You:  Mute.
Ron:  Muted, Sir.

You:  Volume up 10.
Ron:  Volume set to 60 percent, Sir.
```

### Trigger phrases

| Say | What happens |
|---|---|
| *"Set volume to 50"* / *"volume 75"* / *"volume to 30%"* | Absolute level (0–100) |
| *"Volume up"* / *"volume down"* | Nudge ±10 points |
| *"Volume up by 5"* / *"volume down 15"* | Nudge by a signed amount |
| *"Turn it up / down"* | Nudge ±10 |
| *"Mute"* / *"mute the speakers"* | Mute master |
| *"Unmute"* | Unmute master |
| *"Toggle mute"* | Flip mute state |

Or ask the LLM: *"set the volume to 40"* → it emits `set_volume` with
`{"action": "set", "level": 40}`.

### How it changes the volume

`volume.py` co-creates the Windows `MMDeviceEnumerator`, asks it for the
default render (playback) endpoint, and activates `IAudioEndpointVolume` on
that device. It then calls `SetMasterVolumeLevelScalar` (0.0–1.0) or
`SetMute`. The COM interface is created lazily on the first call and cached,
so subsequent adjustments are cheap.

The single COM seam is wrapped so that **nothing raises into the caller**: if
no default render device exists, if `CoCreateInstance` fails, or if the
Activate call is denied, every public function returns `None` (read) or
`False` (write) and `describe()` apologises. The module follows the same two
rules as `weather.py`, `netspeed.py` and `timer.py`: nothing raises, and
nothing happens at import time.

### On the HUD

A volume change publishes a `set` frame through `bus.volume()` with the new
`level` (0–100), `level_fmt` (e.g. `"75%"`), and `muted` flag. The HUD can
render these in a compact volume panel; a terminal `error` frame appears only
when the COM path is unavailable.

### When the audio device is unavailable

In a sandbox, a VM without a sound device, or on a non-Windows host, every
volume call returns `None`/`False` and the assistant says *"I could not change
the volume, Sir."* The command loop never breaks; `test_volume.py` stubs this
path deterministically so it passes on any machine.

```bash
python main.py "set volume to 50"   # full pipeline
python main.py "volume up 10"       # nudge
python main.py "mute"               # mute
python main.py "unmute"             # unmute
python test_volume.py               # offline check (no audio device needed)
```

---

## 📁 Project Structure

```
ron-ai-assistant/
├── main.py              # 🧠 Entry point — intent dispatcher & LLM loop
├── voice.py             # 🎙️ STT (Google) + TTS (Windows SAPI)
├── tools.py             # 🛠️ Tool implementations (apps, web, PDF, YouTube)
├── bus.py               # 📡 Event bus — pipeline → HUD and → history
├── history.py           # 💾 Durable chat history (SQLite, stdlib only)
├── history.db           # 🔒 Your conversations — git-ignored, created on first run
├── weather.py           # 🌦️ Open-Meteo client, spoken phrasing (stdlib only, no key)
├── clock.py             # 🕰️ BST clock + English/Bangla/Hijri calendars (stdlib only)
├── finder.py            # 🔍 Bounded disk search engine (stdlib only)
├── netspeed.py          # 📡 Cloudflare speed-test client, spoken phrasing (stdlib only, no key)
├── timer.py             # ⏱️ Countdown timer engine, spoken phrasing (stdlib only, no key)
├── volume.py            # 🔊 Windows Core Audio volume client (ctypes, no dependency)
├── ui_server.py         # 🖥️ HUD web server + SSE bridge
├── ui/                  # 🎨 Holographic interface (index.html, app.js, style.css)
├── requirements.txt     # 📦 Python dependencies
├── run_ron.bat          # 🪟 Windows launcher script
├── run_ron_ui.bat       # 🪟 Launcher for the HUD
├── test_mic.py          # 🎤 Microphone discovery & test utility
├── test_pdf.py          # 📄 Offline PDF renderer check (no mic/API needed)
├── test_history.py      # 💾 Offline chat-history check (no mic/API needed)
├── test_clock.py        # 🕰️ Offline clock & calendar check (no mic/API needed)
├── test_finder.py       # 🔍 Offline disk-search check (scratch tree, no drives walked)
├── test_netspeed.py     # 📡 Offline speed-test check (stubbed endpoints, no network)
├── test_timer.py        # ⏱️ Offline timer check (stubbed bus, no real countdown)
├── test_volume.py       # 🔊 Offline volume check (GUID layout, phrasing, COM-unavailable path)
├── test_hud.py          # 📡 Offline HUD event-pipeline check
├── test_ui_render.py    # 🖼️ Headless render check for the interface
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
MODEL = "kat-coder-pro-v2.5"    # Default
# MODEL = "DeepSeek-V4-Flash"   # Fallback (confirmed working on this endpoint)
# MODEL = "Qwen3.5-397B-A17B"   # Fallback (test with test_api.py)
```

### Customizing the System Prompt

Edit `SYSTEM_PROMPT` in `main.py` to change Ron's personality, add new tools, or modify behavior. Ron is currently configured to:
- Refer to itself as **Ron**
- Address the user as **Sir** or **Ifteqhar**
- Emit **JSON-only** responses when a tool action is required
- Keep *spoken* replies short and sharp (document bodies are written by a separate writer prompt, so they are not affected)

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
| `kat-coder-pro-v2.5` | ⚠️ Unverified | Current default — not yet tested against this endpoint |
| `DeepSeek-V4-Flash` | ✅ Works | Confirmed via `test_raw.py` (status 200) |
| `Qwen3.5-397B-A17B` | ✅ Works | Good quality; slower on long documents |
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
- **[fpdf2](https://github.com/py-pdf/fpdf2)** — PDF generation (legacy `fpdf` 1.7.2 also supported)
- **[duckduckgo_search](https://github.com/deedy5/duckduckgo_search)** — Fallback URL resolution
- **[Open-Meteo](https://open-meteo.com/)** — Weather and forecast data (no API key; called with stdlib `urllib`)
- **stdlib `datetime` + `calendar`** — Bangladesh Standard Time and the Bangla/Hijri calendars, computed locally

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

Ron distinguishes two different failures, because they need different fixes:

- **`[API Error: Invalid token ...]`** — your **API key is rejected** (expired,
  revoked, or mistyped). Ron says *"My API key was rejected, Sir."* Paste a fresh
  key at `main.py:18`. Note this has nothing to do with your token *allowance*,
  despite the word "token" in the message.
- **`quota` / `balance` / `insufficient`** — you are out of allowance. Ron says
  *"我的月度token配额已不足。"* Top up at the [hcnsec.cn dashboard](https://hcnsec.cn).

Run `python test_api.py` to check the key and endpoint independently. The PDF
renderer needs no key at all — `python test_pdf.py` verifies it offline.

</details>

<details>
<summary><b>🔇 No audio output</b></summary>

- Confirm Windows SAPI voices are installed (`Settings → Time & Language → Language → Speech`).
- The fallback in `voice.py` prints text to console if TTS fails.

</details>

<details>
<summary><b>📄 PDF comes out blank, 0 bytes, or has no bold text</b></summary>

Run `python test_pdf.py` — it prints which PDF library is installed and renders
a sample report without needing a mic or API key.

- **Blank single page:** the model returned a placeholder instead of a body.
  Ron now refuses to save these, so you get a spoken error instead of an empty
  file. Check that `SYSTEM_PROMPT` still sends `topic` (not `content`).
- **No inline bold:** you have legacy `fpdf` 1.7.2, which has no
  `multi_cell(markdown=True)`. Ron emulates bold via `write()`, but for native
  rendering: `pip uninstall -y fpdf` then `pip install "fpdf2>=2.7.6"`.
  (Uninstall first — both packages install into the same `fpdf/` directory.)
- **0-byte file:** legacy `fpdf` writes PDF metadata without UTF-16 encoding
  and truncates the file before encoding the buffer, so a non-ASCII title
  killed the output. `tools.py` now sanitizes metadata on legacy fpdf.

</details>

<details>
<summary><b>💾 "History disabled — chat will not be saved"</b></summary>

Ron prints this once and then carries on without saving. It means `sqlite3` could
not open the database file. Usually one of:

- The project folder is read-only, or on a synced drive that has the file locked.
- `RON_DB` points somewhere that does not exist and cannot be created.
- A previous crash left `history.db` corrupt — rename it and let Ron make a new one.

The assistant itself is unaffected by design: a storage fault never interrupts a
conversation, it only stops it being recorded. Verify the layer independently with
`python test_history.py`, which uses a scratch database and needs no mic or key.

</details>

<details>
<summary><b>🌦️ Weather says "I could not find that place" or the panel reads OFFLINE</b></summary>

Two different failures, and Ron words them differently on purpose:

- **"I could not find *X* on the map, Sir."** — the geocoder had no match. Usually a
  misheard city name, or one that is ambiguous. Add the country or region:
  `set RON_LOCATION=Springfield, Illinois`. Check what the geocoder actually
  returns with:

  ```bash
  python weather.py --probe "Springfield, Illinois"
  ```

  That prints the raw geocode results, the resolved coordinates, the full forecast
  response and the parsed reading — the fastest way to see which step went wrong.

- **"I could not reach the weather service, Sir."** — a network, DNS or rate-limit
  problem. Nothing to configure; it recovers on its own. Ron keeps answering
  everything else normally.

If panel 05 reads **OFFLINE** on the HUD, the poller has no reading to show.
`--no-weather` also produces this deliberately. **STANDBY** is different: it means
the first fetch is still in flight, which is normal for a second or two after
launch.

No API key is involved anywhere in this — if weather fails, it is not your
`main.py` key.

</details>

<details>
<summary><b>🕰️ The time is wrong, or the Bangla/Hijri date looks off by a day</b></summary>

**The time is wrong.** Ron reports a fixed **UTC+6**, not your PC's clock. That is
correct for Bangladesh and wrong everywhere else, which is the trade-off for never
needing the `tzdata` package on Windows. Point it at your own zone:

```bash
set RON_TZ_OFFSET=5.5
set RON_TZ_NAME=India Standard Time
python clock.py
```

An unparseable `RON_TZ_OFFSET` falls back to `UTC+6` silently rather than raising,
and anything beyond ±14 hours is clamped — so a typo cannot take Ron down, but it
also will not announce itself. Run `python clock.py` to see the offset actually in
force; it prints on the first line.

**The Hijri date is a day out.** Expected, and Ron says so in the answer itself.
`clock.py` computes the *tabular* Islamic calendar arithmetically; the date observed
in Bangladesh is fixed by **moon sighting** through the Islamic Foundation and can
differ by a day either way. There is nothing to configure — treat the spoken figure
as the tabular reckoning it says it is.

**The Bangla date is a day out.** This one should not happen, so it is worth
checking properly:

```bash
python clock.py --check
```

That verifies six known dates, asserts Pohela Boishakh lands on 14 April for Bangla
years 1400–1499, and converts every day from 1990 to 2089. If it passes and you
still disagree with the date, you are probably comparing against the **West Bengal**
calendar, which is not the same as Bangladesh's — `clock.py` follows the 2019 Bangla
Academy revision used in Bangladesh.

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

- GitHub: [@eftyqhar](https://github.com/Eftyqhar)
- Project: [ron-ai-assistant](https://github.com/Eftyqhar/RON/)

---

## 🙏 Acknowledgments

- Inspired by **J.A.R.V.I.S.** from the *Iron Man* universe
- Powered by an OpenAI-compatible LLM endpoint (currently `kat-coder-pro-v2.5`)
- Speech recognition by [Google Cloud Speech](https://cloud.google.com/speech-to-text)

---

<div align="center">

**If Ron helps you, drop a ⭐ on the repo — it means a lot.**

*Built with ❤️ in Bangladesh 🇧🇩*

</div>
