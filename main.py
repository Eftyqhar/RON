import json
import os
import re
import sys
import threading
import time
from openai import OpenAI
import bus
import clock
import finder
import history
import netspeed
import timer
import volume
import weather
from voice import speak, listen
from tools import (play_youtube, generate_pdf, open_app, open_website,
                   open_folder, resolve_user_folder, _FOLDER_ALIASES)

# Fix encoding for Windows console
if sys.platform == 'win32':
    import io
    # Flush before the swap. Whatever the importing process already printed is
    # still in the outgoing wrapper's buffer whenever stdout is a file or pipe,
    # and rebinding sys.stdout drops that wrapper without flushing it -- so a
    # redirected run loses every line printed before this import. Matters to the
    # test suites, which print progress and then import this module.
    try:
        sys.stdout.flush()
    except Exception:
        pass
    # line_buffering=True matters whenever stdout is a file or pipe rather than a
    # terminal: Python block-buffers non-TTY streams, so without it every print
    # sits in an 8 KB buffer until the process exits and a slow run looks dead.
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                                  errors='replace', line_buffering=True)

# Use OpenAI-compatible API from hcnsec.cn. The SDK appends /chat/completions to
# base_url, so the /v1 belongs here: api.hcnsec.cn/v1/chat/completions is the path
# confirmed working against this host (see test_output.txt -- status 200).
client = OpenAI(
    api_key="YOUR_API_KEY",
    base_url="https://api.hcnsec.cn/v1"
)
# MODEL = "Qwen3-Embedding-8B"
MODEL = "ox-alpha"
# MODEL = "DeepSeek-V4-Flash"    # fallback; confirmed 200 on this endpoint
# MODEL = "Qwen3.5-397B-A17B"    # fallback; verify with test_api.py

# --- Runtime control -------------------------------------------------------
# The HUD needs to pause the microphone and shut the assistant down from a
# request thread, and it can submit typed commands while the voice loop is
# mid-turn. These three objects are the whole coordination surface; the console
# entry point uses them too, so both front ends behave identically.
command_lock = threading.RLock()   # one command in flight at a time
voice_enabled = threading.Event()  # clear() mutes the mic loop
voice_enabled.set()
shutdown_event = threading.Event()

bus.meta(model=MODEL)

SYSTEM_PROMPT = """You are Ron, an advanced personal AI assistant created by Ifteqhar.
You are intelligent, concise, and helpful — like JARVIS from Iron Man.

You can either respond conversationally OR trigger one of these tools by responding ONLY with valid JSON:

Tools:
1. Play YouTube:    {"tool": "play_youtube", "search_query": "..."}
2. Generate PDF:    {"tool": "generate_pdf", "file_name": "short_file_name", "topic": "What the document should cover"}
3. Open App:        {"tool": "open_app", "app_name": "..."}
4. Open Website:    {"tool": "open_website", "url": "..."}
5. Get Weather:     {"tool": "get_weather", "query": "current|rain|forecast", "location": "..."}
6. Get Date/Time:   {"tool": "get_datetime", "query": "time|date", "calendar": "english|bangla|arabic|all"}
7. Find Files:      {"tool": "find_files", "query": "what to look for", "mode": "keyword|filename|extension|folder"}
8. Internet Speed:  {"tool": "get_internet_speed"}
9. Set Timer:       {"tool": "set_timer", "duration": "2 minutes"}
10. Volume:          {"tool": "set_volume", "action": "set", "level": 50}

Rules:
- play music/video → play_youtube (JSON only)
- create/make/write/generate a pdf/report/document/notes about or on a subject → generate_pdf (JSON only), examples:
    "create a pdf about black holes" → {"tool": "generate_pdf", "file_name": "black_holes", "topic": "Black Holes"}
    "make me a report on the French Revolution" → {"tool": "generate_pdf", "file_name": "french_revolution", "topic": "The French Revolution"}
    "write a document about machine learning basics" → {"tool": "generate_pdf", "file_name": "machine_learning_basics", "topic": "Machine Learning Basics"}
  If — and only if — the user dictates the exact text to put in the file, pass it as "content" instead of "topic":
    "create a pdf named notes with hello world" → {"tool": "generate_pdf", "file_name": "notes", "content": "Hello world"}
  Never write the report body yourself in this JSON. Just give the topic; the document is written separately.
- open software/application → open_app (JSON only). Folders count as well — pass the
  plain folder name, never a program name:
    "open my files" → {"tool": "open_app", "app_name": "files"}
    "open document folder" → {"tool": "open_app", "app_name": "documents"}
- visit/open/go to any website → open_website (JSON only), examples:
    "visit facebook" → {"tool": "open_website", "url": "https://www.facebook.com"}
    "open google" → {"tool": "open_website", "url": "https://www.google.com"}
    "go to youtube" → {"tool": "open_website", "url": "https://www.youtube.com"}
- weather, temperature, rain, or a forecast → get_weather (JSON only). Never state a
  temperature or a forecast yourself; you have no weather data. Omit "location" to
  use the user's own city. Examples:
    "how is it looking outside" → {"tool": "get_weather", "query": "current"}
    "do I need a jacket" → {"tool": "get_weather", "query": "current"}
    "should I take an umbrella" → {"tool": "get_weather", "query": "rain"}
    "what is it like in Tokyo" → {"tool": "get_weather", "query": "current", "location": "Tokyo"}
    "what is the week looking like" → {"tool": "get_weather", "query": "forecast"}
- the time, the date, the day, or a Bangla/Hijri calendar date → get_datetime (JSON
  only). Never state a time or a date yourself; you have no clock. Omit "calendar"
  for the English date, which is the default. Examples:
    "what is the current time" → {"tool": "get_datetime", "query": "time"}
    "what is today's date" → {"tool": "get_datetime", "query": "date"}
    "what day is it" → {"tool": "get_datetime", "query": "date"}
    "what is the Bangla date" → {"tool": "get_datetime", "query": "date", "calendar": "bangla"}
    "what is the Islamic date today" → {"tool": "get_datetime", "query": "date", "calendar": "arabic"}
    "give me the date in all three calendars" → {"tool": "get_datetime", "query": "date", "calendar": "all"}
- find/search/locate a file or folder on the user's disk, or "where is X" → find_files
  (JSON only). Pick the mode from what they ask for; omit it and it is inferred.
  "keyword" matches part of a name, "filename" is an exact file, "extension" is all
  files of a type, "folder" matches directories only. Examples:
    "find my python projects" → {"tool": "find_files", "query": "python projects", "mode": "keyword"}
    "find all pdf files" → {"tool": "find_files", "query": "pdf", "mode": "extension"}
    "locate report.pdf" → {"tool": "find_files", "query": "report.pdf", "mode": "filename"}
    "where is my resume" → {"tool": "find_files", "query": "resume", "mode": "keyword"}
    "find the downloads folder" → {"tool": "find_files", "query": "downloads", "mode": "folder"}
- internet speed / connection speed / how fast is my internet / what about my internet
  speed → get_internet_speed (JSON only). Measures download, upload and ping and
  reports the numbers. No arguments needed:
    "check my internet speed" → {"tool": "get_internet_speed"}
    "how fast is my connection" → {"tool": "get_internet_speed"}
- set a timer / countdown / alarm for some duration → set_timer (JSON only).
  Parse the duration from what they say and pass it as a human-readable string:
    "set a timer for 2 minutes" → {"tool": "set_timer", "duration": "2 minutes"}
    "countdown 30 seconds" → {"tool": "set_timer", "duration": "30 seconds"}
    "set a 1 hour timer" → {"tool": "set_timer", "duration": "1 hour"}
- change / set / adjust the volume → set_volume (JSON only). ``action`` is one
  of ``"set"`` (absolute), ``"mute"``, ``"unmute"``, ``"toggle"`` (flip mute),
  or ``"step"`` (nudge). For ``"set"`` pass ``level`` as 0-100; for ``"step"``
  pass ``delta`` as a signed percentage-point change:
    "set volume to 50" / "volume 50" → {"tool": "set_volume", "action": "set", "level": 50}
    "mute" / "mute the speakers" → {"tool": "set_volume", "action": "mute"}
    "unmute" → {"tool": "set_volume", "action": "unmute"}
    "toggle mute" → {"tool": "set_volume", "action": "toggle"}
    "volume up 10" / "volume down 5" → {"tool": "set_volume", "action": "step", "delta": 10}
    "turn it up" / "bring the volume down" → {"tool": "set_volume", "action": "step", "delta": 10}
- NEVER ask questions for tool actions, just execute immediately
- Keep spoken replies short and sharp. Never be verbose.
- Always refer to yourself as Ron.
- Always refer to the user as Sir or by name Ifteqhar."""

