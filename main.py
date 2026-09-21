import json
import os
import re
import sys
import threading
import time
from openai import OpenAI
import bus
import clock
import email_notify
import finder
import history
import netspeed
import timer
import volume
import weather
import browser_agent
import protocol
import briefing
import researcher
import autopilot
import memory
import coach
import intel
import tribune
import telegram_bridge
import netradar
import clean_slate
import docintel
import voice
from voice import speak, listen
from tools import (play_youtube, generate_pdf, generate_webpage, open_app,
                   open_website, open_folder, resolve_user_folder, _FOLDER_ALIASES)
from imagegen import generate_image

# Fix encoding for Windows console and handle pythonw silent mode
if sys.platform == 'win32':
    import io
    import ctypes

    # Auto-hide console window if running silently
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd and os.environ.get("RON_SHOW_CONSOLE", "0") != "1" and len(sys.argv) <= 1:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # 0 = SW_HIDE
    except Exception:
        pass

    if sys.stdout is not None:
        try:
            sys.stdout.flush()
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                                          errors='replace', line_buffering=True)
        except Exception:
            pass
    else:
        # pythonw has no stdout; redirect to session log file
        try:
            log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
            os.makedirs(log_dir, exist_ok=True)
            log_file = os.path.join(log_dir, "ron_session.log")
            sys.stdout = open(log_file, "a", encoding="utf-8", buffering=1)
        except Exception:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")

    if sys.stderr is None:
        try:
            sys.stderr = sys.stdout
        except Exception:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")

# Use OpenAI-compatible API from hcnsec.cn. The SDK appends /chat/completions to
# base_url, so the /v1 belongs here: api.hcnsec.cn/v1/chat/completions is the path
# confirmed working against this host (see test_output.txt -- status 200).
client = OpenAI(
    api_key="",
    base_url="https://api.hcnsec.cn/v1"
)


MODEL = "auto"
#MODEL = "longcat-2.0"
# MODEL = "step-3.7-flash"
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
3. Create Webpage:  {"tool": "generate_webpage", "file_name": "short_file_name", "topic": "What the webpage should be"}
4. Generate Image:  {"tool": "generate_image", "file_name": "short_file_name", "prompt": "What the image should show"}
5. Open App:        {"tool": "open_app", "app_name": "..."}
6. Open Website:    {"tool": "open_website", "url": "..."}
7. Get Weather:     {"tool": "get_weather", "query": "current|rain|forecast", "location": "..."}
8. Get Date/Time:   {"tool": "get_datetime", "query": "time|date", "calendar": "english|bangla|arabic|all"}
9. Find Files:      {"tool": "find_files", "query": "what to look for", "mode": "keyword|filename|extension|folder"}
10. Internet Speed:  {"tool": "get_internet_speed"}
11. Set Timer:       {"tool": "set_timer", "duration": "2 minutes"}
12. Volume:          {"tool": "set_volume", "action": "set", "level": 50}
13. Browser Control:  {"tool": "browser_action", "action": "open|navigate|click|fill|type|select|scroll|read|inspect|extract|switch_tab|search|back|forward|reload|screenshot|download|upload|save_csv|save_excel", "target": "CSS selector or visible label", "value": "..."}
14. Check Email:      {"tool": "check_email", "action": "unread|latest|search", "query": "optional sender or search query"}
15. Reply Email:      {"tool": "reply_email", "body": "message content to reply with"}
16. Run Protocol:     {"tool": "run_protocol", "protocol": "work|gaming|lockdown|sleep|study"}
17. Executive Briefing: {"tool": "get_briefing", "mode": "morning|evening"}
18. Deep Research:    {"tool": "deep_research", "topic": "detailed subject or question to research", "depth": "quick|deep|exhaustive", "action": "start|open_pdf|open_folder|read_summary"}
19. Web Autopilot:    {"tool": "web_autopilot", "task": "browsing, shopping, or comparison instruction"}
20. Neural Memory:    {"tool": "manage_memory", "action": "remember|recall|forget|show", "text": "fact or subject query"}
21. Executive Coach:  {"tool": "coach_standup", "action": "start|status|complete|debrief|blocker", "text": "optional goals or goal query", "enabled": true}
22. Intel Briefing:   {"tool": "intel_briefing", "category": "all|tech|crypto|github|football", "open_hud": true}
23. Weather Station:  {"tool": "get_weather_station", "location": "optional city"}
24. Cyber Watchdog:   {"tool": "scan_network", "fast": true}
25. Document Intel:   {"tool": "doc_intel", "action": "summarize|ask|extract_tables|open", "query": "optional question or calculation", "file_path": "optional path"}
26. Clean Slate:      {"tool": "clean_slate", "target": "downloads|documents|desktop"}

Rules:
- clean downloads / organize documents / clean slate / tidy desktop / sort files → clean_slate, examples:
    "organize document folder" → {"tool": "clean_slate", "target": "documents"}
    "clean documents" → {"tool": "clean_slate", "target": "documents"}
    "clean downloads" → {"tool": "clean_slate", "target": "downloads"}
    "organize my downloads" → {"tool": "clean_slate", "target": "downloads"}
    "clean slate" → {"tool": "clean_slate", "target": "downloads"}
    "clean my desktop" → {"tool": "clean_slate", "target": "desktop"}
- summarize this pdf / analyze document / extract tables from pdf / what does section X say / calculate total expenses in bank statement → doc_intel, examples:
    "summarize this pdf" → {"tool": "doc_intel", "action": "summarize"}
    "what does section 4 say about warranty" → {"tool": "doc_intel", "action": "ask", "query": "what does section 4 say about warranty"}
    "calculate total expenses in this bank statement" → {"tool": "doc_intel", "action": "ask", "query": "calculate the total expenses"}
    "extract all tables from this pdf into an excel csv" → {"tool": "doc_intel", "action": "extract_tables"}
    "open doc intel" → {"tool": "doc_intel", "action": "open"}
- scan network / local network radar / cyber watchdog / who is on my wifi / network security check → scan_network, examples:
    "scan local network" → {"tool": "scan_network", "fast": true}
    "who is on my wifi" → {"tool": "scan_network", "fast": true}
    "run network radar" → {"tool": "scan_network", "fast": false}
- weather station / open weather station / show weather forecast station / atmospheric radar → get_weather_station, examples:
    "open weather station" → {"tool": "get_weather_station"}
    "weather station" → {"tool": "get_weather_station"}
    "show weather station in Tokyo" → {"tool": "get_weather_station", "location": "Tokyo"}
- read me today's tribune / read tribune / read today's newspaper / listen to tribune broadcast → ron_tribune (action: "read"), examples:
    "read me today's tribune" → {"tool": "ron_tribune", "action": "read"}
    "read today's newspaper" → {"tool": "ron_tribune", "action": "read"}
    "listen to tribune broadcast" → {"tool": "ron_tribune", "action": "read"}
- show newspaper / open newspaper / show tribune / open tribune / newspaper / newspaper pdf → ron_tribune (action: "open"), examples:
    "show newspaper" → {"tool": "ron_tribune", "action": "open"}
    "show me newspaper" → {"tool": "ron_tribune", "action": "open"}
    "show the newspaper" → {"tool": "ron_tribune", "action": "open"}
    "open newspaper" → {"tool": "ron_tribune", "action": "open"}
    "open tribune" → {"tool": "ron_tribune", "action": "open"}
    "newspaper" → {"tool": "ron_tribune", "action": "open"}
    "open newspaper pdf" → {"tool": "ron_tribune", "action": "pdf"}
- generate today's newspaper / publish tribune / compile newspaper → ron_tribune (action: "generate"), examples:
    "generate today's newspaper" → {"tool": "ron_tribune", "action": "generate"}
- intel briefing / morning intel briefing / world report / sports news / football headlines / football scores / what is happening in tech / market update / crypto update → intel_briefing (category: ...), examples:
    "give me the morning intel briefing" → {"tool": "intel_briefing", "category": "all", "open_hud": true}
    "world report" → {"tool": "intel_briefing", "category": "all", "open_hud": true}
    "reads all sports news headline" → {"tool": "intel_briefing", "category": "football", "open_hud": true}
    "sports news" → {"tool": "intel_briefing", "category": "football", "open_hud": true}
    "football scores" → {"tool": "intel_briefing", "category": "football", "open_hud": true}
    "how did real madrid play" → {"tool": "intel_briefing", "category": "football", "open_hud": true}
    "what's happening in tech" → {"tool": "intel_briefing", "category": "tech", "open_hud": true}
    "check crypto markets" → {"tool": "intel_briefing", "category": "crypto", "open_hud": true}
    "show trending github repos" → {"tool": "intel_briefing", "category": "github", "open_hud": true}
- standup / start standup / set my goals today / my goals are ... → coach_standup (action: "start", text: ...), examples:
    "start standup" → {"tool": "coach_standup", "action": "start"}
    "set my goals today: finish testing, write docs, review PR" → {"tool": "coach_standup", "action": "start", "text": "finish testing, write docs, review PR"}
- what are my goals / check standup / how is my focus today → coach_standup (action: "status"), examples:
    "what are my goals today" → {"tool": "coach_standup", "action": "status"}
    "how is my focus today" → {"tool": "coach_standup", "action": "status"}
- finished goal ... / mark goal X as done / completed ... → coach_standup (action: "complete", text: ...), examples:
    "mark goal 1 as done" → {"tool": "coach_standup", "action": "complete", "text": "1"}
    "I finished writing documentation" → {"tool": "coach_standup", "action": "complete", "text": "writing documentation"}
- evening debrief / daily debrief / wrap up the day → coach_standup (action: "debrief"), examples:
    "evening debrief" → {"tool": "coach_standup", "action": "debrief"}
    "daily debrief" → {"tool": "coach_standup", "action": "debrief"}
- enable/disable distraction blocker → coach_standup (action: "blocker", enabled: true/false), examples:
    "enable distraction blocker" → {"tool": "coach_standup", "action": "blocker", "enabled": true}
    "turn off distraction blocker" → {"tool": "coach_standup", "action": "blocker", "enabled": false}
- remember that ... / note down that ... / keep in mind that ... → manage_memory (action: "remember"), examples:
    "remember that my mother's birthday is October 14" → {"tool": "manage_memory", "action": "remember", "text": "my mother's birthday is October 14"}
    "note down that my wifi password is secret" → {"tool": "manage_memory", "action": "remember", "text": "my wifi password is secret"}
- what do you remember about ... / do you recall ... / what is my ... → manage_memory (action: "recall"), examples:
    "what do you remember about my mother" → {"tool": "manage_memory", "action": "recall", "text": "mother"}
    "what is my wifi password" → {"tool": "manage_memory", "action": "recall", "text": "wifi password"}
- forget ... / delete memory about ... → manage_memory (action: "forget"), examples:
    "forget my mother's birthday" → {"tool": "manage_memory", "action": "forget", "text": "mother's birthday"}
- show memories / what do you know about me → manage_memory (action: "show"), examples:
    "show my memories" → {"tool": "manage_memory", "action": "show"}
- autopilot / browse and buy / check Daraz or Amazon / find cheapest ... on Daraz or Amazon or Skyscanner / check github issues → web_autopilot (JSON only), examples:
    "check Daraz and Amazon and tell me the cheapest Logitech MX Master 3S" → {"tool": "web_autopilot", "task": "check Daraz/Amazon and tell me the cheapest Logitech MX Master 3S"}
    "go to github and check the open issues on my RON repository" → {"tool": "web_autopilot", "task": "go to github and check the open issues on my RON repository"}
    "find the cheapest flight ticket from Dhaka to Bangkok next Friday on Skyscanner" → {"tool": "web_autopilot", "task": "find the cheapest flight ticket from Dhaka to Bangkok next Friday on Skyscanner"}
- research / deep research / investigate / compare X and Y → deep_research (JSON only), examples:
    "research solid-state batteries in 2026" → {"tool": "deep_research", "topic": "solid-state batteries in 2026"}
    "compare Supabase vs Firebase pricing and scalability" → {"tool": "deep_research", "topic": "Supabase vs Firebase pricing and scalability"}
    "investigate quantum computing breakthroughs" → {"tool": "deep_research", "topic": "quantum computing breakthroughs"}
- good morning / morning briefing / start my day / good evening / evening briefing → get_briefing (JSON only), examples:
    "good morning ron" → {"tool": "get_briefing", "mode": "morning"}
    "give me the morning briefing" → {"tool": "get_briefing", "mode": "morning"}
    "good evening ron" → {"tool": "get_briefing", "mode": "evening"}
    "evening briefing" → {"tool": "get_briefing", "mode": "evening"}
- initiate/start/activate protocol (work/coding/gaming/lockdown/zero/sleep) → run_protocol (JSON only), examples:
    "initiate work protocol" → {"tool": "run_protocol", "protocol": "work"}
    "coding protocol" → {"tool": "run_protocol", "protocol": "work"}
    "gaming protocol" → {"tool": "run_protocol", "protocol": "gaming"}
    "protocol zero" → {"tool": "run_protocol", "protocol": "lockdown"}
    "sleep protocol" → {"tool": "run_protocol", "protocol": "sleep"}
- reply to email / reply to sender / replay him saying ... → reply_email (JSON only), examples:
    "reply to him and say thank you" → {"tool": "reply_email", "body": "thank you"}
    "replay him and say thank you" → {"tool": "reply_email", "body": "thank you"}
    "reply saying I will look into it" → {"tool": "reply_email", "body": "I will look into it"}
    "reply to latest email saying thanks" → {"tool": "reply_email", "body": "thanks"}
- check inbox / unread emails / read email → check_email (JSON only)
- play music/video → play_youtube (JSON only)
- create/make/write/generate a pdf/report/document/notes about or on a subject → generate_pdf (JSON only), examples:
    "create a pdf about black holes" → {"tool": "generate_pdf", "file_name": "black_holes", "topic": "Black Holes"}
    "make me a report on the French Revolution" → {"tool": "generate_pdf", "file_name": "french_revolution", "topic": "The French Revolution"}
    "write a document about machine learning basics" → {"tool": "generate_pdf", "file_name": "machine_learning_basics", "topic": "Machine Learning Basics"}
  If — and only if — the user dictates the exact text to put in the file, pass it as "content" instead of "topic":
    "create a pdf named notes with hello world" → {"tool": "generate_pdf", "file_name": "notes", "content": "Hello world"}
  Never write the report body yourself in this JSON. Just give the topic; the document is written separately.
- create/make/write/generate a webpage / web page / HTML page for a subject → generate_webpage (JSON only), examples:
    "create a webpage for wishing happy birthday to my friend" → {"tool": "generate_webpage", "file_name": "happy_birthday", "topic": "A stylish Happy Birthday wish page for a friend"}
    "make a webpage for anniversary wishes" → {"tool": "generate_webpage", "file_name": "anniversary", "topic": "An anniversary wish page with animations"}
    "create a webpage about space" → {"tool": "generate_webpage", "file_name": "space", "topic": "Space exploration"}
  The generated page must be a single self-contained .html file with inline CSS and JavaScript, styled and visually appealing.
  Never write the page body yourself in this JSON. Just give the topic; the page is written separately.
- create/make/write/generate an image / picture / photo / drawing / diagram of a subject → generate_image (JSON only), examples:
    "create an image of a diagram of a mobile phone" → {"tool": "generate_image", "file_name": "mobile_phone_diagram", "prompt": "a detailed technical diagram of a mobile phone showing the internal components"}
    "generate a picture of a sunset over the ocean" → {"tool": "generate_image", "file_name": "ocean_sunset", "prompt": "a photorealistic sunset over a calm ocean with orange and purple sky"}
    "draw a cat sitting on a wall" → {"tool": "generate_image", "file_name": "cat_on_wall", "prompt": "a cute cat sitting on a brick wall, digital art style"}
  Describe the image clearly in "prompt"; the model renders it and opens it in the default viewer.
  Never try to describe the image yourself. Just give the prompt; the image is generated separately.
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
- control a website after it is open (click, fill, type, select, scroll, read,
  inspect, extract, search, download, upload, or save extracted data) →
  browser_action (JSON only). Emit exactly ONE browser action per reply so Ron
  can verify the resulting page before taking another step. Prefer a stable CSS
  selector or an exact visible/accessible label in ``target``. For ``open`` or
  ``navigate`` pass ``url``; for ``search`` pass ``query``; for ``fill``/``type``
  and ``select`` pass ``value``; for ``scroll`` use ``direction`` and optional
  ``amount``; for ``save_csv``/``save_excel`` use ``file_name``. ``extract``
  returns text and link records matching ``target``. ``upload`` requires an
  existing ``file_path`` and must only be emitted after the user explicitly
  requested that exact upload. Do not submit forms, send messages, purchase,
  publish, delete, or otherwise create an external effect unless the user has
  explicitly authorised that exact action; only then include ``"confirmed": true``.
  If that authorisation is absent, explain what is ready and ask for confirmation.
  Examples:
    "click Sign in" → {"tool": "browser_action", "action": "click", "target": "Sign in"}
    "fill email with me@example.com" → {"tool": "browser_action", "action": "fill", "target": "email", "value": "me@example.com"}
    "read this page" → {"tool": "browser_action", "action": "read"}
    "export the extracted products to Excel" → {"tool": "browser_action", "action": "save_excel", "file_name": "products"}
- send an email → send_email (JSON only). The recipient, subject, and body must
  all come from the user's words. Examples:
    "send an email to john@example.com with subject Meeting and body See you tomorrow"
      → {"tool": "send_email", "to": "john@example.com", "subject": "Meeting", "body": "See you tomorrow"}
    "send email to me with subject Hi and body Hello therFe"
      → {"tool": "send_email", "to": "me", "subject": "Hi", "body": "Hello there"}
    "send email to rownok with subject Update and body Done"
      → {"tool": "send_email", "to": "rownok", "subject": "Update", "body": "Done"}
    "send good morning to thanos"
      → {"tool": "send_email", "to": "thanos", "subject": "Good Morning", "body": "Good morning"}
    "send hello to hassan"
      → {"tool": "send_email", "to": "hassan", "subject": "Hello", "body": "Hello"}
    "email thanos saying good morning"
      → {"tool": "send_email", "to": "thanos", "subject": "Good Morning", "body": "Good morning"}
