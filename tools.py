import subprocess
import os
import webbrowser
import pywhatkit
from fpdf import FPDF

APPS = {
    "notepad": ("exe", "notepad.exe"),
    "calculator": ("exe", "calc.exe"),
    "chrome": ("exe", "chrome.exe"),
    "firefox": ("exe", "firefox.exe"),
    "brave": ("exe", "C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe"),
    "vs code": ("exe", "code"),
    "vscode": ("exe", "code"),
    "explorer": ("exe", "explorer.exe"),
    "task manager": ("exe", "taskmgr.exe"),
    "word": ("exe", "winword.exe"),
    "excel": ("exe", "excel.exe"),
    "paint": ("exe", "mspaint.exe"),
    "cmd": ("exe", "cmd.exe"),
    "terminal": ("exe", "wt.exe"),
    "whatsapp": ("store", "shell:AppsFolder\\5319275A.WhatsApp_cv1g1gvanyjgm!WhatsApp"),
    "telegram": ("store", "shell:AppsFolder\\TelegramMessengerLLP.TelegramDesktop_t4vj0kkmcyv6y!Telegram"),
    "spotify": ("store", "shell:AppsFolder\\SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"),
    "netflix": ("store", "shell:AppsFolder\\4DF9E0F8.Netflix_mcm4njqhnhss8!Netflix"),
}

def play_youtube(search_query: str):
    try:
        pywhatkit.playonyt(search_query)
        return f"Playing {search_query} on YouTube, Sir."
    except Exception as e:
        return f"Could not play video: {e}"

def open_website(url: str):
    try:
        webbrowser.open(url)
        return f"Opening {url}, Sir."
    except Exception as e:
        return f"Could not open website: {e}"

def generate_pdf(file_name: str, content: str):
    if not file_name.endswith(".pdf"):
        file_name += ".pdf"
    save_path = os.path.join(os.path.expanduser("~"), "Documents", file_name)
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    for line in content.split("\n"):
        pdf.multi_cell(0, 10, line)
    pdf.output(save_path)
    return f"PDF saved to {save_path}"

def open_app(app_name: str):
    key = app_name.lower().strip()
    app = APPS.get(key)
    if app:
        kind, path = app
        try:
            if kind == "store":
                subprocess.Popen(f'explorer "{path}"', shell=True)
            else:
                subprocess.Popen(path, shell=True)
            return f"Opening {app_name}, Sir."
        except Exception as e:
            return f"Failed to open {app_name}: {e}"
    else:
        try:
            subprocess.Popen(app_name, shell=True)
            return f"Trying to open {app_name}, Sir."
        except Exception as e:
            return f"Could not open {app_name}: {e}"