DOCUMENT_WRITER_PROMPT = """You are an expert technical writer producing a polished reference document.

Write a thorough, accurate, well-structured report on the topic you are given.

Structure it in Markdown, exactly like this:
# Document Title
## Introduction
(2-3 paragraphs setting up the subject and why it matters)
## (4 to 6 substantive sections, each with a descriptive ## heading)
(Use ### sub-headings where a section has distinct parts. Use "- " bullet
lists for enumerations, sets of examples, causes, or characteristics. Use
**bold** to highlight key terms. Write real paragraphs of prose, not just
bullet points.)
## Key Takeaways
(4-6 bullet points summarising the essentials)
## Conclusion
(1-2 closing paragraphs)

Requirements:
- Length: 1200-2000 words. This must fill 3 to 5 printed pages. Do not be brief.
- Be specific: include concrete facts, figures, dates, names, and examples.
- Plain Markdown only. No code fences, no tables, no JSON.
- Output ONLY the document itself. No preamble, no commentary, no "here is your report"."""

# --- Conversation context --------------------------------------------------
REPLAY_TURNS = 20        # exchanges recalled from history.db at startup
MAX_CONTEXT_TURNS = 40   # hard ceiling on the live context


def _seed_context():
    """Build the starting context, recalling recent turns from history.db.

    Two things about the replayed turns are worth knowing:

    * The assistant turns are the *spoken* text ("Opening facebook.com, Sir."),
      not the raw {"tool": ...} JSON the model originally emitted -- `speak()` is
      what publishes to the bus. That is the better input: feeding tool-call JSON
      back as assistant history teaches the model that bare JSON is an acceptable
      shape for ordinary conversation, and it starts emitting spurious tool calls.
      Context therefore differs slightly either side of a restart, since the live
      path below appends the raw reply.
    * Folder and website commands are included, because they reach the bus even
      though they never reach the LLM. A fuller record than the live path keeps.
    """
    seeded = [{"role": "system", "content": SYSTEM_PROMPT}]
    if os.environ.get("RON_REPLAY", "1") == "0":
        return seeded
    for role, text in history.recent_turns(REPLAY_TURNS):
        # The database records who spoke ('ron'); the API wants a role name.
        seeded.append({"role": "assistant" if role == "ron" else "user",
                       "content": text})
    if len(seeded) > 1:
        print(f"[Recalled {len(seeded) - 1} turn(s) from "
              f"{os.path.basename(history.db_path())}]")
    return seeded


def _trim_context():
    """Cap the live context, always keeping the system prompt at index 0.

    Without this the list grows for as long as the process runs, and every
    request carries the whole session. Replay makes that pressing rather than
    theoretical, because a run now *starts* with REPLAY_TURNS in hand.
    """
    excess = len(conversation_history) - (MAX_CONTEXT_TURNS + 1)
    if excess > 0:
        del conversation_history[1:1 + excess]


conversation_history = _seed_context()

WEBSITE_KEYWORDS = ["visit", "go to", "browse", "navigate to", "take me to"]

# Folder names live in tools._FOLDER_ALIASES so open_app() and extract_folder()
# agree on what counts as a folder request.

KNOWN_SITES = {
    "youtube": "https://www.youtube.com",
    "facebook": "https://www.facebook.com",
    "google": "https://www.google.com",
    "instagram": "https://www.instagram.com",
    "twitter": "https://www.twitter.com",
    "x": "https://www.x.com",
    "github": "https://www.github.com",
    "gmail": "https://mail.google.com",
    "whatsapp": "https://web.whatsapp.com",
    "netflix": "https://www.netflix.com",
    "amazon": "https://www.amazon.com",
    "reddit": "https://www.reddit.com",
    "pornhub": "https://www.pornhub.com",
    "linkedin": "https://www.linkedin.com",
    "tiktok": "https://www.tiktok.com",
    "discovery ftp": "https://dflix.discoveryftp.net/m",
}

def extract_website(command: str):
    for kw in WEBSITE_KEYWORDS:
        if command.startswith(kw):
            site = command.replace(kw, "").strip()
            if site:
                # Check known sites first
                if site.lower() in KNOWN_SITES:
                    return KNOWN_SITES[site.lower()]
                # If already a URL
                if site.startswith("http"):
                    return site
                # Search and get first result using DuckDuckGo
                from ddgs import DDGS
                with DDGS() as ddgs:
                    results = list(ddgs.text(f"{site} official site", max_results=5))
                    for r in results:
                        url = r["href"]
                        if "wikipedia" not in url and "wiki" not in url:
                            return url
                import urllib.parse
                return f"https://www.google.com/search?q={urllib.parse.quote(site)}"

    # Handle "open <site>" commands
    if command.startswith("open "):
        site = command.replace("open ", "").strip()
        if site.lower() in KNOWN_SITES:
            return KNOWN_SITES[site.lower()]
        # Check if site name is in command (e.g., "open pornhub", "open youtube")
        for site_name, site_url in KNOWN_SITES.items():
            # Word-boundary match. A bare substring test lets the "x" entry fire on
            # any command containing that letter -- "open max" would open x.com.
            if re.search(rf'\b{re.escape(site_name)}\b', command.lower()):
                return site_url

    return None