- set a reminder → set_reminder (JSON only). Parse the clock time from what they
  say and pass it as "at" in a format parse_time() understands ("5:00 PM",
  "17:30", "five pm"). Pass the reminder text as "message". Examples:
    "notify me at 5 pm for study"
      → {"tool": "set_reminder", "at": "5:00 PM", "message": "study"}
    "remind me at 17:30 to call mom"
      → {"tool": "set_reminder", "at": "17:30", "message": "call mom"}
  Never ask questions -- just execute.
- NEVER ask questions for tool actions, just execute immediately
- For a tool action, your complete response must be the JSON object from the
  Tools list. Do not prefix it with "Ron:", do not explain it, and do not say
  that you are about to perform the action. For example, the complete response
  to "play Shape of You on YouTube" is
  {"tool":"play_youtube","search_query":"Shape of You"}.
- Keep spoken replies short and sharp. Never be verbose.
- Always refer to yourself as Ron.
- Always refer to the user as Sir or by name Ifteqhar."""

DOCUMENT_WRITER_PROMPT = """You are an expert technical writer and publication designer producing an executive reference report for RON.

Write an authoritative, thorough, and beautifully structured report on the given topic.
If the request specifies a language (such as Bengali / Bangla, Spanish, etc.), write the entire document in that language with natural, elegant vocabulary.

Format the document in Markdown with rich components that map into an executive report:

# Main Document Title
(A compelling subtitle / executive overview paragraph summarizing the core subject)

## Executive Summary
(2-3 paragraphs setting the stage and explaining significance)
(Include a chronological timeline or milestones using:
- **Era / Year**: Description of key event or milestone
- **Era / Year**: Description of key event or milestone
)
(Include a central takeaway callout using > **Central Insight:** ...)

## (3 to 5 substantive topical sections, each with a descriptive ## heading)
(Use rich structures inside sections:
- ### Subheadings for distinct sub-topics
- Markdown comparison/specification tables:
  | Header 1 | Header 2 | Header 3 |
  | --- | --- | --- |
  | Data 1 | Data 2 | Data 3 |
- Two-column feature cards using bold bullet points:
  - **Feature Title**: Concise description of the characteristic
  - **Feature Title**: Concise description of the characteristic
- Linear progressions or evolution flows using arrows:
  Phase 1 -> Phase 2 -> Phase 3 -> Phase 4
- Key takeaway blockquotes: > **Design Lesson:** ...
- Substantial analytical paragraphs of prose with **bold** key terms
)

## Key Takeaways
(4-6 concise bullet points highlighting the most important findings)

## Future Outlook & Conclusion
(1-2 forward-looking closing paragraphs analyzing future trends)

Requirements:
- Length: 1200-2000 words. Thorough, detailed, and publication-ready.
- Be specific: include concrete dates, metrics, real-world examples, and terminology.
- Output ONLY the document itself. No preamble, no commentary."""

WEBPAGE_WRITER_PROMPT = """You are an expert front-end developer and designer producing a single, self-contained HTML file.

Create a beautiful, stylish, fully self-contained webpage on the topic you are given. The entire page -- HTML structure, CSS styling, and JavaScript behaviour -- must be in ONE .html file. No external stylesheets, no external scripts, no external images.

Requirements:
- Output ONLY the HTML document. No preamble, no commentary, no "here is your page".
- Include <!DOCTYPE html>, <html>, <head>, and <body>. Set a <title> matching the topic.
- Put all CSS in a <style> block in <head>. Make it visually striking: use a harmonious colour palette, gradients, shadows, rounded corners, smooth transitions, and responsive layout.
- Put all JavaScript in a <script> block before </body>. Add life to the page: animations, transitions, hover effects, particle or confetti effects, typing effects, or other interactive behaviour appropriate to the topic.
- Use semantic HTML (header, main, section, footer) and a mobile-friendly viewport meta tag.
- Use CSS variables for the colour palette. Use Flexbox/Grid for layout.
- Use emoji or inline SVG for icons/illustrations -- no external images.
- Make the page feel premium and polished: animated entrance of elements, hover states, a cohesive visual theme.
- Keep the page self-contained and reasonable in size. Prefer inline SVG and CSS art over images."""


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

# This is deliberately sent immediately before every model request.  The main
# prompt contains detailed guidance for every feature, but smaller/fast models
# can lose the output-format rule by the time they reach its end.  Keeping this
# instruction adjacent to the user turn makes the JSON contract reliable.
TOOL_OUTPUT_REMINDER = """Output contract: if the user asked Ron to perform any
action listed in the tools, return ONLY one valid JSON object with a `tool` key.
No `Ron:`, prose, Markdown fence, or confirmation sentence.  Otherwise answer
normally in plain text."""

def extract_tool_call(reply: str) -> dict | None:
    """Return a recognised tool object even when a model wraps it in prose.

    OpenAI-compatible providers do not all honour the JSON-only instruction in
    exactly the same way.  ``raw_decode`` lets us recover a valid object from
    common forms such as ``Ron: { ... }`` or a Markdown explanation.  A tool
    name is still required; unknown names reach the existing safe "unknown
    tool" response rather than being executed.
    """
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", reply):
        try:
            value, _ = decoder.raw_decode(reply[match.start():])
        except json.JSONDecodeError:
            continue
        if (isinstance(value, dict) and isinstance(value.get("tool"), str)
                and value["tool"].strip()):
            return value
    return None


def extract_youtube(command: str) -> str | None:
    """Recognise explicit playback commands without depending on the LLM.

    This is intentionally narrow: it only covers requests beginning with
    ``play``/``put on``.  The query remains free-form, so song, artist, playlist
    and video titles all work, while unrelated conversation still goes to Ron.
    """
    text = command.strip()
    match = re.match(
        r"^(?:ron[,:]?\s+)?(?:hey\s+)?(?:please\s+)?"
        r"(?:(?:can|could|would)\s+you\s+)?(?:play|put\s+on)\s+(.+?)\s*$",
        text, flags=re.IGNORECASE)
    if not match:
        return None

    query = match.group(1).strip()
    # "play X on YouTube" should search X, rather than the literal phrase.
    query = re.sub(r"\s+(?:on|from)\s+youtube\s*$", "", query,
                   flags=re.IGNORECASE).strip()
    return query or None

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
    clean = re.sub(
        r"^(?:(?:hey|hi|hello)\s+)?(?:ron[,:]?\s+)?(?:(?:hey|hi|hello)\s+)?(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?",
        "", command.strip(), flags=re.IGNORECASE).strip()

    # Normalize common speech recognition misrecognitions
    norm = clean.lower()
    norm = re.sub(r"\ba text\b", "rtx", norm)
    norm = re.sub(r"\btill on\b", "deal on", norm)
    norm = re.sub(r"\bfine\b", "find", norm)

    # Veto shopping, deal finding, and product price queries so they don't get hijacked into bare domain searches
    if re.search(r"\b(?:browse\s+and\s+(?:find|buy|check)|cheapest|lowest price|best price|best deals?|deals?\s+(?:on|for)|compare prices?|buy)\b", norm):
        return None
    for kw in WEBSITE_KEYWORDS:
        if clean.lower().startswith(kw):
            site = clean[len(kw):].strip()
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
    if clean.lower().startswith("open "):
        site = clean[5:].strip()
        if site.lower() in KNOWN_SITES:
            return KNOWN_SITES[site.lower()]
        # Check if site name is in command (e.g., "open pornhub", "open youtube")
        for site_name, site_url in KNOWN_SITES.items():
            # Word-boundary match. A bare substring test lets the "x" entry fire on
            # any command containing that letter -- "open max" would open x.com.
            if re.search(rf'\b{re.escape(site_name)}\b', clean.lower()):
                return site_url

    return None


# --- Foreground browser commands -------------------------------------------
# These are direct routes because some compatible models answer "read this
# page" conversationally instead of returning the browser_action JSON. The
# direct route keeps the active Brave tab usable even when the model slips.
_BROWSER_READ_TRIGGER = re.compile(
    r"\b(?:read|summari[sz]e|inspect)\s+(?:this|the\s+current|current)\s+(?:web\s+)?page\b",
    re.IGNORECASE,
)
_BROWSER_CLICK_TRIGGER = re.compile(
    r"^\s*(?:click|press|tap)\s+(?:on\s+)?(?:the\s+)?(.+?)\s*$", re.IGNORECASE
)
_BROWSER_NEXT_TAB_TRIGGER = re.compile(
    r"\b(?:switch|go|move)\s+(?:to\s+)?(?:the\s+)?next\s+tab\b", re.IGNORECASE
)
_BROWSER_PREVIOUS_TAB_TRIGGER = re.compile(
    r"\b(?:switch|go|move)\s+(?:to\s+)?(?:the\s+)?(?:previous|prev|last)\s+tab\b", re.IGNORECASE
)
_BROWSER_NUMBERED_TAB_TRIGGER = re.compile(
    r"\b(?:switch|go|move)\s+(?:to\s+)?(?:the\s+)?(\d+)(?:st|nd|rd|th)?\s+tab\b", re.IGNORECASE
)


def extract_browser_action(command: str):
    """Return a safe, single foreground-browser action or None."""
    text = (command or "").strip()
    numbered_tab = _BROWSER_NUMBERED_TAB_TRIGGER.search(text)
    if numbered_tab:
        return {"action": "switch_tab", "index": int(numbered_tab.group(1))}
    if _BROWSER_NEXT_TAB_TRIGGER.search(text):
        return {"action": "switch_tab", "direction": "next"}
    if _BROWSER_PREVIOUS_TAB_TRIGGER.search(text):
        return {"action": "switch_tab", "direction": "previous"}
    if _BROWSER_READ_TRIGGER.search(text):
        return {"action": "read"}
    click = _BROWSER_CLICK_TRIGGER.match(text)
    if click:
        target = click.group(1).strip(" .")
        if target:
            return {"action": "click", "target": target}
    return None

def extract_folder(command: str):
    """Resolve drive letters and well-known folder names to a filesystem path."""
    text = (command or "").strip().lower()
    # Normalize common speech recognition misrecognitions
    text = re.sub(r"\b(?:shered|shred|sheared|cher|sher)\b", "shared", text)

    # Drive letters: "open D drive", "show me the C drive"
    drive = re.search(r'\b([a-zA-Z])\s*drive\b', text)
    if drive:
        letter = drive.group(1).upper()
        named = re.search(r'(\w+)\s+folder', text)
        if named:
            candidate = f"{letter}:\\{named.group(1)}"
            if os.path.isdir(candidate):
                return candidate
        return f"{letter}:\\"

    # Named user folders: "open documents", "open my downloads folder", "open shered folder"
    if re.search(r'\b(open|show|go to|take me to|explore|খোলো|খুলুন)\b', text) or re.search(r'\b(?:shared|share|ron[\s\-_]*share)\s+(?:folder|files?)\b', text):
        for alias in sorted(_FOLDER_ALIASES, key=len, reverse=True):
            sub = _FOLDER_ALIASES[alias]
            if sub and re.search(rf'\b{re.escape(alias)}\b', text):
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


# --- Holographic Weather Station -------------------------------------------

_WX_STATION_REGEX = re.compile(
    r"\b(?:open|show|launch|display|view|start|pull up|pop up|activate)?\s*"
    r"(?:the\s+)?(?:ron\s+)?(?:holographic\s+)?"
    r"(?:weather\s+station|meteorological\s+station|forecast\s+station|weather\s+radar|"
    r"আবহাওয়া\s*স্টেশন|ওয়েদার\s*স্টেশন|আবহাওয়া\s*কেন্দ্র)\b",
    re.IGNORECASE
)


def extract_weather_station(command: str):
    """Check if the user is asking to open/view the Weather Station overlay."""
    text = (command or "").strip().lower()
    if not text:
        return None
    if _WX_STATION_REGEX.search(text):
        return {"location": _extract_place(text)}
    return None


def handle_weather_station(intent_or_data: dict) -> str:
    """Gather complete station telemetry, push to HUD overlay via bus, and speak executive brief."""
    location = intent_or_data.get("location") if isinstance(intent_or_data, dict) else None
    bus.set_state(bus.EXECUTING, "WEATHER STATION · ONLINE")
    bus.activity("Deploying holographic weather station", "pending")
    station_data = weather.get_weather_station_data(location, force=True)

    # Broadcast to HUD modal
    payload = dict(station_data)
    payload["open"] = True
    bus.weather_station(**payload)

    # Also update panel 05 telemetry
    hud_p = weather.hud_payload(location)
    bus.weather(**hud_p)

    bus.activity("Weather station deployed on HUD", "ok")
    return weather.spoken_weather_station_brief(station_data)


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
    r"|internet|web search|on the web"
    r"|cheapest|lowest price|best price|best deals?|deals?|price of|prices? for"
    r"|buy|shopping|daraz|amazon|skyscanner|flights?|tickets?)\b")

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


# ---------------------------------------------------------------------------
# Browser automation
# ---------------------------------------------------------------------------

def handle_browser_action(data: dict) -> str:
    """Run one DOM-first browser action and report its verified outcome."""
    action = str(data.get("action") or "").strip().lower()
    if not action:
        return "I need a browser action to perform, Sir."
    bus.activity(f"Browser: {action}", "pending")
    result = browser_agent.run(action, **{key: value for key, value in data.items()
                                          if key not in {"tool", "action"}})
    if result.get("needs_confirmation"):
        bus.activity("Browser action awaiting confirmation", "info")
        return result.get("message", "This browser action needs your confirmation, Sir.")
    if not result.get("ok"):
        bus.activity("Browser action failed", "fail")
        return result.get("message", "I could not complete that browser action, Sir.")
    page = result.get("page") or {}
    if result.get("file"):
        return result.get("message", "Browser file saved, Sir.")
    if action == "extract":
        return f"Extracted {result.get('count', 0)} records, Sir."
    if page.get("title"):
        return f"{result.get('message', 'Browser action completed.')} Now on {page['title']}, Sir."
    return result.get("message", "Browser action completed, Sir.")


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Email Checking & Reading (IMAP)
# ---------------------------------------------------------------------------

def handle_check_email(data: dict) -> str:
    """Check unread emails or read the newest email via Gmail IMAP."""
    action = (data.get("action") or "unread").lower().strip()
    sender = data.get("sender") or data.get("query")
    lang = bus.get_language()

    bus.set_state(bus.EXECUTING, "CHECKING INBOX")
    bus.activity("Checking Gmail inbox", "pending")

    if action in ("latest", "read"):
        reply = email_notify.read_latest_email(lang=lang)
    elif action == "search" and sender:
        inbox = email_notify.check_inbox(filter_type="all", limit=3, sender=sender, lang=lang)
        reply = inbox.get("summary_spoken", "No emails found, Sir.")
    else:
        inbox = email_notify.check_inbox(filter_type="unread", limit=3, lang=lang)
        reply = inbox.get("summary_spoken", "Inbox check complete, Sir.")

    bus.activity("Inbox checked", "ok")
    return reply


def extract_check_email(command: str) -> dict | None:
    """Classify an inbox checking or email reading request."""
    text = (command or "").lower().strip()
    if not text:
        return None

    # Exclude sending or composing emails
    if re.search(r"\b(send|compose|write)\s+(?:an?\s+)?(?:email|mail)\b", text):
        return None

    # 1. Read latest email
    if re.search(r"\b(?:read|open)\s+(?:my\s+|the\s+)?(?:latest|newest|recent|new|last)\s+(?:e-?mail|message)\b", text) or \
       re.search(r"\b(?:read|open)\s+(?:my\s+|the\s+)?(?:e-?mail|message)\b", text) or \
       re.search(r"সর্বশেষ\s*ইমেইল", text):
        return {"action": "latest"}

    # 2. Check emails from specific sender
    m_from = re.search(r"\b(?:check|find|search)\s+(?:my\s+)?(?:e-?mails?|inbox)\s+from\s+([a-zA-Z0-9_\-\.@]+)", text)
    if m_from:
        return {"action": "search", "sender": m_from.group(1)}

    # 3. Check inbox / unread emails
    check_patterns = [
        r"\bcheck\s+(?:my\s+)?(?:e-?mails?|inbox)\b",
        r"\b(?:any|do i have(?:\s+any)?)\s+(?:new\s+|unread\s+)?(?:e-?mails?|messages)\b",
        r"\bunread\s+(?:e-?mails?|messages)\b",
        r"\bwhats\s+in\s+(?:my\s+)?inbox\b",
        r"ইমেইল\s*(?:চেক|দেখো)",
        r"ইনবক্স\s*(?:চেক|দেখো)",
        r"(?:নতুন|অপঠিত)\s*ইমেইল",
    ]
    if any(re.search(pat, text) for pat in check_patterns):
        return {"action": "unread"}

    return None


def extract_reply_email(command: str) -> dict | None:
    """Classify a request to reply to the latest email."""
    text = (command or "").strip()
    if not text:
        return None

    patterns = [
        # Explicit target & say/saying:
        # "reply him and saying thank you", "replay him and say thank you", "reply to him and say thanks", etc.
        r"^(?:send\s+(?:a\s+)?)?(?:reply|replay)\s+(?:to\s+)?(?:him|her|them|the\s+email|the\s+latest\s+email|latest\s+email|it|sender|that)?\s*(?:\b(?:and\s+saying|saying|and\s+say|say|that)\b)?\s*[:,-]?\s*(.+)$",
        # Bare reply/replay: "reply thank you", "replay thanks"
        r"^(?:send\s+(?:a\s+)?)?(?:reply|replay)\s*[:,-]?\s*(.+)$",
        # Bengali patterns
        r"(?:তাকে|ওকে|উনাকে|ইমেইলে|ইমেইলের|সর্বশেষ\s*ইমেইলে)\s*(?:উত্তর|রিপ্লাই)\s*(?:দাও|পাঠাও|দিয়ে\s*বলো)\s*[:,-]?\s*(.+)$",
        r"(?:উত্তর|রিপ্লাই)\s*(?:দাও|পাঠাও)\s*[:,-]?\s*(.+)$",
    ]

    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            body = m.group(1).strip()
            # Clean leading phrasing artifacts if still present
            body = re.sub(r"^(?:\b(?:and\s+saying|saying|and\s+say|say|that|to)\b\s*)+", "", body, flags=re.IGNORECASE).strip()
            body = re.sub(r"^[\"']|[\"']$", "", body).strip()
            if body:
                return {"body": body}

    return None


EMAIL_REPLY_PROMPT = """You are an executive email assistant for Ron (Ifteqhar).
Compose a professional, polite, and concise email reply to the incoming email based on the user's intent.

