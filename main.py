import json
import os
import re
import sys
from openai import OpenAI
from voice import speak, listen
from tools import (play_youtube, generate_pdf, open_app, open_website,
                   open_folder, resolve_user_folder, _FOLDER_ALIASES)

# Fix encoding for Windows console
if sys.platform == 'win32':
    import io
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

MODEL = "kat-coder-pro-v2.5"
# MODEL = "DeepSeek-V4-Flash"    # fallback; confirmed 200 on this endpoint
# MODEL = "Qwen3.5-397B-A17B"    # fallback; verify with test_api.py

SYSTEM_PROMPT = """You are Ron, an advanced personal AI assistant created by Ifteqhar.
You are intelligent, concise, and helpful — like JARVIS from Iron Man.

You can either respond conversationally OR trigger one of these tools by responding ONLY with valid JSON:

Tools:
1. Play YouTube:    {"tool": "play_youtube", "search_query": "..."}
2. Generate PDF:    {"tool": "generate_pdf", "file_name": "short_file_name", "topic": "What the document should cover"}
3. Open App:        {"tool": "open_app", "app_name": "..."}
4. Open Website:    {"tool": "open_website", "url": "..."}

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

conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]

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

def write_document(topic: str) -> str:
    """Second LLM call: a dedicated writer turn that produces the full document.

    The assistant persona is told to stay terse, so asking it to inline a whole
    report into a JSON field yields a stub. This call has its own prompt and no
    conversation history, so length is governed only by the writer instructions.
    """
    print(f"[Writing document on '{topic}' -- 1200-2000 words, this usually "
          f"takes 30-90 seconds...]")
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
    return generate_pdf(file_name, content, title=title)


def process_command(user_input: str):
    # Handle folder opening directly
    path = extract_folder(user_input)
    if path:
        speak(open_folder(path))
        return

    # Handle website visits directly without going through AI
    site = extract_website(user_input)
    if site:
        result = open_website(site)
        speak(result)
        return

    conversation_history.append({"role": "user", "content": user_input})

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=conversation_history,
            temperature=0.7,
            timeout=60
        )
        reply = response.choices[0].message.content.strip()
        print(f"[DEBUG API Response] Model: {response.model}")
    except Exception as e:
        error_msg = str(e)
        # OpenAI-style errors carry the useful text in .body['message']
        if hasattr(e, 'body') and isinstance(e.body, dict):
            error_msg = e.body.get('message', error_msg)
        status = getattr(e, "status_code", None)
        low = error_msg.lower()
        print(f"[API Error: {error_msg}]")

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

        if tool == "play_youtube":
            result = play_youtube(data["search_query"])
        elif tool == "generate_pdf":
            result = handle_generate_pdf(data)
        elif tool == "open_app":
            result = open_app(data["app_name"])
        elif tool == "open_website":
            result = open_website(data["url"])
        else:
            result = "Unknown tool requested."

        speak(result)

    except (json.JSONDecodeError, KeyError):
        speak(reply)
    except Exception as e:
        # A tool failure must never take the assistant down with it.
        import traceback
        traceback.print_exc()
        speak(f"That command failed, Sir. {e}")

def main():
    speak("Ron is online. How can I help you, Sir?")
    silent_rounds = 0
    while True:
        try:
            command = ""
            for _ in range(3):
                command = listen(timeout=8, phrase_limit=15)
                if command:
                    break
            if not command:
                # Looping here in silence gives no hint that the microphone is the
                # problem, which just reads as "Ron is broken". Say so periodically.
                silent_rounds += 1
                if silent_rounds >= 3:
                    print('[Nothing recognised in 9 attempts. Check MIC_INDEX in '
                          'voice.py -- run "python test_mic.py" to list devices. '
                          'To skip the mic entirely: python main.py "your command"]')
                    speak("I cannot hear anything, Sir. Please check the microphone.")
                    silent_rounds = 0
                continue
            silent_rounds = 0
            if any(word in command for word in ["goodbye", "shut down", "exit", "sleep"]):
                speak("Going offline, Sir. Goodbye.")
                break
            process_command(command)
        except KeyboardInterrupt:
            # Ctrl+C normally lands inside the blocking mic read. A second one can
            # land inside the blocking SAPI call below -- and an exception raised in
            # an except block is not caught by its sibling except clauses, so
            # without this guard it escapes main() and crashes on the way out.
            try:
                speak("Shutting down. Goodbye, Sir.")
            except KeyboardInterrupt:
                print("\n[Interrupted]")
            break
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[Loop error: {e}]")
            speak("Something went wrong, Sir. Ready for your next command.")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Text mode, for testing without a microphone:
        #   python main.py "create a pdf about black holes"
        # listen() lowercases what it hears, so match that here.
        process_command(" ".join(sys.argv[1:]).strip().lower())
    else:
        try:
            main()
        except KeyboardInterrupt:
            print("\n[Interrupted - exiting.]")