def extract_folder(command: str):
    """Resolve drive letters and well-known folder names to a filesystem path."""
    # Drive letters: "open D drive", "show me the C drive"
    drive = re.search(r'\b([a-zA-Z])\s*drive\b', command)
    if drive:
        letter = drive.group(1).upper()
        named = re.search(r'(\w+)\s+folder', command)
        if named:
            candidate = f"{letter}:\\{named.group(1)}"
            if os.path.isdir(candidate):
                return candidate
        return f"{letter}:\\"

    # Named user folders: "open documents", "open my downloads folder".
    # The previous version built a `folder` variable here and then discarded it,
    # returning None for everything without a drive letter -- so every named-folder
    # request fell through to the LLM, which guessed open_app("Files") and left cmd
    # printing "'Files' is not recognized".
    if re.search(r'\b(open|show|go to|take me to)\b', command):
        for alias in sorted(_FOLDER_ALIASES, key=len, reverse=True):
            sub = _FOLDER_ALIASES[alias]
            if sub and re.search(rf'\b{re.escape(alias)}\b', command):
                path = resolve_user_folder(sub)
                if os.path.isdir(path):
                    return path
    return None

# --- Weather ---------------------------------------------------------------
# Answered directly, without an LLM turn, for the same reasons as folders and
# websites: it costs nothing, it is instant, and it keeps working when the API key
# is rejected or the quota is spent. weather.py does the talking to Open-Meteo.

# "will it rain tomorrow" is a forecast question even though it says rain, so the
# time markers are tested before the rain patterns below.
_WX_FORECAST_WHEN = re.compile(
    r"\b(tomorrow|tonight|this week|next week|the week|next few days|coming days"
    r"|next \d+ days|rest of the week|weekend)\b")
_WX_FORECAST = re.compile(r"\b(forecast|outlook)\b")
_WX_RAIN = re.compile(
    r"\b(will it rain|is it going to rain|gonna rain|is it raining|does it rain"
    r"|chance of rain|rain today|any rain|umbrella|will it snow|is it snowing)\b")
_WX_CURRENT = re.compile(
    r"\b(weather|temperature|how (?:hot|cold|warm) is it|how (?:hot|cold) is"
    r"|humidity|wind speed|how windy)\b")

# A leading tool verb wins: "create a pdf about weather patterns" and "play rain
# sounds" both contain weather words and neither is a weather question.
_WX_NOT = re.compile(
    r"^(?:ron[,\s]+)?(?:hey\s+)?(?:please\s+)?(?:can you\s+|could you\s+)?"
    r"(play|open|launch|start|run|visit|browse|navigate|create|make|write"
    r"|generate|download|install|search for|google)\b")
# Machine telemetry, not the sky. The HUD already reports these.
_WX_HARDWARE = re.compile(r"\b(cpu|gpu|processor|disk|ram|drive|fan|battery)\b")

_WX_PLACE = re.compile(r"\b(?:in|at|for|of)\s+([a-z][a-z ,.'\-]{1,40}?)"
                       r"\s*(?:[?.!]|$|\btoday\b|\btomorrow\b|\btonight\b"
                       r"|\bthis week\b|\bright now\b|\bnow\b)")
# Words that can be captured by the pattern above but are never a place --
# "chance of rain today" would otherwise try to geocode a city called "rain".
_WX_NOT_PLACE = re.compile(
    r"\b(rain|raining|snow|snowing|weather|temperature|forecast|outlook|humidity"
    r"|wind|windy|umbrella|jacket|today|tomorrow|tonight|week|weekend|day|days"
    r"|hour|hours|now|morning|afternoon|evening|night|month|home|outside|me|us"
    r"|you|it|that|this|here|there|long)\b")


def _extract_place(text: str):
    """A trailing 'in <place>' / 'for <place>', or None for the home location."""
    match = _WX_PLACE.search(text)
    if not match:
        return None
    place = match.group(1).strip(" ,.'-")
    if place.startswith("the "):
        place = place[4:].strip()
    if len(place) < 2 or _WX_NOT_PLACE.search(place):
        return None
    return place


def extract_weather(command: str):
    """Classify a weather question, or return None if it is not one.

    Returns {"kind": "current"|"rain"|"forecast", "location": None|str,
             "when": "tomorrow"|"week"}. `location` of None means the configured
    home location.
    """
    text = (command or "").lower().strip()
    if not text or _WX_NOT.search(text) or _WX_HARDWARE.search(text):
        return None

    forecast = _WX_FORECAST.search(text)
    rain = _WX_RAIN.search(text)
    if not (forecast or rain or _WX_CURRENT.search(text)):
        return None

    ahead = _WX_FORECAST_WHEN.search(text)
    if forecast or ahead:
        kind = "forecast"
        when = "tomorrow" if (ahead and ahead.group(1) in ("tomorrow", "tonight")) else "week"
    elif rain:
        kind, when = "rain", "week"
    else:
        kind, when = "current", "week"

    return {"kind": kind, "location": _extract_place(text), "when": when}


def weather_reply(intent: dict) -> str:
    """The spoken answer for an intent. One code path for both routes into it."""
    kind = intent.get("kind") or "current"
    where = intent.get("location") or None
    if kind == "rain":
        return weather.rain_answer(where)
    if kind == "forecast":
        return weather.forecast_answer(where, intent.get("when") or "week")
    return weather.describe(where)


def handle_get_weather(data: dict) -> str:
    """The LLM's fallback route, for phrasings extract_weather does not catch."""
    query = str(data.get("query") or "current").strip().lower()
    location = str(data.get("location") or "").strip()
    return weather_reply({
        "kind": query if query in ("current", "rain", "forecast") else "current",
        "location": location or None,
        "when": str(data.get("when") or "week").strip().lower(),
    })


# --- Date and time ---------------------------------------------------------
# Answered directly for the same reasons as weather: instant, no tokens, and it
# still works with a rejected key. clock.py does the calendar arithmetic.

# A clock or calendar noun has to be present before anything here can fire. Two
# word boundaries are doing quiet work: \btime\b does not match "uptime", and
# \bday\b does not match "today" -- which is how "what is the uptime" and "will
# it rain today" stay out of this route entirely.
_DT_TIME_WORD = re.compile(r"\b(time|clock|o'? ?clock)\b")
_DT_DATE_WORD = re.compile(r"\b(date|day|month|year|calendar|calender)\b")

# Asking the machine, rather than mentioning time in passing. "I have no time for
# this" carries the noun but none of these.
_DT_ASK = re.compile(
    r"\b(what|whats|what's|which|tell|give|say|show|read|current|currently"
    r"|present|exact|now|today|todays|today's|is it|do you know)\b")