Strict Formatting Guidelines:
1. Tone: Professional, warm, courteous, articulate, and business-appropriate.
2. Structure: 1 to 2 clear paragraphs directly addressing the incoming email while fulfilling the user's intent.
3. DO NOT include greetings or salutations (e.g., 'Dear ...', 'Hello ...', 'Hi ...'). The email template automatically adds greetings.
4. DO NOT include sign-offs, closing remarks, or signatures (e.g., 'Best regards', 'Sincerely', 'Thanks', 'Ron'). The email template automatically adds closings and signatures.
5. DO NOT output subject lines, headers, or markdown code fences (```). Output ONLY the raw body paragraphs of the reply.
6. Language: If the requested language is Bengali ('bn'), write fluent, formal, polite Bengali. Otherwise, write natural professional English.
"""


def clean_reply_body(text: str) -> str:
    """Sanitize LLM-generated reply body to ensure no duplicate salutations or markdown fences."""
    content = (text or "").strip()
    if not content:
        return ""

    # Strip markdown code blocks
    if content.startswith("```"):
        parts = content.split("```")
        if len(parts) >= 2:
            content = parts[1]
            if content.lower().startswith("markdown"):
                content = content[8:]
            elif content.lower().startswith("text"):
                content = content[4:]
            content = content.strip()

    # Remove leading Subject: or Re: line if present
    content = re.sub(r"^(?:Subject|Re):\s*[^\n]*\n+", "", content, flags=re.IGNORECASE).strip()

    # Remove leading greetings (e.g. "Dear Thanos,", "Hi Thanos,", "Hello Thanos:")
    content = re.sub(r"^(?:Dear|Hi|Hello|Hey)\s+[^,\n]+[,:\n]+\s*", "", content, flags=re.IGNORECASE).strip()

    # Remove trailing sign-offs (e.g. "Best regards,\nRon", "Sincerely,\n...", "Warm regards,\n...")
    content = re.sub(
        r"\n+(?:Best regards|Warm regards|Kind regards|Regards|Sincerely|Respectfully|Thanks and regards|Thanks|Thank you),?\s*(?:\n+[^\n]+)?\s*$",
        "",
        content,
        flags=re.IGNORECASE
    ).strip()

    # Strip wrapping quotes
    content = re.sub(r'^["\']|["\']$', '', content).strip()
    return content


def generate_email_reply(sender: str, subject: str, incoming_body: str, user_intent: str, lang: str = "en") -> str:
    """Use the LLM to generate a professional, context-aware reply to an incoming email."""
    lang_instruction = "Bengali (formal, polite, professional)" if lang == "bn" else "English (professional, courteous)"
    user_prompt = (
        f"Incoming Email Sender: {sender or 'Sender'}\n"
        f"Incoming Email Subject: {subject or '(No Subject)'}\n"
        f"Incoming Email Content:\n{incoming_body or '(No message body provided)'}\n\n"
        f"User's Reply Instruction/Intent: {user_intent}\n"
        f"Required Language: {lang_instruction}"
    )

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": EMAIL_REPLY_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.6,
            max_tokens=400,
            timeout=30,
        )
        raw_reply = (response.choices[0].message.content or "").strip()
        cleaned = clean_reply_body(raw_reply)
        if cleaned:
            return cleaned
    except Exception as e:
        print(f"[email_reply] LLM generation failed ({e}), using fallback intent.")

    # Graceful fallback if LLM is unreachable or returns empty
    if lang == "bn":
        fallback = f"আপনার '{subject}' সংক্রান্ত বার্তার জন্য আন্তরিক ধন্যবাদ। {user_intent}"
    else:
        fallback = f"Thank you for your message regarding '{subject}'. {user_intent}"
    return clean_reply_body(fallback)


def handle_reply_email(data: dict) -> str:
    """Reply to the most recent email in the inbox using LLM-generated professional draft."""
    user_intent = str(data.get("body") or "").strip()
    lang = bus.get_language()

    if not user_intent:
        return "I need a message to reply with, Sir." if lang != "bn" else "স্যার, রিপ্লাই দেওয়ার জন্য কোনো বার্তা পাইনি।"

    bus.set_state(bus.EXECUTING, "FETCHING LATEST EMAIL")
    bus.activity("Checking inbox for latest email", "pending")

    # Fetch latest email to determine sender and context
    inbox = email_notify.check_inbox(filter_type="all", limit=1, lang=lang)
    if not inbox.get("ok") or not inbox.get("messages"):
        err = "Could not find any recent email to reply to, Sir." if lang != "bn" else "স্যার, রিপ্লাই করার জন্য কোনো সাম্প্রতিক ইমেইল পাওয়া যায়নি।"
        bus.activity("No recent email found to reply to", "fail")
        return err

    target_msg = inbox["messages"][0]
    sender = target_msg.get("from_name") or target_msg.get("from_addr") or "Sender"
    subject = target_msg.get("subject") or "Message"
    incoming_body = target_msg.get("body") or target_msg.get("snippet") or ""

    # Generate professional reply via LLM
    bus.set_state(bus.THINKING, f"COMPOSING REPLY · {sender.upper()[:28]}")
    bus.activity(f"Drafting professional reply to {sender}", "pending")

    prof_reply = generate_email_reply(
        sender=sender,
        subject=subject,
        incoming_body=incoming_body,
        user_intent=user_intent,
        lang=lang,
    )

    # Send the email
    bus.set_state(bus.EXECUTING, f"SENDING REPLY · {sender.upper()[:28]}")
    bus.activity(f"Sending reply to {sender}", "pending")

    result = email_notify.reply_to_latest_email(reply_body=prof_reply, lang=lang, target_msg=target_msg)
    if result.get("ok"):
        bus.activity(f"Reply sent to {result.get('recipient')}", "ok")
    else:
        bus.activity("Failed to send reply", "fail")

    return result.get("message", "Reply processed, Sir.")


# ---------------------------------------------------------------------------
# Workspace Protocols (One-Command Workspace Automation)
# ---------------------------------------------------------------------------

def extract_protocol(command: str) -> dict | None:
    """Classify a workspace protocol automation request."""
    text = (command or "").lower().strip()
    if not text:
        return None

    # 1. Lockdown / Zero / Stealth
    if re.search(r"\b(?:protocol\s+(?:zero|0)|stealth\s+protocol|lockdown\s+protocol|lockdown\s+mode|lock\s+my\s+(?:pc|computer|workstation)|lock\s+workstation)\b", text) or \
       re.search(r"(?:প্রোটোকল\s*জিরো|লকডাউন|পিসি\s*লক)", text):
        return {"protocol": "lockdown"}

    # 2. Work / Coding
    if re.search(r"\b(?:initiate|start|activate|engage|run)?\s*(?:work|coding|code|developer|dev)\s+(?:protocol|mode)\b", text) or \
       re.search(r"\b(?:start\s+working|focus\s+mode|focus\s+protocol)\b", text) or \
       re.search(r"(?:ওয়ার্ক\s*প্রোটোকল|কোডিং\s*প্রোটোকল|কাজের\s*প্রোটোকল|কাজ\s*শুরু\s*করো)", text):
        return {"protocol": "work"}

    # 3. Gaming
    if re.search(r"\b(?:initiate|start|activate|engage|run)?\s*(?:gaming|game|play)\s+(?:protocol|mode)\b", text) or \
       re.search(r"(?:গেমিং\s*প্রোটোকল|গেম\s*প্রোটোকল|গেম\s*মোড|গেমিং\s*মোড)", text):
        return {"protocol": "gaming"}

    # 4. Sleep / Night
    if re.search(r"\b(?:sleep|night|rest)\s+(?:protocol|mode)\b", text) or \
       re.search(r"\b(?:good\s*night\s+ron|power\s+down\s+system)\b", text) or \
       re.search(r"(?:স্লিপ\s*প্রোটোকল|শুভরাত্রি)", text):
        return {"protocol": "sleep"}

    # 5. Study
    if re.search(r"\b(?:study|learning)\s+(?:protocol|mode)\b", text) or \
       re.search(r"(?:স্টাডি\s*প্রোটোকল|পড়াশোনা\s*মোড|পড়াশোনার\s*প্রোটোকল)", text):
        return {"protocol": "study"}

    # 6. Generic pattern: "initiate <name> protocol" or "<name> protocol"
    m = re.search(r"\b(?:initiate|start|activate|engage|run)\s+([a-zA-Z0-9_\-]+)\s+protocol\b", text)
    if m:
        return {"protocol": m.group(1).lower()}

    m2 = re.search(r"\b([a-zA-Z0-9_\-]+)\s+protocol\b", text)
    if m2 and m2.group(1).lower() in ("work", "gaming", "lockdown", "sleep", "study", "zero", "stealth", "code", "dev"):
        return {"protocol": m2.group(1).lower()}

    return None


def handle_protocol(data: dict) -> str:
    """Execute a workstation automation protocol."""
    proto_name = str(data.get("protocol") or "").strip()
    lang = bus.get_language()

    if not proto_name:
        return "I need a protocol name to initiate, Sir." if lang != "bn" else "স্যার, কোন প্রোটোকলটি চালু করতে হবে তা উল্লেখ করুন।"

    result = protocol.execute_protocol(proto_name, lang=lang)
    return result.get("spoken", "Protocol processed, Sir.")


def extract_briefing(command: str) -> dict | None:
    """Classify a morning or evening executive briefing request."""
    text = (command or "").lower().strip()
    if not text:
        return None

    # Never treat third-party communication or contact greetings as a personal briefing:
    # e.g. "send good morning to thanos", "wish thanos good morning", "tell thanos good morning",
    # "send an email to thanos saying good morning"
    if re.search(r"\b(?:send|email|mail|message|text|say|tell|wish|write|forward)\b", text):
        if re.search(r"\bto\s+[a-z0-9_.@\-]+", text) or \
           re.search(r"\b(?:tell|wish|email|message|text|write)\s+(?:to\s+)?[a-z0-9_.@\-]+", text) or \
           re.search(r"\b(?:him|her|them)\b", text):
            return None

    # 1. Morning briefing
    if re.search(r"\b(?:good\s+morning(?:\s+ron)?|morning\s+briefing|start\s+my\s+day|daily\s+briefing|morning\s+update)\b", text) or \
       re.search(r"(?:শুভ\s*সকাল|সকালের\s*ব্রিফিং|সকালের\s*আপডেট)", text):
        return {"mode": "morning"}

    # 2. Evening briefing
    if re.search(r"\b(?:good\s+evening(?:\s+ron)?|evening\s+briefing|night\s+briefing|evening\s+update|wrap\s+up\s+my\s+day)\b", text) or \
       re.search(r"(?:শুভ\s*সন্ধ্যা|সন্ধ্যার\s*ব্রিফিং|সন্ধ্যার\s*আপডেট)", text):
        return {"mode": "evening"}

    # 3. Generic briefing: infer from time of day
    if re.search(r"\b(?:give\s+me\s+a?\s*briefing|executive\s+briefing|brief\s+me|daily\s+update)\b", text) or \
       re.search(r"(?:আমাকে\s*ব্রিফ\s*করো|আজকের\s*ব্রিফিং|ব্রিফিং\s*দাও)", text):
        hr = clock.now().hour
        mode = "morning" if 4 <= hr < 17 else "evening"
        return {"mode": mode}

    return None


def handle_briefing(data: dict) -> str:
    """Execute an executive morning or evening briefing."""
    mode = str(data.get("mode") or "morning").strip().lower()
    play_music = False if mode == "morning" else bool(data.get("play_music", False))
    lang = bus.get_language()
    res = briefing.execute_briefing(mode=mode, play_music=play_music, lang=lang)
    return res.get("spoken", "Briefing delivered, Sir.")


def extract_research(command: str) -> dict | None:
    """Classify an autonomous deep research or technical comparison request."""
    text = (command or "").strip()
    if not text:
        return None

    low = text.lower()

    # Post-research voice actions
    if any(p in low for p in ("open research pdf", "open the research pdf", "open research report", "show research report", "open research document", "show research dossier")):
        return {"action": "open_pdf"}
    if any(p in low for p in ("open research folder", "open research documents", "open research directory")):
        return {"action": "open_folder"}
    if any(p in low for p in ("read research summary", "read the research report", "read research briefing", "read research takeaways")):
        return {"action": "read_summary"}

    # Bengali post-research actions
    if any(p in text for p in ("রিসার্চ রিপোর্ট খোলো", "রিসার্চ পিডিএফ খোলো", "রিসার্চের পিডিএফ দেখাও")):
        return {"action": "open_pdf"}
    if any(p in text for p in ("রিসার্চ সামারি বলো", "রিসার্চের সারাংশ বলো", "রিসার্চ রিপোর্ট পড়ো")):
        return {"action": "read_summary"}

    # Depth detection
    depth = "deep"
    if re.search(r"\b(?:quick\s+research|rapid\s+research|brief\s+research)\b", text, re.I):
        depth = "quick"
    elif re.search(r"\b(?:exhaustive\s+research|comprehensive\s+research|in-depth\s+research)\b", text, re.I):
        depth = "exhaustive"

    # 1. Direct research patterns:
    # "research <topic>", "deep research on <topic>", "investigate <topic>", "do research on <topic>"
    m = re.search(
        r"\b(?:quick\s+research(?:\s+on)?|exhaustive\s+research(?:\s+on)?|deep\s+research(?:\s+on)?|research(?:\s+on|\s+about)?|investigate|do\s+(?:a\s+)?(?:deep\s+|quick\s+|exhaustive\s+)?research\s+(?:on|about)?|deep\s+dive\s+on)\s+(.+)$",
        text,
        re.IGNORECASE,
    )
    if m:
        topic = m.group(1).strip().rstrip(".?!")
        if len(topic) >= 3:
            return {"topic": topic, "depth": depth}

    # 2. Comparison commands:
    # "compare <A> vs <B>", "compare <A> and <B>", "benchmark <A> against <B>"
    m2 = re.search(r"\b(?:compare|benchmark)\s+(.+)$", text, re.IGNORECASE)
    if m2:
        topic = m2.group(1).strip().rstrip(".?!")
        if len(topic) >= 3:
            return {"topic": f"Comparison: {topic}", "depth": depth}

    # 3. Bengali research triggers:
    # "রিসার্চ করো <বিষয়>", "অনুসন্ধান করো <বিষয়>", "<বিষয়> নিয়ে রিসার্চ করো"
    m_bn = re.search(r"(?:রিসার্চ\s*করো|অনুসন্ধান\s*করো|তথ্য\s*খোঁজো)\s*(?:বিষয়ে|সম্পর্কে)?\s*(.+)$", text)
    if m_bn:
        topic = m_bn.group(1).strip().rstrip(".?!")
        if len(topic) >= 2:
            return {"topic": topic, "depth": depth}

    m_bn2 = re.search(r"(.+?)\s*(?:নিয়ে|সম্পর্কে|বিষয়ে)\s*(?:রিসার্চ|অনুসন্ধান)\s*করো", text)
    if m_bn2:
        topic = m_bn2.group(1).strip().rstrip(".?!")
        if len(topic) >= 2:
            return {"topic": topic, "depth": depth}

    return None


def handle_research(data: dict) -> str:
    """Initiate or manage autonomous multi-source deep research."""
    lang = bus.get_language()
    action = str(data.get("action") or "").strip().lower()

    if action == "open_pdf":
        res = researcher.open_research_pdf()
        if res.get("ok"):
            return "Opening the research dossier PDF for you now, Sir." if lang != "bn" else "স্যার, রিসার্চের পিডিএফ ওপেন করছি।"
        return "No generated research PDF was found on disk, Sir." if lang != "bn" else "স্যার, কোনো রিসার্চ পিডিএফ পাওয়া যায়নি।"

    if action == "open_folder":
        res = researcher.open_research_folder()
        if res.get("ok"):
            return "Opening your research publications folder, Sir." if lang != "bn" else "স্যার, রিসার্চ ফোল্ডার ওপেন করছি।"
        return "Unable to open the documents directory, Sir."

    if action == "read_summary":
        snap = researcher.get_current_research_snapshot()
        summary = snap.get("summary")
        if not summary:
            history = researcher.get_research_history()
            if history:
                summary = history[0].get("summary")
        if summary:
            return f"Here is the executive research briefing: {summary}" if lang != "bn" else f"রিসার্চ সামারি: {summary}"
        return "There is no completed research dossier to summarize yet, Sir." if lang != "bn" else "স্যার, এখনও কোনো রিসার্চ ফাইল তৈরি হয়নি।"

    topic = str(data.get("topic") or "").strip()
    depth = str(data.get("depth") or "deep").strip()
    res = researcher.start_deep_research(topic, client=client, model=MODEL, lang=lang, depth=depth)
    return res.get("spoken", "Starting deep research, Sir.")


# ---------------------------------------------------------------------------
# Autonomous Web Agent & Auto-Pilot
# ---------------------------------------------------------------------------

