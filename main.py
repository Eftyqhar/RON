import json
import os
import sys
from openai import OpenAI
from voice import speak, listen
from tools import play_youtube, generate_pdf, open_app, open_website

# Fix encoding for Windows console
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Use OpenAI-compatible API from hcnsec.cn
client = OpenAI(
    api_key="YOUR_API_KEY",
    base_url="https://api.hcnsec.cn/v1"
)

# Model - using DeepSeek-V4-Flash which works with this API
MODEL = "Qwen3.5-397B-A17B"

SYSTEM_PROMPT = """You are Ron, an advanced personal AI assistant created by Ifteqhar.
You are intelligent, concise, and helpful — like JARVIS from Iron Man.

You can either respond conversationally OR trigger one of these tools by responding ONLY with valid JSON:

Tools:
1. Play YouTube:    {"tool": "play_youtube", "search_query": "..."}
2. Generate PDF:    {"tool": "generate_pdf", "file_name": "...", "content": "..."}
3. Open App:        {"tool": "open_app", "app_name": "..."}
4. Open Website:    {"tool": "open_website", "url": "..."}

Rules:
- play music/video → play_youtube (JSON only)
- create/save document → generate_pdf (JSON only)
- open software/application → open_app (JSON only)
- visit/open/go to any website → open_website (JSON only), examples:
    "visit facebook" → {"tool": "open_website", "url": "https://www.facebook.com"}
    "open google" → {"tool": "open_website", "url": "https://www.google.com"}
    "go to youtube" → {"tool": "open_website", "url": "https://www.youtube.com"}
- NEVER ask questions for tool actions, just execute immediately
- Keep responses short and sharp. Never be verbose.
- Always refer to yourself as Ron.
- Always refer to the user as Sir or by name Ifteqhar."""

conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]

WEBSITE_KEYWORDS = ["visit", "go to", "browse", "navigate to", "take me to"]

FOLDER_KEYWORDS = ["open folder", "open directory", "open drive", "show folder", "show me"]

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
            if site_name in command.lower():
                return site_url

    return None

def extract_folder(command: str):
    import re
    # Match drive paths like "D drive", "D:", "C drive"
    drive = re.search(r'\b([a-zA-Z])\s*drive\b', command)
    folder = None
    for kw in FOLDER_KEYWORDS:
        if kw in command:
            folder = command.replace(kw, "").strip()
            break
    if drive:
        drive_letter = drive.group(1).upper()
        # Extract folder name if mentioned
        folder_match = re.search(r'(\w+)\s+folder', command)
        if folder_match:
            path = f"{drive_letter}:\\{folder_match.group(1)}"
        else:
            path = f"{drive_letter}:\\"
        return path
    return None

def process_command(user_input: str):
    # Handle folder opening directly
    path = extract_folder(user_input)
    if path:
        import subprocess
        subprocess.Popen(f'explorer "{path}"', shell=True)
        speak(f"Opening {path}, Sir.")
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
            temperature=0.7
        )
        reply = response.choices[0].message.content.strip()
        print(f"[DEBUG API Response] Model: {response.model}")
    except Exception as e:
        import traceback
        error_msg = str(e)
        # Handle OpenAI API errors which return error objects
        if hasattr(e, 'body') and isinstance(e.body, dict):
            error_msg = e.body.get('message', error_msg)
        full_trace = traceback.format_exc()
        print(f"[API Error: {error_msg}]")
        # Check for quota-related errors in Chinese or English
        if "quota" in error_msg.lower() or "balance" in error_msg.lower() or "token" in error_msg.lower() or "每月" in error_msg:
            speak("我的月度token配额已不足。请为我的API密钥添加更多token。")
        else:
            speak(f"连接AI大脑时出现问题: {error_msg}")
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
            result = generate_pdf(data["file_name"], data["content"])
        elif tool == "open_app":
            result = open_app(data["app_name"])
        elif tool == "open_website":
            result = open_website(data["url"])
        else:
            result = "Unknown tool requested."

        speak(result)

    except (json.JSONDecodeError, KeyError):
        speak(reply)

def main():
    speak("Ron is online. How can I help you, Sir?")
    while True:
        try:
            command = ""
            for _ in range(3):
                command = listen(timeout=8, phrase_limit=15)
                if command:
                    break
            if not command:
                continue
            if any(word in command for word in ["goodbye", "shut down", "exit", "sleep"]):
                speak("Going offline, Sir. Goodbye.")
                break
            process_command(command)
        except KeyboardInterrupt:
            speak("Shutting down. Goodbye, Sir.")
            break

if __name__ == "__main__":
    main()