# The same leading-verb veto as weather: "play time after time", "create a pdf
# about the Bangla calendar", "set a reminder for one day".
_DT_NOT = re.compile(
    r"^(?:ron[,\s]+)?(?:hey\s+)?(?:please\s+)?(?:can you\s+|could you\s+)?"
    r"(play|open|launch|start|run|visit|browse|navigate|create|make|write"
    r"|generate|download|install|search for|google|set|remind|schedule|add)\b")

# Phrases that own the noun for something else. "what time is the meeting" is a
# question about a calendar entry, not about the clock.
_DT_NOT_PHRASE = re.compile(
    r"\b(what time (?:does|do|did|will|would|should|are|is the)\b"
    r"|how (?:much|many|long) time|time (?:for|to)\b"
    r"|all day|every ?day|one day|some day|day off|the other day"
    r"|up to date|out of date|due date|expiry date|release date"
    r"|in time|on time|at the same time|waste of time)")

# Which calendar was named. Checked most-specific first, because "in english,
# bangla and arabic" names two of them and means all three.
_DT_ALL = re.compile(
    r"\b(all (?:three |the )?(?:calendars?|systems?|dates?)|every calendar"
    r"|in all calendars?|all of them|three calendars?"
    r"|english,? bangla,? and arabic|bangla and arabic)\b")
_DT_BANGLA = re.compile(
    r"\b(bangla|bangladeshi?|bengali|bangali|bangabda|bongabdo|bongabda"
    r"|boishakh|bangla calendar)\b")
_DT_ARABIC = re.compile(
    r"\b(arabic|arabi|hijri|hijrah|hijra|islamic|islami|muslim|lunar"
    r"|ramadan|ramzan|ramadhan)\b")


def _dt_system(text: str) -> str:
    """Which calendar the question asked for. English unless another is named."""
    if _DT_ALL.search(text):
        return "all"
    if _DT_BANGLA.search(text):
        return "bangla"
    if _DT_ARABIC.search(text):
        return "arabic"
    return "english"


def extract_datetime(command: str):
    """Classify a clock or calendar question, or return None if it is not one.

    Returns {"kind": "time"|"date", "system": "english"|"bangla"|"arabic"|"all"}.
    """
    text = (command or "").lower().strip()
    if not text or _DT_NOT.search(text) or _DT_NOT_PHRASE.search(text):
        return None

    asked_time = _DT_TIME_WORD.search(text)
    asked_date = _DT_DATE_WORD.search(text)
    if not (asked_time or asked_date) or not _DT_ASK.search(text):
        return None

    # "the time and date" asks for both -- which is exactly what the time answer
    # already gives, so the time branch wins whenever both nouns appear.
    return {"kind": "time" if asked_time else "date",
            "system": _dt_system(text)}


def datetime_reply(intent: dict) -> str:
    """The spoken answer for an intent. One code path for both routes into it."""
    return clock.answer(intent.get("kind") or "time",
                        intent.get("system") or "english")


def handle_get_datetime(data: dict) -> str:
    """The LLM's fallback route, for phrasings extract_datetime does not catch."""
    query = str(data.get("query") or "time").strip().lower()
    system = str(data.get("calendar") or data.get("system") or "english").strip().lower()
    return datetime_reply({
        "kind": "date" if query.startswith("date") else "time",
        "system": system if system in clock.SYSTEMS + ("all",) else "english",
    })


# --- File search -----------------------------------------------------------
# Answered directly, like weather and the clock: the disk walk needs no LLM, so
# it costs no tokens and works with a rejected key. finder.py does the traversal
# and owns every field name; this layer only turns a phrase into a query + mode
# and drives the cinematic HUD overlay through bus.search().

# A search verb has to lead the sentence. Anchoring at the start keeps "I can't
# find my glasses, what's the weather" out (and weather is checked first anyway).
_FIND_TRIGGER = re.compile(
    r"^(?:ron[,\s]+)?(?:hey\s+)?(?:please\s+)?(?:can you\s+|could you\s+)?"
    r"(?:go(?:ing)?\s+(?:and\s+)?)?"
    r"(find|search for|search|locate|look for|hunt (?:for|down)|dig up"
    r"|where(?:'s| is| are|s)?)\s+")

# A search aimed at a service, not the disk. "search youtube for believer" and
# "look it up online" are not file searches; the play/website routes own those.
_FIND_NOT = re.compile(
    r"\b(youtube|spotify|netflix|google|bing|online|the (?:web|internet)"
    r"|internet|web search|on the web)\b")

# Noise words that survive the trigger but are never part of the target name.
_FIND_FILLER = re.compile(
    r"\b(my|the|a|an|all|any|some|every|for|me|us|please|named|called|file"
    r"|files|somewhere|anywhere|out|it|them|those)\b")
_FIND_ON_DISK = re.compile(
    r"\bon\s+(?:my\s+)?(?:computer|pc|laptop|desktop|disk|drives?|system"
    r"|machine|hard\s*drive|ssd)\b")
_FIND_FOLDER = re.compile(r"\b(folders?|directory|directories)\b")
_FIND_EXT_STAR = re.compile(r"(?:^|\s)\*?\.([a-z0-9]{1,5})\b")
_FIND_EXT_WORD = re.compile(r"\b([a-z0-9]{1,5})\s+files?\b")
# Determiners that precede "file(s)" but are not a file type: "a file", "the
# files". Without this "find a file called X" reads "a" as the extension.
_FIND_NOT_EXT = frozenset({"a", "an", "the", "my", "your", "our", "some",
                           "any", "all", "one", "no", "this", "that", "these"})


def extract_find(command: str):
    """Classify a file/folder search, or return None if it is not one.

    Returns {"query": str, "mode": "keyword"|"filename"|"extension"|"folder"}.
    """
    text = (command or "").lower().strip()
    if not text:
        return None
    trigger = _FIND_TRIGGER.match(text)
    if not trigger or _FIND_NOT.search(text):
        return None

    rest = text[trigger.end():]
    rest = _FIND_ON_DISK.sub(" ", rest)

    mode = "keyword"
    if _FIND_FOLDER.search(rest):
        mode = "folder"
        rest = _FIND_FOLDER.sub(" ", rest)

    # Strip filler before working out the needle, so "all pdf files" -> "pdf".
    cleaned = _FIND_FILLER.sub(" ", rest)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.?!'\"-")

    if mode != "folder":
        star = _FIND_EXT_STAR.search(rest)
        word = _FIND_EXT_WORD.search(rest)
        if star:
            mode, cleaned = "extension", star.group(1)
        elif word and word.group(1) not in _FIND_NOT_EXT and (
                word.group(1) in finder._EXT_WORDS or word.group(1).isalpha()):
            mode, cleaned = "extension", word.group(1)
        elif " " not in cleaned and "." in cleaned and not cleaned.endswith("."):
            mode = "filename"

    if not cleaned or len(cleaned) < 2:
        return None
    return {"query": cleaned, "mode": mode}