def extract_autopilot(command: str) -> dict | None:
    """Classify an autonomous web agent/autopilot request, or return None.

    Matches commands like:
      - "check Daraz/Amazon and tell me the cheapest Logitech MX Master 3S"
      - "go to github and check the open issues on my RON repository"
      - "find the cheapest flight ticket from Dhaka to Bangkok next Friday on Skyscanner"
      - "autopilot <task>" / "browse and find <task>"
      - "দারাজ/অ্যামাজনে সবচেয়ে কম দামে <পণ্য> খুঁজে বের করো"
      - "গিটহাবে গিয়ে ইস্যু চেক করো"
    """
    text = (command or "").strip()
    if not text:
        return None
    low = text.lower()

    # Normalize common speech recognition misrecognitions
    norm = low
    norm = re.sub(r"\ba text\b", "rtx", norm)
    norm = re.sub(r"\btill on\b", "deal on", norm)
    norm = re.sub(r"\bfine\b", "find", norm)

    # Direct keyword triggers
    triggers = [
        "autopilot",
        "auto pilot",
        "browse and find",
        "browse and buy",
        "অটোপাইলট",
    ]
    if any(trig in norm for trig in triggers):
        return {"task": text}

    # General price comparison & deals queries:
    # "find cheapest rtx 4070", "browse best deal on rtx 4070", "compare prices for ...", "best deals on ..."
    deals_match = re.search(
        r"\b(?:find|browse|check|look for|get me)\s+(?:the\s+)?(?:cheapest|lowest price|best price|best deals?|deals?\s+(?:on|for))\b|"
        r"\b(?:browse\s+best\s+deals?\s+(?:on|for))\b|"
        r"\b(?:cheapest|lowest price|best deals?)\s+[a-z0-9]|"
        r"\b(?:compare\s+prices?|price\s+comparison)\b",
        norm
    )
    if deals_match:
        return {"task": text}

    # "check Daraz/Amazon...", "find the cheapest ... on Daraz/Amazon/Skyscanner"
    ecommerce_match = re.search(
        r"\b(?:check|search|find|look up)\s+(?:on\s+)?(?:daraz|dara|daras|daraj|amazon|ebay|aliexpress)\b|"
        r"\b(?:daraz|dara|daras|daraj|amazon|ebay)\s+(?:and\s+)?(?:tell me\s+)?(?:the\s+)?(?:cheapest|price)\b|"
        r"\b(?:cheapest|lowest price)\s+.+\s+(?:on\s+)?(?:daraz|dara|daras|daraj|amazon|ebay|skyscanner)\b",
        norm
    )
    if ecommerce_match:
        return {"task": text}

    # "go to github and check the open issues...", "check issues on github"
    github_match = re.search(
        r"\b(?:go to\s+)?github\s+(?:and\s+)?(?:check|find|see)\s+(?:the\s+)?(?:open\s+)?(?:issues|prs|pull requests)\b|"
        r"\b(?:check|find)\s+(?:open\s+)?(?:issues|prs)\s+on\s+github\b",
        low
    )
    if github_match:
        return {"task": text}

    # Flight queries on Skyscanner
    flight_match = re.search(
        r"\b(?:find|check|search)\s+(?:the\s+)?(?:cheapest\s+)?(?:flight|ticket)\s+.+\s+on\s+skyscanner\b",
        low
    )
    if flight_match:
        return {"task": text}

    # Bengali matches
    if any(w in text for w in ["দারাজ", "অ্যামাজন", "গিটহাব", "স্কাইস্ক্যানার", "সবচেয়ে কম দাম", "টিকেট", "ফ্লাইট"]):
        if any(w in text for w in ["খুঁজো", "খুঁজে বের করো", "চেক করো", "দেখাও", "তুলনা করো"]):
            return {"task": text}

    return None


def handle_autopilot(data: dict) -> str:
    """Execute autonomous web autopilot mission."""
    task = str(data.get("task") or data.get("query") or "").strip()
    if not task:
        return "I need a browsing or shopping task for the autopilot, Sir."
    lang = bus.get_language()
    res = autopilot.start_autopilot(task, client=client, model=MODEL, lang=lang)
    return res.get("spoken", "Autonomous web autopilot engaged, Sir.")


# ---------------------------------------------------------------------------
# Neural Long-Term Memory & Knowledge Graph
# ---------------------------------------------------------------------------

def extract_memory(command: str) -> dict | None:
    """Classify a memory command (remember, recall, forget, show), or return None.

    Matches English and Bengali trigger phrasings:
      - "Ron, remember that my mother's birthday is October 14"
      - "Remember that my wifi password is xyz"
      - "Note down that I like dark coffee"
      - "Keep in mind that I prefer dark mode"
      - "What do you remember about my mother?"
      - "What is my wifi password?" / "Do you recall my keys?"
      - "Ron, forget my mother's birthday"
      - "Delete memory about wifi"
      - "Show my memories" / "What do you know about me?"
      - "মনে রাখো আমার প্রিয় রঙ নীল"
      - "আমার মায়ের জন্মদিন কি মনে আছে?"
      - "আমার ওয়াইফাই পাসওয়ার্ড কী?"
      - "স্মৃতি থেকে মুছে ফেলো ওয়াইফাই পাসওয়ার্ড"
      - "আমার স্মৃতিগুলো দেখাও"
    """
    text = (command or "").strip()
    if not text:
        return None
    low = text.lower()

    # 1. SHOW MEMORIES
    show_patterns = [
        r"\b(?:show|view|open|display)\s+(?:my\s+)?(?:memories|memory(?:\s+matrix)?)\b",
        r"\bwhat\s+do\s+you\s+(?:know|remember)\s+about\s+me\b",
        r"\blist\s+(?:my\s+)?memories\b",
        r"\bmemory\s+matrix\b",
        r"\b(?:আমার\s+)?(?:স্মৃতি(?:গুলো|সমূহ)?\s*দেখাও|মেমরি\s*দেখাও)\b",
    ]
    if any(re.search(p, low, re.UNICODE) for p in show_patterns):
        return {"action": "show"}

    # 2. FORGET MEMORIES
    forget_patterns = [
        r"^(?:ron[,\s]+)?(?:please\s+)?(?:forget\s+that|forget|delete\s+memory\s+(?:about|of)?|remove\s+memory\s+(?:about|of)?|clear\s+memory\s+(?:about|of)?)\s+(.+)$",
        r"^(?:স্মৃতি\s*থেকে\s*মুছে\s*ফেলো|মুছে\s*ফেলো|ভুলে\s*যাও)\s+(.+)$",
        r"^(.+?)\s*(?:সম্পর্কিত\s*)?(?:স্মৃতি\s*মুছে\s*ফেলো|ভুলে\s*যাও)$",
    ]
    for p in forget_patterns:
        m = re.search(p, text, re.IGNORECASE | re.UNICODE)
        if m:
            query = m.group(1).strip().rstrip(".?!")
            if query:
                return {"action": "forget", "text": query}

    # 3. REMEMBER / STORE MEMORIES
    remember_patterns = [
        r"^(?:ron[,\s]+)?(?:please\s+)?(?:remember\s+that|remember|note\s+down\s+that|note\s+down|keep\s+in\s+mind\s+that|keep\s+in\s+mind|store\s+in\s+memory\s+that|store\s+in\s+memory|save\s+to\s+memory)\s+(.+)$",
        r"^(?:মনে\s*রাখো|মনে\s*রাখ|নোট\s*করো|স্মরণে\s*রাখো)\s+(.+)$",
    ]
    for p in remember_patterns:
        m = re.search(p, text, re.IGNORECASE | re.UNICODE)
        if m:
            fact = m.group(1).strip().rstrip(".?!")
            if fact:
                return {"action": "remember", "text": fact}

    # 4. RECALL / QUERY MEMORIES
    recall_explicit = [
        r"^(?:ron[,\s]+)?(?:what\s+do\s+you\s+remember\s+about|what\s+do\s+you\s+recall\s+about|do\s+you\s+remember|do\s+you\s+recall)\s+(.+?)\??$",
        r"^(?:ron[,\s]+)?tell\s+me\s+my\s+(.+?)\??$",
        r"^(?:আমার\s+)?(.+?)\s*(?:কি\s*)?মনে\s*আছে\??$",
    ]
    for p in recall_explicit:
        m = re.search(p, text, re.IGNORECASE | re.UNICODE)
        if m:
            query = m.group(1).strip().rstrip(".?!")
            if query:
                return {"action": "recall", "text": query}

    # "what is my <x>"
    m_what_is_my = re.search(r"^(?:ron[,\s]+)?what\s+is\s+my\s+(.+?)\??$", low)
    if m_what_is_my:
        query = m_what_is_my.group(1).strip().rstrip(".?!")
        if not any(ex in query for ex in ("internet speed", "speed", "ip", "time", "date", "schedule")):
            return {"action": "recall", "text": query}

    # Bengali: "আমার <বিষয়> কী?"
    m_bn_what = re.search(r"^আমার\s+(.+?)\s*কী\??$", text)
    if m_bn_what:
        query = m_bn_what.group(1).strip().rstrip(".?!")
        if not any(ex in query for ex in ("সময়", "তারিখ", "নেট স্পিড", "ইন্টারনেট স্পিড")):
            return {"action": "recall", "text": query}

    return None


def handle_remember(data: dict) -> str:
    """Store fact in long-term memory."""
    fact = str(data.get("text") or data.get("fact") or data.get("content") or "").strip()
    if not fact:
        return "I didn't catch what you'd like me to remember, Sir."

    bus.set_state(bus.EXECUTING, "MEMORY · STORING")
    bus.activity(f"Storing memory: {fact[:35]}", "pending")
    res = memory.remember(fact)
    bus.activity("Memory saved to neural graph", "ok")

    mem = res.get("memory", {})
    sub = mem.get("subject") or "that fact"
    if bus.get_language() == "bn":
        return f"মনে রেখেছি স্যার: {sub}।"
    return f"I have committed that to memory, Sir: {sub}."


def handle_recall(data: dict) -> str:
    """Retrieve and formulate speech for requested memories."""
    query = str(data.get("text") or data.get("query") or "").strip()
    if not query:
        return "What would you like me to recall, Sir?"

    bus.set_state(bus.EXECUTING, "MEMORY · RECALLING")
    bus.activity(f"Searching memories: {query[:35]}", "pending")
    matches = memory.recall(query, limit=3, min_score=0.15)

    if not matches:
        bus.activity("No matching memories found", "ok")
        if bus.get_language() == "bn":
            return f"স্যার, '{query}' সম্পর্কে আমার স্মৃতিতে কিছু সংরক্ষিত নেই।"
        return f"I don't have any memory recorded regarding '{query}', Sir."

    bus.activity(f"Recalled {len(matches)} relevant memory node(s)", "ok")
    top = matches[0]

    if bus.get_language() == "bn":
        return f"স্যার, আপনার {top['subject']} সম্পর্কে যা মনে আছে: {top['content']}।"

    if len(matches) == 1:
        return f"Regarding your {top['subject']}, Sir: {top['content']}."

    second = matches[1]
    return f"Regarding your {top['subject']}, Sir: {top['content']}. Also, {second['content']}."


def handle_forget(data: dict) -> str:
    """Forget and purge memory nodes."""
    query = str(data.get("text") or data.get("query") or "").strip()
    if not query:
        return "What would you like me to forget, Sir?"

    bus.set_state(bus.EXECUTING, "MEMORY · PURGING")
    bus.activity(f"Purging memory: {query[:35]}", "pending")
    res = memory.forget(query)

    if res.get("ok"):
        bus.activity("Memory purged from graph", "ok")
        deleted_list = ", ".join(res.get("deleted_subjects", []))
        if bus.get_language() == "bn":
            return f"স্যার, {deleted_list} সম্পর্কিত স্মৃতি মুছে ফেলা হয়েছে।"
        return f"I have cleared the memory regarding {deleted_list}, Sir."

    bus.activity("No memory matched for deletion", "fail")
    if bus.get_language() == "bn":
        return f"মুছে ফেলার মতো কোনো স্মৃতি পাওয়া যায়নি, স্যার।"
    return f"I could not find any memories matching '{query}', Sir."


def handle_show_memories(data: dict = None) -> str:
    """Open HUD memory matrix and summarize stored knowledge."""
    bus.set_state(bus.EXECUTING, "MEMORY · MATRIX")
    bus.activity("Opening Memory Matrix overlay", "ok")
    stats = memory.get_memory_stats()
    total = stats.get("total_memories", 0)
    cats_count = len(stats.get("categories", {}))

    memory._broadcast_memory_state(event_name="show_overlay")

    if bus.get_language() == "bn":
        return f"স্যার, নিউরাল স্মৃতি ম্যাট্রিক্স খোলা হয়েছে। মোট {total}টি স্মৃতি সংরক্ষিত রয়েছে।"
    return f"Opening the Neural Memory Matrix, Sir. You have {total} active memories across {cats_count} categories."


def handle_memory(data: dict) -> str:
    """Dispatcher for tool 20 `manage_memory` from LLM."""
    action = str(data.get("action") or "").lower().strip()
    if action == "remember":
        return handle_remember(data)
    elif action == "recall":
        return handle_recall(data)
    elif action == "forget":
        return handle_forget(data)
    elif action in ("show", "list", "display", "matrix"):
        return handle_show_memories(data)
    else:
        return handle_recall(data)


# ---------------------------------------------------------------------------
# Daily Standup & Executive Focus Coach
# ---------------------------------------------------------------------------

def extract_coach(command: str) -> dict | None:
    """Classify an executive coach / standup request, or return None.

    Matches:
      - "Ron, start standup" / "Start daily standup"
      - "My goals today are finish documentation, review PR 42, and workout"
      - "Set my goals: A, B, C"
      - "Check standup" / "What are my goals today?" / "How is my focus today?"
      - "I finished goal 1" / "Mark documentation as done"
      - "Evening debrief" / "Daily debrief" / "Wrap up the day"
      - "Enable distraction blocker" / "Turn off distraction blocker"
      - Bengali:
        - "স্ট্যান্ডআপ শুরু করো" / "আমার আজকের লক্ষ্য..."
        - "আজকের লক্ষ্যগুলো কী?" / "আজকের ফোকাস কেমন?"
        - "১ নম্বর লক্ষ্য সম্পন্ন" / "দিনের কাজের হিসাব"
    """
    text = (command or "").strip()
    if not text:
        return None
    low = text.lower()

    # 1. Distraction Blocker Toggle
    if re.search(r"\b(?:enable|turn\s+on|activate)\s+(?:distraction\s+)?blocker\b", low) or "ব্লকার চালু" in text or "ব্লকার অন" in text:
        return {"action": "blocker", "enabled": True}
    if re.search(r"\b(?:disable|turn\s+off|deactivate)\s+(?:distraction\s+)?blocker\b", low) or "ব্লকার বন্ধ" in text or "ব্লকার অফ" in text:
        return {"action": "blocker", "enabled": False}

    # 2. Evening Debrief / Wrap up
    debrief_patterns = [
        r"\b(?:evening|daily|night|end\s+of\s+day)\s+debrief\b",
        r"\b(?:wrap\s+up|close\s+out)\s+(?:for\s+)?(?:the\s+day|today)\b",
        r"\bdaily\s+retrospective\b",
        r"(?:দিনের\s*কাজের\s*হিসাব|ইভনিং\s*ডিব্রিফ|আজকের\s*ডিব্রিফ)",
    ]
    if any(re.search(p, text, re.IGNORECASE | re.UNICODE) for p in debrief_patterns):
        return {"action": "debrief"}

    # 3. Status & Check Standup
    status_patterns = [
        r"\b(?:what\s+are|show|check|list)\s+(?:my\s+)?(?:daily\s+)?(?:goals|standup|non-negotiables)\b",
        r"\bcheck\s+standup\b",
        r"\bhow\s+is\s+my\s+(?:focus|productivity)(?:\s+today)?\b",
        r"\bproductivity\s+score\b",
        r"(?:আমার\s+)?(?:আজকের\s+)?(?:লক্ষ্যগুলো\s*কী|কাজগুলো\s*কী|স্ট্যান্ডআপ\s*চেক\s*করো)",
        r"আজকের\s+ফোকাস\s+কেমন",
    ]
    if any(re.search(p, text, re.IGNORECASE | re.UNICODE) for p in status_patterns):
        return {"action": "status"}

    # 4. Mark Goal Complete
    complete_patterns = [
        r"\b(?:i\s+)?(?:finished|completed|done\s+with)\s+(?:goal\s+)?(.+)$",
        r"\bmark\s+(?:goal\s+)?(.+?)\s+(?:as\s+)?(?:done|finished|complete|completed)\b",
        r"\bgoal\s+(\d+)\s+(?:is\s+)?(?:done|finished|complete|completed)\b",
        r"\b(.+?)\s*(?:কাজ|লক্ষ্য)?\s*(?:শেষ\s*হয়েছে|সম্পন্ন\s*হয়েছে|ডান)\b",
        r"\b(\d+)\s*নম্বর\s*লক্ষ্য\s*সম্পন্ন\b",
    ]
    for p in complete_patterns:
        m = re.search(p, text, re.IGNORECASE | re.UNICODE)
        if m:
            ident = m.group(1).strip().rstrip(".?!")
            if ident:
                return {"action": "complete", "text": ident}

    # 5. Start Standup / Set Goals
    if re.search(r"\b(?:start|begin|run)\s+(?:my\s+)?(?:daily\s+)?standup\b", low) or "স্ট্যান্ডআপ শুরু করো" in text:
        m_goals = re.search(r"\b(?:standup|করো)[:\s]+(.+)$", text, re.IGNORECASE)
        goals_text = m_goals.group(1).strip() if m_goals else ""
        return {"action": "start", "text": goals_text}

    start_patterns = [
        r"\b(?:my\s+)?(?:goals|non-negotiables)\s+(?:for\s+today\s+are|today\s+are|are)\s+(.+)$",
        r"\bset\s+(?:my\s+)?goals?\s+(?:for\s+today|today)?(?:\s*[:to]\s*|\s+)(.+)$",
        r"\bআমার\s+আজকের\s+লক্ষ্য\s*(?:হলো|হচ্ছে)?\s*(.+)$",
    ]
    for p in start_patterns:
        m = re.search(p, text, re.IGNORECASE | re.UNICODE)
        if m:
            goals_text = m.group(1).strip().rstrip(".?!")
            if goals_text:
                return {"action": "start", "text": goals_text}

    # 6. Direct Open / Standup UI / Focus
    if re.search(r"\b(?:open|show|display|view)\s+(?:standup(?:\s+ui)?|focus(?:\s+hud)?|coach)\b", low) or \
       re.search(r"^(?:ron[,\s]+)?(?:standup(?:\s+ui)?|focus(?:\s+hud)?|coach)$", low) or \
       text.strip() == "স্ট্যান্ডআপ":
        return {"action": "status"}

    return None


def handle_coach_standup(data: dict) -> str:
    """Initialize standup goals or prompt the user."""
    raw_text = str(data.get("text") or "").strip()
    if not raw_text:
        bus.set_state(bus.EXECUTING, "COACH · STANDUP")
        bus.activity("Standup initiated", "ok")
        coach.broadcast_state(event_name="show_overlay")
        if bus.get_language() == "bn":
            return "শুভ সকাল স্যার! আজকের আপনার ৩টি প্রধান নন-নেগোশিয়েবল লক্ষ্য কী কী?"
        return "Good morning Sir. What are your 3 high-priority non-negotiables for today?"

    items = re.split(r",|\band\b|;|\d+\.\s*", raw_text, flags=re.IGNORECASE)
    goals = [it.strip().strip(".?!") for it in items if len(it.strip()) > 2]
    if not goals:
        goals = [raw_text]

    bus.set_state(bus.EXECUTING, "COACH · RECORDING GOALS")
    bus.activity(f"Recording {len(goals)} non-negotiables", "pending")
    res = coach.start_standup(goals)
    coach.broadcast_state(event_name="show_overlay")
    bus.activity(f"{len(goals)} goals locked in", "ok")

    if bus.get_language() == "bn":
        return f"স্যার, আজকের জন্য আপনার {len(goals)}টি লক্ষ্য সংরক্ষিত হয়েছে। শুভকামনা রইল!"
    return f"Standup logged, Sir. I have locked in {len(goals)} non-negotiable goals for today. Let's execute."


def handle_coach_complete(data: dict) -> str:
    """Mark a goal completed."""
    ident = str(data.get("text") or "").strip()
    if not ident:
        return "Which goal did you complete, Sir?"

    bus.set_state(bus.EXECUTING, "COACH · GOAL COMPLETED")
    bus.activity(f"Completing goal: {ident[:30]}", "pending")
    res = coach.mark_goal(ident, completed=True)

    if res.get("ok"):
        goal = res.get("goal", {})
        pct = res.get("metrics", {}).get("goal_pct", 0)
        bus.activity(f"Goal completed ({pct}% today)", "ok")
        if bus.get_language() == "bn":
            return f"চমৎকার স্যার! '{goal.get('text')}' সম্পন্ন হয়েছে। আজকের লক্ষ্য অগ্রগতি {pct}%।"
        return f"Excellent execution, Sir. '{goal.get('text')}' is complete. You're at {pct}% of your daily goals."

    bus.activity("Goal not found", "fail")
    if bus.get_language() == "bn":
        return f"স্যার, '{ident}' সম্পর্কিত কোনো লক্ষ্য খুঁজে পাইনি।"
    return f"I could not locate any goal matching '{ident}', Sir."


def handle_coach_status(data: dict = None) -> str:
    """Report current standup and focus status."""
    bus.set_state(bus.EXECUTING, "COACH · STATUS")
    bus.activity("Checking standup & focus telemetry", "ok")
    status = coach.get_standup_status()
    metrics = status["metrics"]
    total = metrics["total_goals"]
    done = metrics["completed_goals"]
    pct = metrics["productivity_score"]
    focus_hrs = metrics["focus_hours"]

    coach.broadcast_state(event_name="show_overlay")

    if total == 0:
        if bus.get_language() == "bn":
            return f"স্যার, আজ কোনো লক্ষ্য সেট করা হয়নি। আপনি {focus_hrs} ঘণ্টা ফোকাস কাজ করেছেন। স্কোর {pct}%।"
        return f"No goals logged yet today, Sir. You have clocked {focus_hrs} hours of deep focus with a {pct}% productivity score."

    if bus.get_language() == "bn":
        return f"স্যার, আজ {total}টির মধ্যে {done}টি লক্ষ্য সম্পন্ন হয়েছে। প্রোডাক্টিভিটি স্কোর {pct}%, ফোকাস {focus_hrs} ঘণ্টা।"
    return f"You have completed {done} of {total} goals today with {focus_hrs} hours of focused work. Current productivity score: {pct}%."


def handle_coach_debrief(data: dict = None) -> str:
    """Deliver evening debrief retrospective."""
    bus.set_state(bus.EXECUTING, "COACH · DEBRIEF")
    bus.activity("Synthesizing daily retrospective", "pending")
    res = coach.evening_debrief()
    bus.activity("Retrospective archived to Neural Memory", "ok")
    return res.get("spoken", "Daily debrief complete, Sir.")


def handle_coach_blocker(data: dict) -> str:
    """Toggle distraction blocker."""
    enabled = bool(data.get("enabled", True))
    coach.set_blocker(enabled)
    status_str = "armed and active" if enabled else "disabled"
    bus.activity(f"Distraction blocker {status_str}", "ok")
    if bus.get_language() == "bn":
        return f"ডিসট্র্যাকশন ব্লকার {'চালু' if enabled else 'বন্ধ'} করা হয়েছে, স্যার।"
    return f"Distraction blocker is now {status_str}, Sir."


def handle_coach(data: dict) -> str:
    """Dispatcher for tool 21 `coach_standup` from LLM."""
    action = str(data.get("action") or "").lower().strip()
    if action == "start":
        return handle_coach_standup(data)
    elif action == "complete":
        return handle_coach_complete(data)
    elif action == "status":
        return handle_coach_status(data)
    elif action == "debrief":
        return handle_coach_debrief(data)
    elif action == "blocker":
        return handle_coach_blocker(data)
    else:
        return handle_coach_status(data)


_focus_monitor_started = False

def start_focus_monitor():
    """Start background focus tracking and proactive distraction interception thread."""
    global _focus_monitor_started
    if _focus_monitor_started:
        return
    _focus_monitor_started = True

    def _loop():
        while not shutdown_event.is_set():
            try:
                nudge = coach.track_tick(interval_seconds=5.0)
                if nudge and nudge.get("spoken"):
                    bus.activity(nudge["spoken"], "info")
                    if bus.get_language() == "bn":
                        streak_min = nudge.get("streak_min", 20)
                        app = nudge.get("app", "সোশ্যাল মিডিয়া")
                        pending = nudge.get("pending_task", "কাজ")
                        speak(f"স্যার, আপনি {streak_min} মিনিট ধরে {app} ব্যবহার করছেন যখন আপনার '{pending}' কাজটি বাকি আছে।")
                    else:
                        speak(nudge["spoken"])
            except Exception:
                pass
            time.sleep(5.0)

    t = threading.Thread(target=_loop, daemon=True, name="FocusMonitorThread")
    t.start()


# ---------------------------------------------------------------------------
# Live Intel Briefing Radio ("RON World Report")
# ---------------------------------------------------------------------------

def extract_intel(command: str) -> dict | None:
    """Classify an intel briefing radio / world report request, or return None."""
    text = (command or "").strip()
    if not text:
        return None
    low = text.lower()

    # 1. Direct english intel commands
    if re.search(r"\b(?:give\s+me\s+(?:the\s+)?)?(?:morning\s+)?intel(?:\s+briefing|\s+report|\s+radio)?\b", low) or \
       re.search(r"\b(?:ron\s+|jarvis\s+)?world\s+report\b", low) or \
       re.search(r"\b(?:news\s+radio|intel\s+radio|global\s+intel|morning\s+intel)\b", low):
        return {"tool": "intel_briefing", "category": "all", "open_hud": True}

    # 2. Specific topic commands: sports/football, fixtures, tech, crypto, github
    if re.search(r"\b(?:(?:read\w*|tell\w*|give\w*|show\w*|get\w*)?\s*(?:all\s+)?(?:sports?|football|soccer)(?:\s+(?:news|headlines?|scores?|matches?|fixtures?|briefing|update))?|sports?\s+(?:news|headlines?|scores?|fixtures?)|football\s+(?:news|headlines?|scores?|fixtures?|matches?)|soccer\s+(?:news|headlines?|scores?|fixtures?)|upcoming\s+(?:football\s+|soccer\s+)?(?:fixtures?|matches?)|latest\s+(?:matches?|scores?|fixtures?)|match\s+scores?|real\s*madrid|barcelona|barca|man(?:chester)?\s*city|man(?:chester)?\s*united|man\s*utd|bayern(?:\s*munich)?|arsenal|bangladesh(?:\s+match|\s+football)?)\b", low):
        return {"tool": "intel_briefing", "category": "football", "open_hud": True}
    if re.search(r"\b(?:what(?:'s|\s+is)\s+(?:happening|new)\s+in\s+tech|tech\s+(?:news|briefing|headlines))\b", low):
        return {"tool": "intel_briefing", "category": "tech", "open_hud": True}
    if re.search(r"\b(?:crypto\s+(?:markets?|update|prices?)|check\s+crypto|market\s+briefing)\b", low):
        return {"tool": "intel_briefing", "category": "crypto", "open_hud": True}
    if re.search(r"\b(?:trending\s+(?:github|repos?)|github\s+trending)\b", low):
        return {"tool": "intel_briefing", "category": "github", "open_hud": True}

    # 3. Direct UI show commands
    if re.search(r"\b(?:open|show|display|view)\s+(?:intel(?:\s+hud|\s+overlay|\s+report)?|world\s+report)\b", low) or \
       re.search(r"^(?:ron[,\\s]+)?(?:intel(?:\s+hud|\s+report)?|world\s+report)$", low):
        intel.broadcast_state(event_name="show_overlay")
        return {"tool": "intel_briefing", "category": "all", "open_hud": True, "ui_only": True}

    # 4. Bengali Natural Language Patterns
    if re.search(r"(?:(?:সব\s*)?(?:খেলা|খেলার|স্পোর্টস|ফুটবল)(?:\s*(?:খবর|সংবাদ|শিরোনাম|স্কোর|ম্যাচ|ফিক্সচার|আপডেট))?|আসন্ন\s*(?:ম্যাচ|খেলা|ফিক্সচার)|রিয়াল\s*মাদ্রিদ|বার্সেলোনা|বার্সা|ম্যানচেস্টার\s*সিটি|ম্যানচেস্টার\s*ইউনাইটেড|বায়ার্ন|আর্সেনাল|বাংলাদেশ\s*(?:ফুটবল|ম্যাচ))", text):
        return {"tool": "intel_briefing", "category": "football", "open_hud": True}

    if re.search(r"(?:ইনটেল\s*(?:ব্রিফিং|রেডিও|রিপোর্ট)|(?:ওয়ার্ল্ড|ওয়ার্ল্ড)\s*রিপোর্ট|বিশ্বের\s*খবর|টেক\s*নিউজ|টেকনোলজি\s*নিউজ|ক্রিপ্টো\s*মার্কেট)", text):
        return {"tool": "intel_briefing", "category": "all", "open_hud": True}

    return None


def handle_intel_briefing(data: dict) -> str:
    """Execute the Live Intel Briefing Radio ('RON World Report')."""
    category = str(data.get("category") or "all").lower().strip()
    open_hud = bool(data.get("open_hud", True))
    ui_only = bool(data.get("ui_only", False))
    lang = bus.get_language()

    if ui_only:
        bus.set_state(bus.EXECUTING, "INTEL · WORLD REPORT HUD")
        intel.broadcast_state(event_name="show_overlay")
        if lang == "bn":
            return "স্যার, ইন্টেল ওয়ার্ল্ড রিপোর্ট কনসোল খোলা হয়েছে।"
        return "Opening the RON World Intel Report HUD, Sir."

    res = intel.execute_intel_briefing(category=category, open_hud=open_hud, play_chime=True, lang=lang)
    return res.get("spoken", "Intel broadcast delivered, Sir.")


# ---------------------------------------------------------------------------
# The RON World Tribune (Personal AI Newspaper Editor)
# ---------------------------------------------------------------------------

def extract_tribune(command: str) -> dict | None:
    """Classify a Tribune / Newspaper request, or return None."""
    text = (command or "").strip()
    if not text:
        return None
    low = text.lower()

    # 1. Explicit read / broadcast audio: "read me today's tribune", "read today's newspaper", "listen to tribune broadcast"
    if re.search(r"\b(?:read(?:\s+me)?|broadcast|listen(?:\s+to)?|play(?:\s+audio)?|narrate|speak)\s+(?:(?:me|us|the|today(?:'s)?|ron(?:'s)?|world)\s+)*(?:tribune|newspaper|news\s*paper|paper)\b", low) or \
       re.search(r"\b(?:ron\s+)?world\s+tribune\s+(?:broadcast|radio|audio)\b", low):
        return {"action": "read", "open_hud": True}

    # 2. PDF launch: "open newspaper pdf", "show newspaper pdf", "view tribune pdf"
    if re.search(r"\b(?:open|show|display|view|launch|see|get|find)?\s*(?:(?:the|today(?:'s)?|ron(?:'s)?|world)\s+)*(?:tribune|newspaper|news\s*paper)\s+pdf\b", low):
        return {"action": "pdf", "open_hud": True}

    # 3. Open / Show HUD modal: "show newspaper", "open newspaper", "show news paper", "show me newspaper", "newspaper"
    if re.search(r"\b(?:open|show|display|view|launch|see|look\s+at)\s+(?:(?:me|us|the|today(?:'s)?|ron(?:'s)?|world)\s+)*(?:tribune|newspaper|news\s*paper|paper)\b", low) or \
       re.search(r"^(?:ron[,\\s]+)?(?:the\s+)?(?:tribune|newspaper|news\s*paper)$", low) or \
       re.search(r"\b(?:tribune|newspaper|news\s*paper)\s+(?:hud|overlay|modal|reader|console)\b", low):
        return {"action": "open", "open_hud": True}

    # 4. Generate / Publish / Refresh: "generate today's newspaper", "publish tribune", "print newspaper"
    if re.search(r"\b(?:generate|create|build|compile|publish|make|print|refresh)\s+(?:(?:the|today(?:'s)?|ron(?:'s)?|world)\s+)*(?:tribune|newspaper|news\s*paper|paper)\b", low):
        return {"action": "generate", "open_hud": True}

    # 5. Bengali natural language triggers
    if re.search(r"(?:আজকের\s*)?(?:ট্রাইবিউন|সংবাদপত্র|খবরের\s*কাগজ)\s*(?:পড়|পড়ো|শোনাও|বলো|শুনতে\s*চাই)", text):
        return {"action": "read", "open_hud": True}
    if re.search(r"(?:আজকের\s*)?(?:ট্রাইবিউন|সংবাদপত্র|খবরের\s*কাগজ)\s*(?:পিডিএফ|pdf)", text, re.IGNORECASE):
        return {"action": "pdf", "open_hud": True}
    if re.search(r"(?:আজকের\s*)?(?:ট্রাইবিউন|সংবাদপত্র|খবরের\s*কাগজ)\s*(?:খোল|খোলো|দেখাও|ওপেন\s*কর|ডিসপ্লে)", text) or \
       re.search(r"^(?:ট্রাইবিউন|সংবাদপত্র|খবরের\s*কাগজ)$", text):
        return {"action": "open", "open_hud": True}
    if re.search(r"(?:ট্রাইবিউন|সংবাদপত্র)\s*(?:তৈরি|জেনারেট|বানাও|প্রিন্ট|প্রকাশ)", text):
        return {"action": "generate", "open_hud": True}

    return None