def run_find(spec: dict) -> str:
    """Shared search path for both routes: drive the HUD, return the spoken line.

    Publishes a `scanning` frame immediately, streams throttled progress while
    finder walks the disk, then publishes the finished payload. Wrapped whole so
    a fault in the walk can never break the command -- it apologises instead.
    """
    query = (spec.get("query") or "").strip()
    mode = spec.get("mode") or "auto"
    try:
        bus.search(status="scanning", ok=True, query=query, mode=mode,
                   scanned=0, count=0)

        last = [0.0]

        def progress(scanned, current):
            now = time.time()
            if now - last[0] >= 0.2:          # throttle the SSE fan-out
                last[0] = now
                bus.search(status="scanning", ok=True, query=query, mode=mode,
                           scanned=scanned, count=0, current=current)

        result = finder.find(query, mode=mode, progress=progress)
        bus.search(**finder.hud_payload(result))
        return finder.describe(result)
    except Exception as e:
        bus.search(status="error", ok=False, query=query,
                   error=e.__class__.__name__)
        return "I ran into a problem searching your disk, Sir."


def handle_find(data: dict) -> str:
    """The LLM's fallback route, for phrasings extract_find does not catch."""
    query = str(data.get("query") or "").strip()
    if not query:
        return "I need something to search for, Sir."
    mode = str(data.get("mode") or "auto").strip().lower()
    if mode not in ("keyword", "filename", "extension", "folder", "auto"):
        mode = "auto"
    return run_find({"query": query, "mode": mode})


# --- Internet speed test ---------------------------------------------------
# Answered directly, like weather and the clock: a network measurement needs no
# LLM, costs no tokens, and works with a rejected key. netspeed.py does the
# timing and owns every field name; this layer only recognises the trigger
# phrases and drives the cinematic HUD overlay through bus.netspeed().

_SPEED_TRIGGER = re.compile(
    r"\b(internet\s*(speed|connection|connectivity)|connection\s*speed"
    r"|network\s*(speed|connectivity)|bandwidth|link\s*speed|how fast (?:is|'s) my"
    r"\s*(internet|connection|network)|speed\s*test|ping\s*my\s*(internet|connection)"
    r"|what about my internet|check my (?:internet|network|connection)"
    r"\s*(speed|performance))\b")


def extract_speed(command: str):
    """Classify a speed-test request, or return None if it is not one.

    Returns {"kind": "speed"} when the command asks about the user's own
    internet/connection speed. A single hit is enough -- there is only one mode.
    """
    text = (command or "").lower().strip()
    if not text:
        return None
    # Vague "speed" alone is not a speed test -- "speed up my PC" is not this.
    if not _SPEED_TRIGGER.search(text):
        return None
    return {"kind": "speed"}


def run_speed(_spec: dict) -> str:
    """Shared path for both routes: drive the HUD, return the spoken line.

    Publishes a `scanning` frame the moment the test starts, then the finished
    payload. Wrapped whole so a fault in the measurement can never break the
    command -- it apologises instead.
    """
    try:
        bus.netspeed(status="scanning", ok=True,
                     ping_ms=None, download_mbps=None, upload_mbps=None,
                     phase="PING", phase_done=0, phase_total=4)

        def _progress(phase, done, total):
            bus.netspeed(status="scanning", ok=True,
                         ping_ms=None, download_mbps=None, upload_mbps=None,
                         phase=phase.upper(), phase_done=int(done), phase_total=int(total))

        result = netspeed.run(progress=_progress)
        bus.netspeed(**netspeed.hud_payload(result))
        return netspeed.describe(result)
    except Exception as e:
        bus.netspeed(status="error", ok=False, error=e.__class__.__name__)
        return "I ran into a problem testing your internet speed, Sir."


def handle_get_internet_speed(data: dict) -> str:
    """The LLM's fallback route, for phrasings extract_speed does not catch."""
    return run_speed({"kind": "speed"})


# --- Timer -----------------------------------------------------------------
# Answered directly, like the speed test: a countdown needs no LLM, costs no
# tokens, and works with a rejected key. timer.py owns the timing thread and
# every field name; this layer only recognises the trigger phrases and drives
# the cinematic HUD overlay through bus.timer().

_TIMER_TRIGGER = re.compile(
    r"\b(timer|countdown|alarm|remind(er| me)?|notify me)\b.*"
    r"\b(\d+)\s*(sec(?:ond)?s?|min(?:ute)?s?|hrs?|hours?|h|m|s)\b"
    r"|\b(set|start)\s+(a\s+)?(\d+)\s*(sec(?:ond)?s?|min(?:ute)?s?|hrs?|hours?)"
    r"|\b(\d+)\s*(sec(?:ond)?s?|min(?:ute)?s?|hrs?|hours?)\s*(timer|countdown)\b")


def extract_timer(command: str):
    """Classify a timer request, returning {"duration_sec": N} or None."""
    text = (command or "").lower().strip()
    if not text:
        return None
    m = _TIMER_TRIGGER.search(text)
    if not m:
        return None
    g = m.groups()
    # The regex has three alternatives, each with its own capture slots.
    # Alt 1 (timerword ... N unit):  number=g[2], unit=g[3]
    # Alt 2 (set/start ... N unit):  number=g[6], unit=g[7]
    # Alt 3 (N unit timer/countdown): number=g[8], unit=g[9]
    if g[2] is not None and g[3] is not None:
        number, unit = g[2], g[3]
    elif g[6] is not None and g[7] is not None:
        number, unit = g[6], g[7]
    elif g[8] is not None and g[9] is not None:
        number, unit = g[8], g[9]
    else:
        return None
    unit = (unit or "").strip()
    try:
        n = int(number)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    if unit in ("s", "sec", "secs", "second", "seconds"):
        secs = n
    elif unit in ("m", "min", "mins", "minute", "minutes"):
        secs = n * 60
    elif unit in ("h", "hr", "hrs", "hour", "hours"):
        secs = n * 3600
    else:
        return None
    # Cap at 24 hours so a misheard number does not pin the HUD forever.
    return {"duration_sec": min(secs, 86400)}


def run_timer(spec: dict) -> str:
    """Shared path for both routes: drive the HUD, return the spoken line.

    Publishes a `set` frame the moment the timer arms, then `running` ticks,
    then a terminal `done` frame. If a timer is already running it is cancelled
    first so only one countdown is live at a time. Wrapped whole so a fault can
    never break the command -- it apologises instead.
    """
    global _active_timer
    secs = int(spec.get("duration_sec", 0))
    try:
        # Cancel the previous timer so there is only ever one live countdown.
        if _active_timer is not None:
            timer.cancel(_active_timer)
        _active_timer = timer.run(secs)
        bus.timer(**timer.hud_payload(_active_timer))
        return timer.describe(_active_timer)
    except Exception:
        bus.timer(status="error", ok=False, error="timer fault")
        return "I ran into a problem setting the timer, Sir."


def handle_set_timer(data: dict) -> str:
    """The LLM's fallback route, for phrasings extract_timer does not catch."""
    dur = str(data.get("duration") or "").strip()
    # Reuse extract_timer's parser on the LLM-normalised duration string.
    spec = extract_timer(f"set a timer for {dur}")
    if not spec:
        return "I could not parse a duration from that, Sir. Try '2 minutes'."
    return run_timer(spec)


_active_timer = None   # the live timer spec, if any; cancelled on the next run


def _timer_label(spec: dict) -> str:
    """Short human label for the HUD detail line, e.g. '2m'."""
    secs = int(spec.get("duration_sec", 0))
    if secs >= 3600 and secs % 3600 == 0:
        return f"{secs // 3600}h"
    if secs >= 60 and secs % 60 == 0:
        return f"{secs // 60}m"
    if secs < 60:
        return f"{secs}s"
    h = secs // 3600
    m = (secs % 3600) // 60
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------

_VOLUME_TRIGGER = re.compile(
    r"""(?xi)
    ^(?:.*?\b)?
    (?:
        (?:volume|vol|speaker)\s*(?:control|level)?
        |(?:set|change|adjust|turn)\s+(?:the\s+)?(?:volume|vol|speaker)
        |mute|unmute
    )
    .*$
    """,
)

_VOLUME_ACTIONS = {
    "mute": "mute",
    "unmute": "unmute",
    "toggle": "toggle",
    "toggle mute": "toggle",
}


def extract_volume(user_input: str) -> dict | None:
    """Parse a volume command into an action spec, or None.

    Recognised forms:
      - ``set volume to N`` / ``volume N`` / ``volume to N``
      - ``volume up`` / ``volume down``
      - ``volume up by N`` / ``volume down N``
      - ``mute`` / ``unmute`` / ``toggle mute``
    """
    text = (user_input or "").strip()
    if not text:
        return None

    lower = text.lower()

    # Mute / unmute / toggle mute -- exact-ish.
    if re.search(r"\btoggle\s+mute\b", lower):
        return {"action": "toggle"}
    if re.search(r"\bunmute\b", lower) and not re.search(r"\bunmute\s+(?!$)", lower):
        return {"action": "unmute"}
    if lower == "mute" or re.search(r"\bmute\s+(?:the\s+)?(?:speakers|volume|audio)\b", lower):
        return {"action": "mute"}

    # Absolute: "set volume to 50", "volume 50", "volume to 75%".
    abs_m = re.search(
        r"(?:volume|vol)\s*(?:to|at|=)?\s*(\d{1,3})\s*%?", lower)
    if abs_m:
        level = int(abs_m.group(1))
        if 0 <= level <= 100:
            return {"action": "set", "level": level}

    # Relative: "volume up [by N]", "volume down N", "turn it up/down".
    rel_m = re.search(r"(?:volume|turn\s+it|it)\s+(up|down)(?:\s+(?:by\s+)?(\d{1,3}))?", lower)
    if rel_m:
        direction = 1 if rel_m.group(1) == "up" else -1
        delta = int(rel_m.group(2)) if rel_m.group(2) else 10
        return {"action": "step", "delta": direction * min(delta, 100)}

    return None


def run_volume(spec: dict) -> str:
    """Execute a volume action and return the spoken confirmation.

    Publishes the resulting level/mute state to the bus so the HUD can show it.
    """
    action = str(spec.get("action", "")).lower()
    level = None
    muted = None
    try:
        if action == "set":
            lvl = int(spec.get("level", 50))
            ok = volume.set_level(lvl)
            level = round(lvl, 1) if ok else None
            muted = volume.is_muted()
        elif action == "mute":
            ok = volume.mute()
            level = volume.get_level()
            muted = True if ok else None
        elif action == "unmute":
            ok = volume.unmute()
            level = volume.get_level()
            muted = False if ok else None
        elif action == "toggle":
            new_muted = volume.toggle_mute()
            muted = new_muted
            level = volume.get_level()
        elif action == "step":
            delta = int(spec.get("delta", 10))
            level = volume.step(delta)
            muted = volume.is_muted()
        else:
            return "I did not understand that volume command, Sir."
    except Exception:
        ok = False

    if level is None:
        bus.volume(status="error", ok=False, error="volume fault")
        return "I could not change the volume, Sir."

    bus.volume(**volume.hud_payload(action, level, muted))
    return volume.describe(action, level, muted)


def handle_set_volume(data: dict) -> str:
    """The LLM's fallback route for volume commands."""
    action = str(data.get("action") or "set").lower()
    spec = {"action": action}
    if "level" in data:
        spec["level"] = int(data["level"])
    if "delta" in data:
        spec["delta"] = int(data["delta"])
    return run_volume(spec)


def write_document(topic: str) -> str:
    """Second LLM call: a dedicated writer turn that produces the full document.

    The assistant persona is told to stay terse, so asking it to inline a whole
    report into a JSON field yields a stub. This call has its own prompt and no
    conversation history, so length is governed only by the writer instructions.
    """
    print(f"[Writing document on '{topic}' -- 1200-2000 words, this usually "
          f"takes 30-90 seconds...]")
    bus.set_state(bus.THINKING, f"COMPOSING DOCUMENT · {topic.upper()[:48]}")
    bus.activity(f"Writing document: {topic}", "pending")
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": DOCUMENT_WRITER_PROMPT},
            {"role": "user", "content": f"Write the full document about: {topic}"},
        ],
        temperature=0.7,
        max_tokens=6000,
        # Without this the SDK waits its 600s default, so a stalled request looks
        # like Ron has simply died. Long documents legitimately need a few minutes.
        timeout=180,
    )
    choice = response.choices[0]
    content = (choice.message.content or "").strip()

    # Some models still wrap the whole thing in a code fence despite the prompt.
    if content.startswith("```"):
        parts = content.split("```")
        if len(parts) >= 2:
            content = parts[1]
            if content.lower().startswith("markdown"):
                content = content[8:]
            elif content.lower().startswith("md"):
                content = content[2:]
            content = content.strip()

    # 2000 words of Markdown can brush the token ceiling. If it did, the text
    # stops mid-sentence — trim back to the last complete paragraph so the PDF
    # does not end on half a word.
    if getattr(choice, "finish_reason", None) == "length":
        cut = max(content.rfind("\n\n"), content.rfind(". "))
        if cut > len(content) * 0.6:
            content = content[:cut + 1].rstrip()
        print("[Document hit the token ceiling; trimmed to the last complete section.]")

    return content