def handle_tribune(data: dict) -> str:
    """Execute Tribune actions: read 3-min broadcast, open HUD modal, launch PDF, or force generate."""
    import tribune
    raw_action = str(data.get("action") or "open").lower().strip()
    lang = bus.get_language()

    # Normalize action
    if raw_action in ("open", "show", "view", "display", "launch", "ui", "console"):
        action = "open"
    elif raw_action in ("pdf", "open_pdf", "show_pdf"):
        action = "pdf"
    elif raw_action in ("generate", "create", "publish", "compile", "print", "refresh"):
        action = "generate"
    elif raw_action in ("read", "broadcast", "audio", "listen", "speak"):
        action = "read"
    else:
        action = "open"

    if action == "open":
        bus.set_state(bus.EXECUTING, "THE RON WORLD TRIBUNE · HUD")
        edition = tribune.build_today_tribune(force_refresh=False, generate_pdf_doc=True)

        # Ensure PDF file exists on disk
        pdf_path = edition.get("pdf_path")
        if not pdf_path or not os.path.exists(pdf_path):
            try:
                pdf_path = tribune.generate_newspaper_pdf(edition)
                edition["pdf_path"] = pdf_path
            except Exception as e:
                print(f"[Tribune] PDF auto-generation error: {e}")

        # Broadcast with event="show_overlay" and open=True
        edition_payload = dict(edition)
        edition_payload["event"] = "show_overlay"
        edition_payload["open"] = True
        bus.tribune(**edition_payload)

        if lang == "bn":
            return "স্যার, দ্য রন ওয়ার্ল্ড ট্রাইবিউন সংবাদপত্র কনসোলে খোলা হয়েছে এবং ডকুমেন্টস ফোল্ডারে পিডিএফ সংরক্ষিত আছে।"
        return "Opening today's edition of The RON World Tribune on your console, Sir. The PDF has been generated in your Documents folder."

    if action == "pdf":
        bus.set_state(bus.EXECUTING, "THE RON WORLD TRIBUNE · PDF")
        edition = tribune.build_today_tribune(force_refresh=False, generate_pdf_doc=True)
        pdf_path = edition.get("pdf_path")
        if not pdf_path or not os.path.exists(pdf_path):
            try:
                pdf_path = tribune.generate_newspaper_pdf(edition)
                edition["pdf_path"] = pdf_path
            except Exception as e:
                print(f"[Tribune] PDF generation error: {e}")

        # Launch PDF viewer
        if pdf_path and os.path.exists(pdf_path):
            try:
                if hasattr(os, "startfile"):
                    os.startfile(pdf_path)
            except Exception as e:
                print(f"[Tribune] Failed to startfile {pdf_path}: {e}")

        edition_payload = dict(edition)
        edition_payload["event"] = "show_overlay"
        edition_payload["open"] = True
        bus.tribune(**edition_payload)

        if lang == "bn":
            return "স্যার, আজকের ট্রাইবিউন সংবাদপত্র পিডিএফ ফাইলে খোলা হয়েছে।"
        return "Opening the PDF broadsheet for today's RON World Tribune, Sir."

    if action == "generate":
        bus.set_state(bus.EXECUTING, "THE RON WORLD TRIBUNE · PUBLISHING")
        edition = tribune.build_today_tribune(force_refresh=True, generate_pdf_doc=True)
        edition_payload = dict(edition)
        edition_payload["event"] = "show_overlay"
        edition_payload["open"] = True
        bus.tribune(**edition_payload)

        if lang == "bn":
            return "স্যার, দ্য রন ওয়ার্ল্ড ট্রাইবিউন-এর নতুন সংস্করণ সংকলিত ও পিডিএফ প্রকাশ করা হয়েছে।"
        return f"Today's edition of The RON World Tribune has been compiled across {edition.get('source_count', 50)} sources and saved to your Documents folder, Sir."

    # Explicit action == "read" -> 3-minute executive broadcast
    res = tribune.read_tribune_broadcast(force_refresh=False, lang=lang)
    return res.get("script", "Tribune broadcast completed, Sir.")


# ---------------------------------------------------------------------------
# Cyber Watchdog & Network Radar
# ---------------------------------------------------------------------------

def extract_netradar(command: str) -> dict | None:
    """Classify a network radar or Wi-Fi security scan request, or return None."""
    text = (command or "").strip()
    if not text:
        return None
    low = text.lower()

    if re.search(r"\b(?:(?:open|show|display|launch|view)\s+(?:the\s+)?(?:network\s+)?(?:radar|watchdog|scanner)|scan(?:\s+(?:the|local|my))?\s+(?:network|wifi|wi-fi|lan|subnet)|network\s+radar|cyber\s+watchdog|who(?:\s+is)?\s+on\s+(?:my\s+)?(?:wifi|wi-fi|network)|network\s+security|scan\s+wifi|wifi\s+radar)\b", low):
        fast = not ("full" in low or "deep" in low or "thorough" in low)
        return {"fast": fast, "open_hud": True}

    # Bengali triggers: "ওয়াইফাই স্ক্যান করো", "নেটওয়ার্ক স্ক্যান", "ওয়াইফাই রাডার"
    if any(w in text for w in ["ওয়াইফাই স্ক্যান", "নেটওয়ার্ক স্ক্যান", "ওয়াইফাই রাডার", "নেটওয়ার্ক রাডার", "রাডার খোলো", "রাডার ওপেন", "কে কে ওয়াইফাই ব্যবহার করছে", "লোকাল নেটওয়ার্ক"]):
        return {"fast": True, "open_hud": True}

    return None


def handle_network_scan(data: dict) -> str:
    """Perform network sweep and deliver tactical spoken debrief."""
    fast = bool(data.get("fast", True))
    lang = bus.get_language()

    bus.set_state(bus.EXECUTING, "CYBER WATCHDOG · RADAR SWEEP")
    # Emit event immediately so HUD radar overlay opens with the live sweep animation
    bus.netradar(event="show_overlay", open=True, scanning=True, status="SWEEPING SUBNET")

    res = netradar.scan_network(fast=fast)
    
    # Broadcast completed radar sweep payload with open=True so HUD renders findings
    res_payload = dict(res)
    res_payload["event"] = "show_overlay"
    res_payload["open"] = True
    bus.netradar(**res_payload)
    
    devs = res.get("devices", [])
    rogues = res.get("rogue_devices", [])
    open_vulns = [p for p in res.get("ports_audit", []) if p.get("is_vulnerable")]

    if lang == "bn":
        if rogues:
            return f"স্যার, লোকাল নেটওয়ার্কে {len(devs)}টি ডিভাইস পাওয়া গেছে। সতর্কতা: {len(rogues)}টি অপরিচিত ডিভাইস সনাক্ত হয়েছে।"
        return f"স্যার, নেটওয়ার্ক রাডার স্ক্যান সম্পন্ন হয়েছে। {len(devs)}টি সক্রিয় ডিভাইস পাওয়া গেছে এবং সব পোর্ট নিরাপদ রয়েছে।"

    # English debrief
    if open_vulns:
        return f"Perimeter scan complete, Sir. Found {len(devs)} active devices. Warning: detected open vulnerable ports on workstation. Cyber Watchdog console is open on HUD."
    if rogues:
        rogue_names = ", ".join([d["vendor"] for d in rogues[:2]])
        return f"Network radar sweep complete, Sir. Found {len(devs)} connected devices. Warning: {len(rogues)} unrecognized device detected from vendor {rogue_names}. Details displayed on HUD."
    return f"Perimeter sweep complete, Sir. Verified {len(devs)} active devices across subnet {res.get('subnet')}. All trusted, workstation ports secure."


# ---------------------------------------------------------------------------
# Smart PDF & Document Intelligence Engine
# ---------------------------------------------------------------------------

def extract_docintel(command: str) -> dict | None:
    """Classify a Document Intelligence request, or return None."""
    text = (command or "").strip()
    if not text:
        return None
    low = text.lower()

    # 1. Bengali triggers:
    # "এই পিডিএফটা সামারি করো", "পিডিএফ সামারি", "ডকুমেন্ট বিশ্লেষণ", "টেবিল এক্সট্র্যাক্ট করো", "ডক ইন্টেল খোলো"
    if "পিডিএফ" in text or "ডকুমেন্ট" in text or "ডক ইন্টেল" in text or (("সামারি" in text or "বিশ্লেষণ" in text or "সারাংশ" in text) and any(w in text for w in ["ফাইল", "পিডিএফ", "কাগজ", "ডকুমেন্ট", "দলিল"])):
        if any(w in text for w in ["টেবিল", "এক্সট্র্যাক্ট", "এক্সেল"]):
            return {"action": "extract_tables"}
        if any(w in text for w in ["কত", "টাকা", "খরচ", "হিসাব", "যোগফল"]):
            return {"action": "ask", "query": text}
        if any(w in text for w in ["কী বলা", "কি বলা", "ধারা", "সেকশন", "ওয়ারেন্টি", "শর্ত"]):
            return {"action": "ask", "query": text}
        if any(w in text for w in ["সামারি", "বিশ্লেষণ", "সারাংশ", "পড়ো", "দেখাও"]):
            return {"action": "summarize"}
        return {"action": "open"}

    # 2. Extract tables / Excel CSV:
    if re.search(r"\b(?:extract|export|save|dump|convert)\s+(?:all\s+)?(?:the\s+)?tables?\s+(?:from|in|of)?\s*(?:this|the)?\s*(?:pdf|doc|document|statement)?\s*(?:to|into|as)?\s*(?:an?\s+)?(?:excel|csv|spreadsheet)?\b", low):
        return {"action": "extract_tables"}

    # 3. Calculate / Financial Math:
    # "calculate the total expenses in this bank statement", "calculate total expenses", "sum up all amounts"
    calc_m = re.search(r"\b(?:calculate|compute|sum(?:\s+up)?|add(?:\s+up)?|total|what\s+is\s+the\s+total)\s+(?:the\s+)?(?:total\s+)?(?:expenses|spending|revenue|income|tax(?:es)?|amounts?|transactions?|cost|balance|sum)\b", low)
    if calc_m:
        return {"action": "ask", "query": text}

    # 4. Specific section / warranty / topic Q&A on active document:
    # "what does section 4 say about warranty", "what does the document say about ...", "check section 3"
    if re.search(r"\b(?:what\s+does\s+(?:section|page|clause|article|paragraph|the\s+document|the\s+pdf)\s+|according\s+to\s+(?:section|page|the\s+document)|find\s+in\s+(?:the\s+)?(?:pdf|document)|where\s+does\s+it\s+mention)\b", low):
        return {"action": "ask", "query": text}

    # 5. Summarize / Analyze / Executive Digest:
    if re.search(r"\b(?:summarize|analyse|analyze|debrief|digest|give\s+me\s+(?:a\s+)?summary\s+of|explain)\s+(?:this|the|active)?\s*(?:pdf|docx?|document|paper|manual|contract|statement)\b", low) or \
       re.search(r"^(?:ron[,\\s]+)?(?:summarize|analyze)\s+(?:this|the)?\s*(?:pdf|document)$", low):
        return {"action": "summarize"}

    # 6. Open / show doc intel overlay:
    if re.search(r"\b(?:open|show|display|launch|view)\s+(?:the\s+)?(?:doc(?:ument)?\s+intel(?:ligence)?|pdf\s+(?:reader|analyzer|engine|intel)|document\s+reader)\b", low) or \
       re.search(r"^(?:ron[,\\s]+)?(?:doc\s*intel|smart\s*pdf|document\s*intelligence)$", low):
        return {"action": "open"}

    return None


def handle_docintel(data: dict) -> str:
    """Execute Document Intelligence requests: summarize, ask, extract tables, or open HUD."""
    action = data.get("action", "open")
    query = data.get("query", "")
    file_path = data.get("file_path", "")
    lang = bus.get_language()

    active = docintel.get_active_document()

    # If file_path provided and not currently active, parse it first
    if file_path and os.path.isfile(file_path):
        try:
            docintel.parse_document(file_path=file_path)
            active = docintel.get_active_document()
        except Exception as e:
            return f"Failed to ingest document: {e}"

    if action == "open":
        bus.set_state(bus.EXECUTING, "DOCUMENT INTELLIGENCE · HUD")
        bus.docintel(event="show_overlay", open=True, status="READY")
        if lang == "bn":
            return "স্যার, ডকুমেন্ট ইন্টেলিজেন্স ইন্টারফেস ওপেন করা হয়েছে। যে কোনো পিডিএফ বা ডকুমেন্ট ড্র্যাগ ও ড্রপ করতে পারেন।"
        return "Opening Document Intelligence console on HUD, Sir. Drag and drop any PDF, Word document, or spreadsheet."

    if not active:
        bus.set_state(bus.EXECUTING, "DOCUMENT INTELLIGENCE · HUD")
        bus.docintel(event="show_overlay", open=True, status="IDLE")
        if lang == "bn":
            return "স্যার, বর্তমানে কোনো সক্রিয় ডকুমেন্ট লোড করা নেই। দয়া করে কনসোলে একটি পিডিএফ বা ফাইল দিন।"
        return "No active document is currently loaded, Sir. I have opened the Document Intelligence console so you can drag and drop your file."

    if action == "summarize":
        bus.set_state(bus.EXECUTING, "DOCUMENT INTELLIGENCE · DIGEST")
        bus.docintel(event="show_overlay", open=True, status="ANALYZING")
        digest = docintel.generate_executive_digest(active.get("id"))
        bus.docintel(event="show_overlay", open=True, status="READY")
        return digest.get("spoken_debrief") or "Executive digest complete, Sir. Key takeaways and critical metrics are displayed on your HUD."

    if action == "extract_tables":
        bus.set_state(bus.EXECUTING, "DOCUMENT INTELLIGENCE · EXPORT")
        exported = docintel.export_tables_to_csv(active.get("id"))
        bus.docintel(event="show_overlay", open=True, status="READY", exported_tables=exported)
        if not exported:
            if lang == "bn":
                return "স্যার, এই ডকুমেন্টে কোনো সুনির্দিষ্ট স্ট্রাকচার্ড টেবিল পাওয়া যায়নি।"
            return "No structured data tables were detected in the active document to export, Sir."
        if lang == "bn":
            return f"স্যার, ডকুমেন্ট থেকে মোট {len(exported)}টি টেবিল এক্সেল সিএসভি ফরম্যাটে আপনার ডকুমেন্টস ফোল্ডারে সেভ করা হয়েছে।"
        return f"Exported {len(exported)} table(s) from {active.get('filename')} into clean CSV spreadsheets in your Documents folder, Sir."

    if action == "ask":
        bus.set_state(bus.EXECUTING, "DOCUMENT INTELLIGENCE · QUERY")
        bus.docintel(event="show_overlay", open=True, status="ANALYZING")
        res = docintel.ask_document(query, active.get("id"))
        bus.docintel(event="show_overlay", open=True, status="READY")
        return res.get("spoken_answer") or res.get("answer") or "Analysis complete, Sir. Full breakdown rendered on HUD."

    return "Document request completed, Sir."


# ---------------------------------------------------------------------------
# Clean Slate Workspace & Downloads Auto-Organizer
# ---------------------------------------------------------------------------

def extract_clean_slate(command: str) -> dict | None:
    """Classify a Clean Slate downloads/documents/desktop organization request, or return None."""
    text = (command or "").strip()
    if not text:
        return None
    low = text.lower()

    # English patterns:
    # "clean slate", "organize document folder", "organize documents", "clean documents folder",
    # "clean downloads", "organize downloads", "clean my downloads", "tidy downloads", "sort downloads"
    # "clean desktop", "organize desktop", "clean my desktop", "tidy desktop", "sort desktop"
    if re.search(r"\b(?:clean\s+slate|declutter\s+(?:my\s+)?(?:downloads?|documents?|desktop|files?)|tidy\s+(?:up\s+)?(?:my\s+)?(?:downloads?|documents?|desktop|files?)(?:\s+folder)?|organize\s+(?:my\s+)?(?:downloads?|documents?|desktop|files?)(?:\s+folder)?|sort\s+(?:my\s+)?(?:downloads?|documents?|desktop|files?)(?:\s+folder)?|clean\s+(?:up\s+)?(?:my\s+)?(?:downloads?|documents?|desktop)(?:\s+folder)?)\b", low):
        if "document" in low or "doc" in low:
            target = "documents"
        elif "desktop" in low:
            target = "desktop"
        else:
            target = "downloads"
        return {"target": target}

    # Bengali patterns: "ডকুমেন্ট ফোল্ডার পরিষ্কার করো", "ডকুমেন্ট সাজাও", "ডাউনলোড ফোল্ডার পরিষ্কার করো", "ডেস্কটপ পরিষ্কার করো"
    if any(w in text for w in ["পরিষ্কার করো", "পরিষ্কার", "সাজাও", "গোছাও"]):
        if any(w in text for w in ["ডকুমেন্ট", "দলিল", "কাগজপত্র"]):
            return {"target": "documents"}
        if "ডেস্কটপ" in text:
            return {"target": "desktop"}
        if any(w in text for w in ["ডাউনলোড", "ফাইল"]):
            return {"target": "downloads"}

    return None


def handle_clean_slate(data: dict) -> str:
    """Execute Clean Slate directory organization and return spoken response."""
    target = data.get("target", "downloads")
    lang = bus.get_language()
    res = clean_slate.organize_directory(target=target, dry_run=False, lang=lang)
    return res.get("spoken", "Clean slate complete, Sir.")


# ---------------------------------------------------------------------------
# Email sending
# ---------------------------------------------------------------------------

EMAIL_COMPOSE_PROMPT = """You are an executive email assistant for Ron (Ifteqhar).
Your job is to compose a thoughtful, articulate, context-appropriate, and well-written email based on the user's intent or instruction.

Strict Guidelines:
1. Tone & Voice:
   - For personal messages (birthdays, greetings, celebrations, check-ins): Warm, thoughtful, celebratory, genuine, and articulate.
   - For professional/work messages (meetings, updates, proposals, questions): Courteous, crisp, professional, and well-structured.
2. Structure & Length:
   - Write 1 to 3 clear, engaging body paragraphs that convey the message with substance, charm, and depth.
   - Never output single-sentence or lazy one-line summaries when the intent is a warm wish or meaningful message.
3. Formatting:
   - DO NOT include salutations or greetings (e.g., 'Dear ...', 'Hello ...', 'Hi ...'). The system's HTML template automatically adds the appropriate personalized greeting.
   - DO NOT include sign-offs or signatures (e.g., 'Best regards', 'Sincerely', 'Ron'). The template automatically appends the signature.
   - DO NOT output markdown code fences (```).
4. Output Format:
   - You MUST output a valid JSON object with exactly two keys: "subject" and "body".
   Example:
   {"subject": "Warmest Birthday Wishes! 🎉", "body": "Wishing you an extraordinary birthday filled with joy, laughter, and great memories.\\n\\nMay this upcoming year bring you continued health, happiness, and outstanding success in everything you pursue!"}
5. Language:
   - If the requested language is Bengali ('bn'), output fluent, polite, elegant Bengali for both subject and body. Otherwise, use natural, expressive English.
"""