def handle_generate_pdf(data: dict) -> str:
    """Two-stage PDF: the assistant gives intent, a writer turn gives the body."""
    topic = (data.get("topic") or data.get("subject") or data.get("about") or "").strip()
    content = (data.get("content") or "").strip()
    file_name = (data.get("file_name") or data.get("filename") or "").strip()

    # A placeholder like "..." is not real content — this was the original bug.
    if not re.sub(r"[\s.…\-_*#]", "", content):
        content = ""

    # Use the model's text verbatim only when it dictated exact text (no topic
    # given) or already wrote a full document. Otherwise write it properly.
    verbatim = bool(content) and (not topic or len(content) >= 200)

    if not verbatim:
        if not topic:
            topic = (file_name.replace("_", " ").replace("-", " ").strip()
                     or content or "the requested subject")
        speak(f"Writing your document on {topic}, Sir. Give me a moment.")
        try:
            content = write_document(topic)
        except Exception as e:
            print(f"[Document generation failed: {e}]")
            return f"I could not write the document, Sir. {e}"

        if len(content) < 200:
            return "The document came back too short, so I did not save it. Please try again, Sir."

    if not file_name:
        file_name = re.sub(r"[^\w\s-]", "", topic or "document").strip()
        file_name = re.sub(r"\s+", "_", file_name).lower()[:60]

    title = topic.title() if topic else file_name.replace("_", " ").title()
    bus.set_state(bus.EXECUTING, "RENDERING PDF")
    bus.activity(f"Rendering PDF: {file_name}.pdf", "pending")
    result = generate_pdf(file_name, content, title=title)
    bus.activity("Document saved to Documents", "ok")
    return result


def process_command(user_input: str):
    """Public entry point. Serialised so a typed command and the mic loop cannot
    interleave two LLM turns (and two SAPI calls) on top of each other."""
    with command_lock:
        try:
            return _process_command(user_input)
        except Exception as e:
            import traceback
            traceback.print_exc()
            bus.set_state(bus.ERROR, str(e)[:160])
            bus.activity(f"Command failed: {e}", "fail")
            speak(f"That command failed, Sir. {e}")


def _process_command(user_input: str):
    bus.transcript("user", user_input)
    bus.activity("Command received", "ok")

    # Weather, answered without an LLM turn. Checked first, which is safe in both
    # directions: every weather pattern needs a weather noun, while extract_folder
    # needs a _FOLDER_ALIASES hit and extract_website needs a WEBSITE_KEYWORDS
    # prefix or "open " -- so "what's the weather" cannot reach them and "open
    # documents" cannot reach this. test_weather.py pins that both ways.
    intent = extract_weather(user_input)
    if intent:
        bus.set_state(bus.EXECUTING, "WEATHER · " + intent["kind"].upper())
        bus.activity("Fetching weather", "pending")
        reply = weather_reply(intent)
        payload = weather.hud_payload(intent["location"])
        bus.weather(**payload)
        ok = bool(payload.get("ok"))
        bus.activity("Weather retrieved" if ok else "Weather unavailable",
                     "ok" if ok else "fail")
        speak(reply)
        return

    # The clock and the calendar, on the same terms as weather. Deliberately
    # placed after it: a phrase carrying both nouns -- "what's the temperature
    # today" -- is a weather question, and letting weather answer first is how
    # that stays true without either pattern having to know about the other.
    when = extract_datetime(user_input)
    if when:
        bus.set_state(bus.EXECUTING, "CLOCK · " + when["kind"].upper())
        reply = datetime_reply(when)
        bus.activity(f"Clock read ({when['system']})", "ok")
        speak(reply)
        return

    # Disk search, answered directly. Its trigger verbs (find/search/locate/
    # where is) don't overlap with the folder ("open ...") or website routes, and
    # a service search ("search youtube for ...") is vetoed inside extract_find,
    # so this can sit ahead of both. run_find drives the cinematic HUD overlay.
    hunt = extract_find(user_input)
    if hunt:
        bus.set_state(bus.EXECUTING, "FILE SEARCH · " + hunt["query"].upper()[:40])
        bus.activity(f"Searching disk for {hunt['query']}", "pending")
        reply = run_find(hunt)
        bus.activity("Search complete", "ok")
        speak(reply)
        return

    # Handle folder opening directly
    path = extract_folder(user_input)
    if path:
        bus.set_state(bus.EXECUTING, f"OPEN FOLDER · {path}")
        bus.activity(f"Opening folder {path}", "pending")
        result = open_folder(path)
        bus.activity("Folder opened", "ok")
        speak(result)
        return

    # Handle website visits directly without going through AI
    site = extract_website(user_input)
    if site:
        bus.set_state(bus.EXECUTING, f"OPEN URL · {site[:60]}")
        bus.activity(f"Opening {site}", "pending")
        result = open_website(site)
        bus.activity("Browser launched", "ok")
        speak(result)
        return

    # Internet speed test, answered directly. The trigger verbs (speed / how fast
    # is my internet / what about my internet) do not overlap with any other
    # route, so this sits safely ahead of the LLM. run_speed drives the cinematic
    # HUD overlay through bus.netspeed().
    speed = extract_speed(user_input)
    if speed:
        bus.set_state(bus.EXECUTING, "INTERNET SPEED TEST")
        bus.activity("Measuring internet speed", "pending")
        reply = run_speed(speed)
        bus.activity("Speed test complete", "ok")
        speak(reply)
        return

    # Timer / countdown, answered directly. The trigger verbs (timer / countdown /
    # set a timer for N minutes) do not overlap with any other route, so this sits
    # safely ahead of the LLM. run_timer drives the cinematic HUD overlay through
    # bus.timer() and keeps a live countdown on a background thread.
    tm = extract_timer(user_input)
    if tm:
        bus.set_state(bus.EXECUTING, f"TIMER · {_timer_label(tm)}")
        bus.activity(f"Setting timer for {_timer_label(tm)}", "pending")
        reply = run_timer(tm)
        bus.activity("Timer armed", "ok")
        speak(reply)
        return

    # Volume control, answered directly. The verbs (volume / mute / unmute /
    # turn it up) do not overlap with any other route, so this sits safely ahead
    # of the LLM. run_volume changes the system master volume and publishes the
    # resulting level/mute state through bus.volume() for the HUD overlay.
    vm = extract_volume(user_input)
    if vm:
        action = vm.get("action", "")
        label = {
            "set": f"{vm.get('level', '?')}%",
            "step": f"{'+' if vm.get('delta', 0) > 0 else ''}{vm.get('delta', 0)}",
            "mute": "mute",
            "unmute": "unmute",
            "toggle": "toggle",
        }.get(action, action)
        bus.set_state(bus.EXECUTING, f"VOLUME · {label.upper()}")
        bus.activity(f"Changing volume: {label}", "pending")
        reply = run_volume(vm)
        bus.activity("Volume changed", "ok")
        speak(reply)
        return

    conversation_history.append({"role": "user", "content": user_input})
    _trim_context()   # bound the request we are about to send

    try:
        bus.set_state(bus.THINKING, f"QUERYING {MODEL.upper()}")
        bus.activity("Reasoning over request", "pending")
        response = client.chat.completions.create(
            model=MODEL,
            messages=conversation_history,
            temperature=0.7,
            timeout=60
        )
        reply = response.choices[0].message.content.strip()
        print(f"[DEBUG API Response] Model: {response.model}")
        bus.meta(model=response.model or MODEL, api_ok=True)
        bus.activity("AI response generated", "ok")
    except Exception as e:
        error_msg = str(e)
        # OpenAI-style errors carry the useful text in .body['message']
        if hasattr(e, 'body') and isinstance(e.body, dict):
            error_msg = e.body.get('message', error_msg)
        status = getattr(e, "status_code", None)
        low = error_msg.lower()
        print(f"[API Error: {error_msg}]")
        bus.meta(api_ok=False)
        bus.activity(f"API error: {error_msg}", "fail")
        bus.set_state(bus.ERROR, error_msg[:160])

        # Order matters here. "Invalid token" means the API *key* was rejected, but
        # it contains the substring "token" -- so a quota check that greps for
        # "token" first will blame the monthly allowance and send you hunting a
        # billing problem when the real fix is pasting a valid key.
        if (status in (401, 403) or "invalid token" in low or "unauthorized" in low
                or "invalid api key" in low or "authentication" in low):
            speak("My API key was rejected, Sir. Please check the key in main.py.")
        elif ("quota" in low or "balance" in low or "insufficient" in low
                or "exceeded" in low or "每月" in low):
            speak("invalid token。")
        else:
            speak(f"error: {error_msg}")
        return

    conversation_history.append({"role": "assistant", "content": reply})
    _trim_context()

    # Try to parse as tool call
    try:
        # Extract JSON if wrapped in markdown code block
        if "```" in reply:
            reply_clean = reply.split("```")[1].strip()
            if reply_clean.startswith("json"):
                reply_clean = reply_clean[4:].strip()
        else:
            reply_clean = reply

        data = json.loads(reply_clean)
        tool = data.get("tool")
        bus.set_state(bus.EXECUTING, (tool or "UNKNOWN TOOL").upper())
        bus.activity(f"Tool call: {tool}", "pending")

        if tool == "play_youtube":
            result = play_youtube(data["search_query"])
        elif tool == "generate_pdf":
            result = handle_generate_pdf(data)
        elif tool == "open_app":
            result = open_app(data["app_name"])
        elif tool == "open_website":
            result = open_website(data["url"])
        elif tool == "get_weather":
            result = handle_get_weather(data)
            bus.weather(**weather.hud_payload(
                str(data.get("location") or "").strip() or None))
        elif tool == "get_datetime":
            result = handle_get_datetime(data)
        elif tool == "find_files":
            # run_find already published the HUD overlay frames itself.
            result = handle_find(data)
        elif tool == "get_internet_speed":
            result = handle_get_internet_speed(data)
        elif tool == "set_timer":
            result = handle_set_timer(data)
        elif tool == "set_volume":
            result = handle_set_volume(data)
        else:
            result = "Unknown tool requested."

        bus.activity(f"Tool executed: {tool}", "ok" if tool else "fail")
        speak(result)

    except (json.JSONDecodeError, KeyError):
        # Not a tool call -- an ordinary spoken reply.
        speak(reply)
    except Exception as e:
        # A tool failure must never take the assistant down with it.
        import traceback
        traceback.print_exc()
        bus.activity(f"Tool failed: {e}", "fail")
        bus.set_state(bus.ERROR, str(e)[:160])
        speak(f"That command failed, Sir. {e}")