def generate_composed_email(recipient: str, user_intent: str, current_subject: str = "", lang: str = "en") -> tuple[str, str]:
    """Use the LLM to compose a high-quality subject and body based on the user's intent."""
    lang_instruction = "Bengali (polite, fluent, elegant)" if lang == "bn" else "English (expressive, professional, warm)"
    user_prompt = (
        f"Recipient: {recipient or 'Recipient'}\n"
        f"Provided / Initial Subject: {current_subject or '(None provided)'}\n"
        f"User's Email Intent / Message: {user_intent or 'Send greetings'}\n"
        f"Target Language: {lang_instruction}\n\n"
        f"Compose an email subject and body in JSON format: {{\"subject\": \"...\", \"body\": \"...\"}}"
    )

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": EMAIL_COMPOSE_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=500,
            timeout=30,
        )
        raw_text = (response.choices[0].message.content or "").strip()
        
        # Parse JSON response
        data = None
        if raw_text.startswith("```"):
            clean_json = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.IGNORECASE)
            clean_json = re.sub(r"\s*```$", "", clean_json).strip()
        else:
            clean_json = raw_text

        # Try to find JSON object substring if surrounded by other text
        json_m = re.search(r"\{.*\}", clean_json, re.DOTALL)
        if json_m:
            try:
                import json
                data = json.loads(json_m.group(0))
            except Exception:
                data = None

        if isinstance(data, dict):
            out_subj = str(data.get("subject") or "").strip()
            out_body = str(data.get("body") or "").strip()
            out_body = clean_reply_body(out_body)
            if out_body:
                final_subj = out_subj or current_subject or "Message"
                return final_subj, out_body
        else:
            # If not JSON, clean the raw text as body
            cleaned = clean_reply_body(clean_json)
            if cleaned:
                final_subj = current_subject or (user_intent[:35].strip().title() or "Message")
                return final_subj, cleaned
    except Exception as e:
        print(f"[email_compose] LLM generation error ({e}), using fallback.")

    fallback_subj = current_subject or (user_intent[:35].strip().title() if user_intent else "Message")
    fallback_body = user_intent or fallback_subj
    return fallback_subj, fallback_body


def handle_send_email(data: dict) -> str:
    """Send an email. Always crafts and polishes the email content via LLM."""
    to = str(data.get("to") or "").strip()
    raw_subject = str(data.get("subject") or "").strip()
    raw_body = str(data.get("body") or "").strip()
    lang = bus.get_language()

    if not to:
        return "I need a recipient for that email, Sir." if lang != "bn" else "স্যার, ইমেইল পাঠানোর জন্য প্রাপকের নাম বা ঠিকানা প্রয়োজন।"

    # Check recipient resolution first so we don't give a misleading SMTP error
    resolved = email_notify.resolve_recipient(to)
    if not resolved:
        bus.activity(f"Recipient not found: {to}", "fail")
        if lang == "bn":
            return f"'{to}'-এর কোনো ইমেইল ঠিকানা খুঁজে পাইনি, স্যার। contacts.json ফাইলে যোগ করুন অথবা সম্পূর্ণ ইমেইল ঠিকানা দিন।"
        return f"I could not find an email address for '{to}', Sir. Please add them to contacts.json or specify their full email address."

    recipient_name = to.title() if ("@" not in to) else ""
    user_intent = raw_body or raw_subject or "Greetings"

    # Always generate/polish the email subject and body via LLM
    bus.set_state(bus.EXECUTING, f"DRAFTING EMAIL · {to.upper()[:30]}")
    bus.activity(f"Composing email for {recipient_name or to} with AI", "pending")
    subject, body = generate_composed_email(
        recipient=recipient_name or to,
        user_intent=user_intent,
        current_subject=raw_subject,
        lang=lang,
    )

    bus.set_state(bus.EXECUTING, f"SEND EMAIL · {to.upper()[:40]}")
    bus.activity(f"Sending email to {resolved}", "pending")
    ok = email_notify.send_email(to, subject, body, recipient_name=recipient_name)
    if ok:
        bus.activity("Email sent", "ok")
        display_name = recipient_name or to
        if lang == "bn":
            return f"{display_name}-কে ইমেইল সফলভাবে পাঠানো হয়েছে, স্যার।"
        return f"Email sent to {display_name} regarding '{subject}', Sir."

    bus.activity("Email send failed", "fail")
    if lang == "bn":
        return "ইমেইল পাঠানো সম্ভব হয়নি, স্যার। config.json-এর SMTP সেটিংস পরীক্ষা করুন।"
    return "I could not send that email, Sir. Please check the SMTP configuration or network connection."


def extract_send_email(command: str):
    """Classify an email request, or return None.

    Supports:
    1. Explicit full specification:
       "send an email to <to> with subject <subj> and body <body>"
    2. Direct message/greeting to contact:
       "send good morning to thanos", "send hello to hassan"
    3. Natural intent:
       "send (an) email to <to> saying <msg>", "email <to> saying <msg>"
    4. Inverted:
       "send <to> an email saying <msg>"
    5. Bengali:
       "থানোসকে গুড মর্নিং পাঠাও"
    """
    text = (command or "").strip()
    if not text:
        return None

    clean_text = text.rstrip("!.?")
    low = clean_text.lower()

    # 1. Traditional explicit: requires "to", "subject", "body"
    if re.search(r"\b(send|email|mail|e-mail)\b", low) and re.search(r"\bsubject\b", low) and re.search(r"\bbody\b", low):
        to_m = re.search(r"\bto\s+([^\s,;]+)", clean_text, re.IGNORECASE)
        subj_m = re.search(r"\bsubject\s+(.*?)(?:\s+(?:and\s+)?body\s+|\s+body\s+|\s*$)", clean_text, re.IGNORECASE)
        body_m = re.search(r"\bbody\s+(.*)$", clean_text, re.IGNORECASE)
        if to_m:
            to_val = to_m.group(1).strip()
            if to_val.lower().endswith("with"):
                to_val = to_val[:-4].strip()
            result = {"to": to_val}
            if subj_m:
                result["subject"] = subj_m.group(1).strip()
            if body_m:
                result["body"] = body_m.group(1).strip()
            if result.get("to") and result.get("subject") and result.get("body"):
                return result

    # 2. "send (an) email/mail/message to <recipient> saying/with <message>"
    m2 = re.match(
        r"^(?:send\s+(?:an?\s+)?(?:email|mail|message)\s+to|email|mail)\s+([a-zA-Z0-9_.@\-]+)\s+(?:saying|and\s+say|with\s+message|that|:)\s+(.+)$",
        clean_text,
        re.IGNORECASE,
    )
    if m2:
        to_val = m2.group(1).strip()
        body_val = m2.group(2).strip().strip("\"'")
        subj = body_val[:35].strip().title()
        return {"to": to_val, "subject": subj, "body": body_val}

    # 3. Inverted: "send <recipient> (an) email/message saying <message>"
    m3 = re.match(
        r"^send\s+([a-zA-Z0-9_.@\-]+)\s+(?:an?\s+)?(?:email|mail|message)\s+(?:saying|and\s+say|with\s+message|that|:)\s+(.+)$",
        clean_text,
        re.IGNORECASE,
    )
    if m3:
        to_val = m3.group(1).strip()
        body_val = m3.group(2).strip().strip("\"'")
        subj = body_val[:35].strip().title()
        return {"to": to_val, "subject": subj, "body": body_val}

    # 4. Direct conversational greeting / message: "send <message> to <recipient>"
    # e.g. "send good morning to thanos", "send hello to hassan"
    m4 = re.match(
        r"^(?:send|email|mail)\s+(?:a\s+(?:quick\s+)?(?:message|email|mail|greeting|note)\s+(?:saying\s+)?)?(.+?)\s+to\s+([a-zA-Z0-9_.@\-]+)$",
        clean_text,
        re.IGNORECASE,
    )
    if m4:
        body_val = m4.group(1).strip().strip("\"'")
        to_val = m4.group(2).strip()
        if len(to_val) >= 2 and to_val.lower() not in ("it", "this", "that", "there", "here"):
            resolved = email_notify.resolve_recipient(to_val)
            if resolved or "@" in to_val:
                subj = body_val[:35].strip().title()
                return {"to": to_val, "subject": subj, "body": body_val}

    # 5. Bengali patterns: "থানোসকে গুড মর্নিং পাঠাও"
    bn_m = re.search(r"([a-zA-Z0-9_.@\-]+|থানোস|হাসান)(?:কে|-কে)\s*(?:ইমেইল|বার্তা|মেসেজ)?\s*(?:পাঠাও|বলো)\s*[:,-]?\s*(.+)$", clean_text)
    if bn_m:
        to_raw = bn_m.group(1).strip()
        body_val = bn_m.group(2).strip()
        to_map = {"থানোস": "thanos", "হাসান": "hassan"}
        to_val = to_map.get(to_raw, to_raw)
        return {"to": to_val, "subject": body_val[:30].strip(), "body": body_val}

    return None


# ---------------------------------------------------------------------------
# Scheduled reminders
# ---------------------------------------------------------------------------

def extract_reminder(command: str):
    """Classify a reminder request, or return None.

    Returns {"at": "5:00 PM", "message": "study"} or None.
    """
    text = (command or "").lower().strip()
    if not text:
        return None
    if not re.search(r"\b(remind|reminder|notify|notification|alarm)\b", text):
        return None

    # Extract the time phrase.
    time_m = re.search(
        r"\b(?:at|for)?\s*(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)|"
        r"\d{1,2}(?::\d{2})?)\b",
        text, re.IGNORECASE)
    at_str = time_m.group(1).strip() if time_m else None

    # Extract the message -- everything after the time phrase, or after the
    # trigger verb if no time was found.
    msg = ""
    if time_m and time_m.end() < len(text):
        msg = text[time_m.end():].strip()
        # Strip leading filler: "for", "to", "about", etc.
        msg = re.sub(r"^(?:for|to|about|that|me)\s+", "", msg).strip()
    if not msg:
        msg = "your reminder"

    return {"at": at_str, "message": msg}


def run_reminder(spec: dict) -> str:
    """Shared path: parse the time, arm the reminder, return the spoken line."""
    at_str = (spec.get("at") or "").strip()
    message = (spec.get("message") or "").strip()
    if not at_str:
        return "I need a time for that reminder, Sir."

    when = email_notify.parse_time(at_str)
    if when is None:
        return f"I could not understand the time '{at_str}', Sir. Try '5 pm' or '17:30'."

    import clock as _clock
    tz = _clock.tz()
    at = email_notify.next_occurrence(when, tz)

    bus.set_state(bus.EXECUTING, f"REMINDER · {at.strftime('%I:%M %p').upper()}")
    bus.activity(f"Setting reminder for {at.strftime('%I:%M %p')}: {message}", "pending")
    reminder_spec = email_notify.schedule_reminder(at, message)
    bus.activity("Reminder armed", "ok")
    return email_notify.describe(reminder_spec)


def handle_set_reminder(data: dict) -> str:
    """The LLM's fallback route for reminder commands."""
    at = str(data.get("at") or "").strip()
    message = str(data.get("message") or "").strip()
    return run_reminder({"at": at, "message": message})


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


def write_webpage(topic: str) -> str:
    """Second LLM call: a dedicated writer turn that produces a single
    self-contained .html file (inline CSS + JS) for the requested topic.

    The same two-stage separation as the PDF path: the assistant turn stays
    terse, so a dedicated writer turn produces the full page body.

    A single 6000-token response is not enough for a multi-section page with
    inline CSS and JS, so when the model hits the token ceiling the content is
    appended and the request is continued until the page is complete (or a
    generous chunk ceiling is reached).
    """
    print(f"[Writing webpage on '{topic}' -- this usually takes 30-90 seconds, "
          f"longer for larger pages...]")
    bus.set_state(bus.THINKING, f"COMPOSING WEBPAGE · {topic.upper()[:48]}")
    bus.activity(f"Writing webpage: {topic}", "pending")

    system = {"role": "system", "content": WEBPAGE_WRITER_PROMPT}
    user = {"role": "user", "content": f"Create the webpage about: {topic}"}
    messages = [system, user]

    content = ""
    max_chunks = 4  # initial + up to 3 continuations (~24k tokens)
    for chunk in range(max_chunks):
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=32758,
            # Without this the SDK waits its 600s default, so a stalled request looks
            # like Ron has simply died. Long pages legitimately need a few minutes.
            timeout=30000,
        )
        choice = response.choices[0]
        piece = (choice.message.content or "").strip()

        # Some models wrap the result in a code fence despite the prompt.
        if not content and piece.startswith("```"):
            parts = piece.split("```")
            if len(parts) >= 2:
                piece = parts[1]
                if piece.lower().startswith("html"):
                    piece = piece[4:]
                elif piece.lower().startswith("js"):
                    piece = piece[2:]
                piece = piece.strip()

        content += piece

        if getattr(choice, "finish_reason", None) != "length":
            # The model produced a complete page on its own.
            break

        # The model hit the token ceiling. Append what we have as an assistant
        # turn and ask it to continue from exactly where it left off, without
        # repeating any content. This is how multi-section pages get finished.
        print(f"[Webpage chunk {chunk + 1} hit the token ceiling; requesting "
              f"the rest...]")
        messages.append({"role": "assistant", "content": piece})
        messages.append({
            "role": "user",
            "content": (
                "Continue the HTML from exactly where you left off. Output only "
                "the remaining HTML, starting mid-tag if necessary. Do not "
                "repeat any content you have already written. Close all open "
                "tags so the document is complete."
            ),
        })

    # If the final chunk still ended on length, trim back to the last clean
    # block boundary so we do not ship a page cut mid-tag.
    if getattr(choice, "finish_reason", None) == "length":
        cut = max(content.rfind("</div>"), content.rfind("</section>"),
                  content.rfind("</body>"), content.rfind("\n\n"))
        if cut > len(content) * 0.6:
            content = content[:cut + 1].rstrip()
        print("[Webpage exhausted its token budget; trimmed to the last "
              "complete block.]")

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


def handle_generate_webpage(data: dict) -> str:
    """Two-stage webpage: the assistant gives intent, a writer turn gives the HTML."""
    topic = (data.get("topic") or data.get("subject") or data.get("about") or "").strip()
    content = (data.get("content") or "").strip()
    file_name = (data.get("file_name") or data.get("filename") or "").strip()

    if not re.sub(r"[\s.…\-_*#]", "", content):
        content = ""

    verbatim = bool(content) and (not topic or len(content) >= 200)

    if not verbatim:
        if not topic:
            topic = (file_name.replace("_", " ").replace("-", " ").strip()
                     or content or "the requested subject")
        speak(f"Designing your webpage on {topic}, Sir. Give me a moment.")
        try:
            content = write_webpage(topic)
        except Exception as e:
            print(f"[Webpage generation failed: {e}]")
            return f"I could not write the webpage, Sir. {e}"

        if len(content) < 200:
            return "The webpage came back too short, so I did not save it. Please try again, Sir."

    if not file_name:
        file_name = re.sub(r"[^\w\s-]", "", topic or "page").strip()
        file_name = re.sub(r"\s+", "_", file_name).lower()[:60]

    title = topic.title() if topic else file_name.replace("_", " ").title()
    bus.set_state(bus.EXECUTING, "RENDERING WEBPAGE")
    bus.activity(f"Rendering webpage: {file_name}.html", "pending")
    result = generate_webpage(file_name, content, title=title)
    bus.activity("Webpage saved to Webpages", "ok")
    return result


def handle_generate_image(data: dict) -> str:
    """Single-stage image generation: the prompt is all the model needs."""
    prompt = (data.get("prompt") or data.get("description") or "").strip()
    file_name = (data.get("file_name") or data.get("filename") or "").strip()

    if not prompt:
        return "I need a description of what to draw, Sir. What should the image show?"

    bus.set_state(bus.EXECUTING, "GENERATING IMAGE")
    bus.activity(f"Generating image: {prompt}", "pending")
    result = generate_image(prompt, file_name=file_name)
    bus.activity("Image saved to Images", "ok")
    return result


def is_bangla_mode_trigger(command: str) -> bool:
    """Check if the user is asking to activate or switch to Bangla mode."""
    text = (command or "").strip().lower()
    if not text:
        return False
    if any(neg in text for neg in ["off", "turn off", "shut off", "disable", "stop", "বন্ধ", "অফ"]):
        return False
    bangla_patterns = [
        r"\b(?:turn\s+on|activate|switch\s+to|enable|start)\s+bangla(?:\s+mode)?\b",
        r"\bbangla\s+mode\s+on\b",
        r"\bbangla\s+mode\b",
        r"বাংলা\s*মোড",
    ]
    return any(re.search(pat, text, re.IGNORECASE) for pat in bangla_patterns)