def run_voice_loop(greet=True):
    """The always-on listening loop.

    Split out of main() so the HUD server can run it on a background thread while
    it serves the interface. Honours `voice_enabled` (mic muted from the HUD) and
    `shutdown_event` (quit requested from either front end).
    """
    if greet:
        speak("Ron is online. How can I help you, Sir?")
    silent_rounds = 0
    while not shutdown_event.is_set():
        try:
            if not voice_enabled.is_set():
                # Muted: hold the mic closed so another app can use it, and check
                # back often enough that un-muting feels immediate.
                bus.set_state(bus.IDLE, "MICROPHONE MUTED")
                voice_enabled.wait(0.4)
                continue

            command = ""
            for _ in range(3):
                if shutdown_event.is_set() or not voice_enabled.is_set():
                    break
                command = listen(timeout=8, phrase_limit=15)
                if command:
                    break
            if shutdown_event.is_set():
                break
            if not command:
                # Looping here in silence gives no hint that the microphone is the
                # problem, which just reads as "Ron is broken". Say so periodically.
                silent_rounds += 1
                if silent_rounds >= 3:
                    print('[Nothing recognised in 9 attempts. Check MIC_INDEX in '
                          'voice.py -- run "python test_mic.py" to list devices. '
                          'To skip the mic entirely: python main.py "your command"]')
                    bus.activity("No speech detected in 9 attempts", "fail")
                    bus.meta(mic_ok=False)
                    speak("I cannot hear anything, Sir. Please check the microphone.")
                    silent_rounds = 0
                continue
            silent_rounds = 0
            if any(word in command for word in ["goodbye", "shut down", "exit", "sleep"]):
                bus.transcript("user", command)
                speak("Going offline, Sir. Goodbye.")
                shutdown_event.set()
                break
            process_command(command)
        except KeyboardInterrupt:
            # Ctrl+C normally lands inside the blocking mic read. A second one can
            # land inside the blocking SAPI call below -- and an exception raised in
            # an except block is not caught by its sibling except clauses, so
            # without this guard it escapes main() and crashes on the way out.
            shutdown_event.set()
            try:
                speak("Shutting down. Goodbye, Sir.")
            except KeyboardInterrupt:
                print("\n[Interrupted]")
            break
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[Loop error: {e}]")
            bus.activity(f"Loop error: {e}", "fail")
            bus.set_state(bus.ERROR, str(e)[:160])
            speak("Something went wrong, Sir. Ready for your next command.")
    bus.set_state(bus.OFFLINE, "SESSION ENDED")


def main():
    run_voice_loop()

if __name__ == "__main__":
    try:
        if len(sys.argv) > 1:
            # Text mode, for testing without a microphone:
            #   python main.py "create a pdf about black holes"
            # listen() lowercases what it hears, so match that here.
            process_command(" ".join(sys.argv[1:]).strip().lower())
        else:
            main()
    except KeyboardInterrupt:
        print("\n[Interrupted - exiting.]")
    finally:
        # Stamps ended_at on the session row. Whoever owns the process owns this
        # call -- ui_server.py makes it when the HUD is the front end, so
        # run_voice_loop() deliberately does not close a database it may be
        # sharing with a still-running HUD.
        history.close()