def is_english_mode_trigger(command: str) -> bool:
    """Check if the user is asking to activate English mode or turn off Bangla mode."""
    text = (command or "").strip().lower()
    if not text:
        return False
    english_patterns = [
        r"\b(?:turn\s+off|disable|stop)\s+bangla(?:\s+mode)?\b",
        r"\bbangla\s+mode\s+off\b",
        r"\b(?:turn\s+on|activate|switch\s+to|enable|start)\s+english(?:\s+mode)?\b",
        r"\benglish\s+mode\s+on\b",
        r"\benglish\s+mode\b",
        r"বাংলা\s*মোড\s*(?:বন্ধ|অফ)",
        r"(?:ইংলিশ|ইংরেজি)\s*মোড",
    ]
    return any(re.search(pat, text, re.IGNORECASE) for pat in english_patterns)


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

    # Language mode switching
    if is_bangla_mode_trigger(user_input):
        voice.set_language("bn")
        bus.set_language("bn")
        bus.set_state(bus.EXECUTING, "BANGLA MODE (বাংলা)")
        bus.activity("Switched to Bangla mode", "ok")
        speak("আসসালামু আলাইকুম স্যার। বাংলা মোড চালু করা হয়েছে। আমি আপনাকে কীভাবে সাহায্য করতে পারি?")
        return

    if is_english_mode_trigger(user_input):
        voice.set_language("en")
        bus.set_language("en")
        bus.set_state(bus.EXECUTING, "ENGLISH MODE")
        bus.activity("Switched to English mode", "ok")
        speak("English mode activated, Sir. How may I assist you?")
        return

    # Playback is a clear imperative and needs no judgement from the model.
    # Besides being quicker, this guarantees that "play Shape of You" launches
    # YouTube even if a provider answers with a friendly confirmation instead
    # of the JSON protocol.
    youtube_query = extract_youtube(user_input)
    if youtube_query:
        bus.set_state(bus.EXECUTING, "YOUTUBE · " + youtube_query.upper()[:48])
        bus.activity(f"Playing on YouTube: {youtube_query}", "pending")
        result = play_youtube(youtube_query)
        bus.activity("YouTube playback requested", "ok")
        speak(result)
        return

    # Weather Station holographic HUD popup & executive brief.
    ws_intent = extract_weather_station(user_input)
    if ws_intent:
        reply = handle_weather_station(ws_intent)
        speak(reply)
        return

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

    # Autonomous Web Agent & Auto-Pilot ("Browse and buy/find"), answered directly.
    # Placed before extract_find, extract_folder, and extract_website so price comparisons
    # ("find cheapest..."), shopping deals ("browse best deal on..."), and multi-step
    # web automation routes to the headless browser agent rather than local disk search
    # or opening bare website homepages.
    ap = extract_autopilot(user_input)
    if ap:
        reply = handle_autopilot(ap)
        speak(reply)
        return

    # Neural Long-Term Memory & Knowledge Graph ("Remember & Recall"), answered directly.
    # Placed before extract_find, extract_folder, and extract_website so remember/recall/forget
    # commands ("remember that...", "what is my wifi password?", "forget...") route to the
    # local neural memory engine rather than looking for folders, files, or websites.
    mem_spec = extract_memory(user_input)
    if mem_spec:
        action = mem_spec.get("action")
        if action == "remember":
            reply = handle_remember(mem_spec)
        elif action == "recall":
            reply = handle_recall(mem_spec)
        elif action == "forget":
            reply = handle_forget(mem_spec)
        elif action == "show":
            reply = handle_show_memories(mem_spec)
        else:
            reply = handle_memory(mem_spec)
        speak(reply)
        return

    # Executive Coach & Daily Standup ("Standup & Focus"), answered directly
    coach_spec = extract_coach(user_input)
    if coach_spec:
        action = coach_spec.get("action")
        if action == "start":
            reply = handle_coach_standup(coach_spec)
        elif action == "complete":
            reply = handle_coach_complete(coach_spec)
        elif action == "status":
            reply = handle_coach_status(coach_spec)
        elif action == "debrief":
            reply = handle_coach_debrief(coach_spec)
        elif action == "blocker":
            reply = handle_coach_blocker(coach_spec)
        else:
            reply = handle_coach(coach_spec)
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

    # Foreground browser reads and clicks are deliberately direct. This avoids
    # a model reply in prose preventing Ron from operating the active Brave tab.
    browser_command = extract_browser_action(user_input)
    if browser_command:
        label = browser_command.get("action", "browser").upper()
        bus.set_state(bus.EXECUTING, f"BROWSER · {label}")
        bus.activity(f"Browser {browser_command.get('action')}", "pending")
        reply = handle_browser_action(browser_command)
        bus.activity("Browser action handled", "ok")
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
        # Open website in user's browser (Brave) as a new tab in the existing window.
        result = open_website(site)
        bus.activity("Website opened", "ok")
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

    # Timer cancel / stop, answered directly
    if re.search(r"\b(cancel|stop|abort|clear|kill)\s+(the\s+)?timer\b", user_input.lower()):
        if _active_timer is not None:
            timer.cancel(_active_timer)
            _active_timer = None
            bus.set_state(bus.EXECUTING, "TIMER CANCELLED")
            bus.activity("Timer cancelled", "ok")
            speak("Timer has been cancelled, Sir.")
            return
        else:
            bus.timer(status="cancelled", ok=True, remaining_sec=0)
            speak("No active timer is running, Sir.")
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

    # Reply to latest email, answered directly
    rep_mail = extract_reply_email(user_input)
    if rep_mail:
        bus.set_state(bus.EXECUTING, "REPLY EMAIL")
        bus.activity("Replying to email", "pending")
        reply = handle_reply_email(rep_mail)
        speak(reply)
        return

    # Check incoming emails or read newest email, answered directly
    chk_mail = extract_check_email(user_input)
    if chk_mail:
        bus.set_state(bus.EXECUTING, "CHECKING INBOX")
        bus.activity("Checking inbox", "pending")
        reply = handle_check_email(chk_mail)
        speak(reply)
        return

    # Email sending, answered directly. The verbs (send / email / mail) do not
    # overlap with any other route, so this sits safely ahead of the LLM.
    mail = extract_send_email(user_input)
    if mail:
        bus.set_state(bus.EXECUTING, "SEND EMAIL")
        bus.activity(f"Sending email to {mail.get('to', '?')}", "pending")
        reply = handle_send_email(mail)
        bus.activity("Email handled", "ok")
        speak(reply)
        return

    # Scheduled reminders, answered directly. The verbs (remind / notify /
    # alarm) do not overlap with any other route, so this sits safely ahead
    # of the LLM. run_reminder arms the wall-clock daemon and publishes the
    # HUD overlay through bus.reminder().
    rem = extract_reminder(user_input)
    if rem:
        reply = run_reminder(rem)
        bus.activity("Reminder handled", "ok")
        speak(reply)
        return

    # Workspace Protocols (Work / Gaming / Lockdown / Sleep), answered directly
    proto = extract_protocol(user_input)
    if proto:
        reply = handle_protocol(proto)
        speak(reply)
        return

    # Executive Morning / Evening Briefing ("Good morning, Ron"), answered directly
    bf = extract_briefing(user_input)
    if bf:
        reply = handle_briefing(bf)
        speak(reply)
        return

    # Autonomous Multi-Source Deep Researcher ("Research <topic>"), answered directly
    rs = extract_research(user_input)
    if rs:
        reply = handle_research(rs)
        speak(reply)
        return

    # Autonomous Web Agent & Autopilot, answered directly
    ap = extract_autopilot(user_input)
    if ap:
        reply = handle_autopilot(ap)
        speak(reply)
        return

    # Neural Long-Term Memory, answered directly
    mem = extract_memory(user_input)
    if mem:
        reply = handle_memory(mem)
        speak(reply)
        return

    # Daily Standup & Executive Coach, answered directly
    c_cmd = extract_coach(user_input)
    if c_cmd:
        reply = handle_coach(c_cmd)
        speak(reply)
        return

    # Live Intel Briefing Radio ("RON World Report"), answered directly
    intel_cmd = extract_intel(user_input)
    if intel_cmd:
        reply = handle_intel_briefing(intel_cmd)
        speak(reply)
        return

    # The RON World Tribune (Personal AI Newspaper Editor), answered directly
    tribune_cmd = extract_tribune(user_input)
    if tribune_cmd:
        reply = handle_tribune(tribune_cmd)
        speak(reply)
        return

    # Cyber Watchdog & Network Radar, answered directly
    nr_cmd = extract_netradar(user_input)
    if nr_cmd:
        reply = handle_network_scan(nr_cmd)
        speak(reply)
        return

    # Document Intelligence Engine, answered directly
    doc_cmd = extract_docintel(user_input)
    if doc_cmd:
        reply = handle_docintel(doc_cmd)
        speak(reply)
        return

    # Clean Slate Workspace Auto-Organizer, answered directly
    cs_cmd = extract_clean_slate(user_input)
    if cs_cmd:
        reply = handle_clean_slate(cs_cmd)
        speak(reply)
        return

    conversation_history.append({"role": "user", "content": user_input})
    _trim_context()   # bound the request we are about to send

    try:
        current_reminder = TOOL_OUTPUT_REMINDER

        # Proactively inject relevant long-term personal facts and preferences
        proactive_context = memory.get_proactive_context(user_input)
        if proactive_context:
            current_reminder += f"\n\n{proactive_context}"

        if bus.get_language() == "bn":
            current_reminder += (
                "\n\n[CRITICAL LANGUAGE & PERSONA INSTRUCTION]\n"
                "- Bangla mode is currently ACTIVE.\n"
                "- You MUST communicate and answer in fluent, natural, polite Bengali (বাংলা).\n"
                "- Address the user respectfully as 'স্যার' (Sir).\n"
                "- When greeting the user or responding to a greeting/start of conversation, ALWAYS use 'আসসালামু আলাইকুম' (Assalamu Alaikum).\n"
                "- For tool actions, continue to follow the output contract: return ONLY the single JSON object with the tool key."
            )

        bus.set_state(bus.THINKING, f"QUERYING {MODEL.upper()}")
        bus.activity("Reasoning over request", "pending")
        response = client.chat.completions.create(
            model=MODEL,
            messages=[*conversation_history,
                      {"role": "system", "content": current_reminder}],
            temperature=0.7,
            timeout=60
        )
        reply = response.choices[0].message.content.strip()
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

    # Try to parse a tool call.  Do not require the entire reply to be JSON:
    # several compatible models emit a brief preface despite the output rule.
    try:
        data = extract_tool_call(reply)
        if data is None:
            speak(reply)
            return

        tool = data["tool"]
        bus.set_state(bus.EXECUTING, (tool or "UNKNOWN TOOL").upper())
        bus.activity(f"Tool call: {tool}", "pending")

        if tool == "play_youtube":
            result = play_youtube(data["search_query"])
        elif tool == "generate_pdf":
            result = handle_generate_pdf(data)
        elif tool == "generate_webpage":
            result = handle_generate_webpage(data)
        elif tool == "generate_image":
            result = handle_generate_image(data)
        elif tool == "open_app":
            result = open_app(data["app_name"])
        elif tool == "open_website":
            result = open_website(data["url"])
        elif tool == "get_weather":
            result = handle_get_weather(data)
            bus.weather(**weather.hud_payload(
                str(data.get("location") or "").strip() or None))
        elif tool in ("get_weather_station", "weather_station"):
            result = handle_weather_station(data)
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
        elif tool == "browser_action":
            result = handle_browser_action(data)
        elif tool == "send_email":
            result = handle_send_email(data)
        elif tool == "check_email":
            result = handle_check_email(data)
        elif tool == "reply_email":
            result = handle_reply_email(data)
        elif tool == "set_reminder":
            result = handle_set_reminder(data)
        elif tool == "run_protocol":
            result = handle_protocol(data)
        elif tool == "get_briefing":
            result = handle_briefing(data)
        elif tool == "deep_research":
            result = handle_research(data)
        elif tool == "web_autopilot":
            result = handle_autopilot(data)
        elif tool == "manage_memory":
            result = handle_memory(data)
        elif tool == "coach_standup":
            result = handle_coach(data)
        elif tool == "intel_briefing":
            result = handle_intel_briefing(data)
        elif tool in ("ron_tribune", "tribune"):
            result = handle_tribune(data)
        elif tool in ("doc_intel", "docintel", "document_intelligence"):
            result = handle_docintel(data)
        elif tool in ("clean_slate", "clean_downloads", "organize_downloads"):
            result = handle_clean_slate(data)
        else:
            result = "Unknown tool requested."

        bus.activity(f"Tool executed: {tool}", "ok" if tool else "fail")
        speak(result)

    except KeyError as e:
        # The object was a tool call but it omitted a required argument.
        # Keep this separate from conversational replies so the malformed model
        # response is visible in the activity feed instead of silently ignored.
        bus.activity(f"Malformed tool call: missing {e}", "fail")
        speak("I received an incomplete action request, Sir. Please try again.")
    except Exception as e:
        # A tool failure must never take the assistant down with it.
        import traceback
        traceback.print_exc()
        bus.activity(f"Tool failed: {e}", "fail")
        bus.set_state(bus.ERROR, str(e)[:160])
        speak(f"That command failed, Sir. {e}")

WAKE_PATTERNS = [
    r"^(?:hey|hi|hello|ok|okay)\s+(?:ron|run|wrong|round|raun)\b",
    r"^wake\s+up(?:\s+ron)?\b",
    r"^are\s+you\s+(?:there|online)(?:\s+ron)?\b",
    r"^ron$",
    r"^(?:হে|এই\s*যে|হাই|হ্যালো)\s*রন\b",
    r"^(?:জেগে\s*ওঠো|জেগে\s*উঠ|উঠো)\b",
    r"^রন$",
]


def check_wake_word(text: str) -> tuple[bool, str]:
    """Check if text contains or begins with a wake word.
    Returns (is_wake: bool, trailing_command: str).
    """
    if not text:
        return False, ""
    clean = re.sub(r"[,\.!?]", "", text).strip().lower()
    for pattern in WAKE_PATTERNS:
        m = re.search(pattern, clean)
        if m:
            extracted = clean[m.end():].strip()
            return True, extracted
    for trigger in ["hey ron", "hello ron", "wake up ron", "wake up", "হে রন", "এই যে রন", "জেগে ওঠো"]:
        if trigger in clean:
            parts = clean.split(trigger, 1)
            extracted = parts[1].strip() if len(parts) > 1 else ""
            return True, extracted
    return False, ""


def is_sleep_command(text: str) -> bool:
    """Check if text is an explicit request to enter sleep / standby mode."""
    if not text:
        return False
    clean = re.sub(r"[,\.!?]", "", text).strip().lower()

    # Reject informational/query questions about sleep
    if any(clean.startswith(w) for w in ["why", "how", "what", "can humans", "tell me about"]):
        return False
    if "tracking" in clean or "tracker" in clean:
        return False

    patterns = [
        r"^(?:ron\s+)?(?:go\s+to\s+)?sleep(?:\s+now|\s+ron)?$",
        r"^(?:please\s+)?(?:go\s+to\s+)?sleep(?:\s+now|\s+ron)?$",
        r"^(?:time\s+to\s+sleep|take\s+a\s+nap)$",
        r"^(?:enter\s+)?sleep\s+mode$",
        r"^standby(?:\s+mode)?$",
        r"^(?:ron\s+)?(?:স্লিপ|স্লিপ\s*মোড|ঘুমাও|ঘুমাতে\s*যাও)$",
    ]
    for p in patterns:
        if re.search(p, clean):
            return True
    return False


def enter_sleep_mode(shutdown_event, voice_enabled) -> tuple[bool, str]:
    """Standby sleep loop.

    Ron remains quiet and low-overhead until 'Hey Ron' or wake words are spoken,
    or until shutdown is requested.
    Returns:
        (woken_up: bool, trailing_command: str)
    """
    bus.set_state(bus.SLEEP, "STANDBY · SAY 'HEY RON'")
    bus.activity("System entered sleep mode", "info")
    print("\n[Ron is asleep. Say 'Hey Ron' to wake up.]")

    while not shutdown_event.is_set():
        try:
            if not voice_enabled.is_set():
                bus.set_state(bus.SLEEP, "MICROPHONE MUTED")
                voice_enabled.wait(0.4)
                continue

            bus.set_state(bus.SLEEP, "STANDBY · SAY 'HEY RON'")
            text = listen(timeout=6, phrase_limit=10)
            if shutdown_event.is_set():
                break
            if not text:
                continue

            # If user explicitly requests complete shutdown while asleep
            if any(w in text.lower() for w in ["goodbye", "shut down", "exit"]):
                bus.transcript("user", text)
                speak("Going offline, Sir. Goodbye.")
                shutdown_event.set()
                return False, ""

            is_wake, trailing_cmd = check_wake_word(text)
            if is_wake:
                bus.activity(f"Wake word detected: '{text}'", "ok")
                bus.transcript("user", text)
                bus.set_state(bus.IDLE, "ACTIVE")
                if bus.get_language() == "bn":
                    speak("আমি প্রস্তুত স্যার, বলুন কী করতে পারি?")
                else:
                    speak("I'm back online, Sir. How can I help you?")
                return True, trailing_cmd
            else:
                print(f"[Sleep mode: Ignored non-wake speech '{text}']")
        except KeyboardInterrupt:
            shutdown_event.set()
            return False, ""
        except Exception as e:
            print(f"[Sleep loop exception: {e}]")
            time.sleep(0.5)

    return False, ""


def run_voice_loop(greet=True):
    """The always-on listening loop.

    Split out of main() so the HUD server can run it on a background thread while
    it serves the interface. Honours `voice_enabled` (mic muted from the HUD) and
    `shutdown_event` (quit requested from either front end).
    """
    start_focus_monitor()
    try:
        telegram_bridge.start_telegram_daemon()
    except Exception as e:
        print(f"[telegram_bridge error: {e}]")
    try:
        docintel.start_rag_watcher()
    except Exception as e:
        print(f"[docintel watcher error: {e}]")
    if greet:
        if bus.get_language() == "bn":
            speak("আসসালামু আলাইকুম স্যার। রন প্রস্তুত আছে। আমি আপনাকে কীভাবে সাহায্য করতে পারি?")
        else:
            speak("Ron is online. How can I help you, Sir?")
    while not shutdown_event.is_set():
        try:
            if not voice_enabled.is_set():
                # Muted: hold the mic closed so another app can use it, and check
                # back often enough that un-muting feels immediate.
                bus.set_state(bus.IDLE, "MICROPHONE MUTED")
                voice_enabled.wait(0.4)
                continue

            command = listen(timeout=8, phrase_limit=15)
            if shutdown_event.is_set():
                break
            if not command:
                continue

            # Check for shutdown commands
            if any(word in command for word in ["goodbye", "shut down", "exit"]):
                bus.transcript("user", command)
                speak("Going offline, Sir. Goodbye.")
                shutdown_event.set()
                break

            # Check for sleep command
            if is_sleep_command(command):
                bus.transcript("user", command)
                if bus.get_language() == "bn":
                    speak("স্লিপ মোডে যাচ্ছি স্যার। আমাকে ডাকতে 'হে রন' বলুন।")
                else:
                    speak("Going to sleep, Sir. Say 'Hey Ron' when you need me.")
                woken, trailing = enter_sleep_mode(shutdown_event, voice_enabled)
                if woken and trailing:
                    process_command(trailing)
                continue

            # Check if user said wake word while already active
            is_wake, trailing = check_wake_word(command)
            if is_wake:
                bus.transcript("user", command)
                if trailing:
                    process_command(trailing)
                else:
                    if bus.get_language() == "bn":
                        speak("আমি শুনছি স্যার, বলুন কী করতে পারি?")
                    else:
                        speak("Yes, Sir? How can I help you?")
                continue

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
