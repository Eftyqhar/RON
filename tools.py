import subprocess
import os
import re
import shutil
import inspect
import tempfile
import webbrowser

import fpdf as _fpdf
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
        # Imported lazily: pywhatkit is slow to load and pulls in network deps,
        # and its absence must not break the other tools.
        import pywhatkit
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

# ----------------------------------------------------------------------------
# PDF generation
#
# Two different libraries answer to `import fpdf`:
#   * legacy pyfpdf 1.7.2 -- unmaintained, no markdown kwarg, and its PDF
#     metadata path is NOT unicode-safe (see _build_pdf).
#   * fpdf2 2.7+          -- maintained, has multi_cell(markdown=True).
# Both are supported here, because which one is installed is not up to us.
# ----------------------------------------------------------------------------

_FPDF_VERSION = str(getattr(_fpdf, "FPDF_VERSION", "0"))
_IS_LEGACY_FPDF = _FPDF_VERSION.split(".")[0] == "1"

# Probe the API shape rather than trusting a version string.
_ADD_FONT_TAKES_UNI = "uni" in inspect.signature(FPDF.add_font).parameters
_MULTI_CELL_MARKDOWN = "markdown" in inspect.signature(FPDF.multi_cell).parameters

# Legacy fpdf caches parsed TrueType metrics next to the .ttf by default, i.e.
# it tries to write into C:\Windows\Fonts. That write fails (permission) and is
# swallowed, so every single PDF re-parses ~1 MB of Arial three times over.
# Point the cache somewhere writable instead.
if hasattr(_fpdf, "set_global"):
    try:
        _cache_dir = os.path.join(tempfile.gettempdir(), "ron_fpdf_fontcache")
        os.makedirs(_cache_dir, exist_ok=True)
        _fpdf.set_global("FPDF_CACHE_MODE", 2)
        _fpdf.set_global("FPDF_CACHE_DIR", _cache_dir)
    except Exception as e:
        print(f"[Font cache setup skipped: {e}]")

_FONT_DIR = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")

# (family, regular, bold, italic, bold-italic) -- tried in order, first one that
# fully loads wins. A real TrueType font is what gives us Unicode; the built-in
# core fonts are latin-1 only and cannot render curly quotes, arrows, etc.
_LATIN_FONTS = [
    ("RonSans", "arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"),
    ("RonSans", "calibri.ttf", "calibrib.ttf", "calibrii.ttf", "calibriz.ttf"),
    ("RonSans", "segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "segoeuiz.ttf"),
    ("RonSans", "verdana.ttf", "verdanab.ttf", "verdanai.ttf", "verdanaz.ttf"),
    ("RonSans", "tahoma.ttf", "tahomabd.ttf", "tahoma.ttf", "tahomabd.ttf"),
]

# Arial has no CJK coverage, so prefer a CJK-capable face when the text needs
# one. Caveat: the stock Windows CJK fonts are TrueType *collections* (.ttc),
# which legacy fpdf cannot open at all -- so CJK documents fall back to Arial
# and the ideographs come out blank. _load_fonts warns when that happens.
_CJK_FONTS = [
    ("RonSans", "malgun.ttf", "malgunbd.ttf", "malgun.ttf", "malgunbd.ttf"),
    ("RonSans", "msyh.ttc", "msyhbd.ttc", "msyh.ttc", "msyhbd.ttc"),
    ("RonSans", "simsun.ttc", "simsun.ttc", "simsun.ttc", "simsun.ttc"),
    ("RonSans", "msgothic.ttc", "msgothic.ttc", "msgothic.ttc", "msgothic.ttc"),
]

_CJK_RE = re.compile(r"[\u3000-\u9fff\uac00-\ud7af\uff00-\uffef]")

# Lossy ASCII fallbacks, used when no TrueType font could be loaded and for PDF
# metadata on legacy fpdf. No replacement may produce "--", which fpdf2's
# markdown parser reads as an underline delimiter.
_UNICODE_MAP = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2032": "'", "\u2033": '"',
    "\u2013": "-", "\u2014": "-", "\u2015": "-", "\u2212": "-",
    "\u2026": "...", "\u00a0": " ", "\u2022": "-", "\u00b7": "-", "\u25cf": "-",
    "\u2192": "->", "\u2190": "<-", "\u21d2": "=>", "\u2194": "<->",
    "\u00d7": "x", "\u00f7": "/", "\u2248": "~", "\u2260": "!=",
    "\u2264": "<=", "\u2265": ">=", "\u00b0": " deg",
    "\u20ac": "EUR", "\u00a3": "GBP", "\u00a5": "JPY",
    "\u2122": "(TM)", "\u00ae": "(R)", "\u00a9": "(C)",
    "\u2605": "*", "\u2606": "*", "\u2713": "v", "\u2717": "x",
}

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)


class RonPDF(FPDF):
    """FPDF with a page-number footer."""

    def footer(self):
        # Save the caller's font so the footer leaks no state into the body.
        prev = (self.font_family, self.font_style, self.font_size_pt)
        self.set_y(-15)
        self.set_x(0)
        try:
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(130, 130, 130)
            # Spanning the full page width from x=0 centres the label no matter
            # what the left margin is -- it may be temporarily indented for a
            # list item when an automatic page break lands here.
            self.cell(self.w, 10, f"Page {self.page_no()}", align="C")
        except Exception:
            pass
        self.set_text_color(0, 0, 0)
        if prev[0]:
            try:
                self.set_font(prev[0], prev[1], prev[2])
            except Exception:
                pass


def _add_font(pdf, family, style, path):
    if _ADD_FONT_TAKES_UNI:
        pdf.add_font(family, style, path, uni=True)
    else:
        pdf.add_font(family, style, path)


def _load_fonts(pdf, content):
    """Register a Unicode TrueType font. Returns (family, unicode_ok).

    Also records which styles actually registered on the instance, so _font()
    can degrade "BI" to "B" instead of raising "Undefined font".
    """
    needs_cjk = bool(_CJK_RE.search(content))
    candidates = (_CJK_FONTS + _LATIN_FONTS) if needs_cjk else list(_LATIN_FONTS)

    for i, (family, regular, bold, italic, bold_italic) in enumerate(candidates):
        # Each candidate gets its own family name. add_font() is a no-op when the
        # family+style key already exists, so if a face registers regular+bold and
        # then fails on italic, reusing the name would keep those two orphans and
        # silently ignore the next candidate's -- yielding mixed typefaces.
        family = f"{family}{i}"
        required = [os.path.join(_FONT_DIR, f) for f in (regular, bold, italic)]
        if not all(os.path.isfile(p) for p in required):
            continue
        try:
            for style, path in zip(("", "B", "I"), required):
                _add_font(pdf, family, style, path)
        except Exception:
            continue  # unreadable face (e.g. a .ttc on legacy fpdf) -- try next
        styles = {"", "B", "I"}
        # Bold-italic is optional; only nested **_x_** markdown needs it.
        try:
            _add_font(pdf, family, "BI", os.path.join(_FONT_DIR, bold_italic))
            styles.add("BI")
        except Exception:
            pass
        pdf._ron_styles = styles
        if needs_cjk and regular not in {f[1] for f in _CJK_FONTS}:
            print("[PDF] Warning: text contains CJK characters but no CJK font "
                  "could be loaded; those glyphs will be blank.")
        return family, True

    # Core font: latin-1 only, but all four styles exist.
    pdf._ron_styles = {"", "B", "I", "BI"}
    if needs_cjk:
        print("[PDF] Warning: CJK text with no usable TrueType font; "
              "those characters will be replaced.")
    return "Helvetica", False


def _font(pdf, family, style, size):
    """set_font, degrading to a style that was actually registered."""
    available = getattr(pdf, "_ron_styles", None)
    if available is not None:
        # Normalise to canonical "BI" order and drop duplicates, so "IB" and
        # "BB" both resolve to a key that was actually registered.
        wanted = "".join(ch for ch in "BI" if ch in style.upper())
        while wanted and wanted not in available:
            wanted = wanted[:-1]
        style = wanted
    pdf.set_font(family, style, size)


def _sanitize_latin1(text):
    """Map common Unicode to ASCII, then drop anything still unencodable."""
    for ch, rep in _UNICODE_MAP.items():
        text = text.replace(ch, rep)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _clean_inline(text):
    """Strip Markdown that cannot be rendered inline."""
    text = re.sub(r"`([^`]*)`", r"\1", text)                # `code` -> code
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links -> label
    return text


def _strip_emphasis(text):
    return text.replace("**", "").replace("__", "")


def _bold_runs(text):
    """Split text into (chunk, is_bold) runs on **bold** / __bold__."""
    runs, pos = [], 0
    for m in _BOLD_RE.finditer(text):
        if m.start() > pos:
            runs.append((text[pos:m.start()], False))
        runs.append((m.group(1) or m.group(2) or "", True))
        pos = m.end()
    if pos < len(text):
        runs.append((text[pos:], False))
    return runs


def _write(pdf, family, size, text, style="", height=6.5, indent=0.0, md=True):
    """Write a wrapped block of text, optionally indented.

    Indentation shifts the left margin rather than just x, so the continuation
    lines of a wrapped bullet stay aligned instead of snapping back to the page
    margin. x is set unconditionally because fpdf2's multi_cell leaves x at the
    right-hand edge of the previous block (legacy fpdf resets it to l_margin),
    and a w=0 cell would then resolve to near-zero width.
    """
    if not text:
        text = " "
    base = pdf.l_margin
    if indent:
        pdf.set_left_margin(base + indent)
    pdf.set_x(base + indent)
    try:
        if md and _MULTI_CELL_MARKDOWN:
            _font(pdf, family, style, size)
            pdf.multi_cell(0, height, text, align="L", markdown=True)
        elif md and _BOLD_RE.search(text):
            # Legacy fpdf has no markdown kwarg, so emulate inline bold by
            # flowing runs with write() and switching fonts between them.
            for chunk, bold in _bold_runs(text):
                if not chunk:
                    continue
                _font(pdf, family, style + "B" if bold else style, size)
                pdf.write(height, chunk)
            _font(pdf, family, style, size)
            pdf.ln(height)
        else:
            _font(pdf, family, style, size)
            pdf.multi_cell(0, height, _strip_emphasis(text), align="L")
    finally:
        if indent:
            pdf.set_left_margin(base)
        pdf.set_x(base)


def _rule(pdf):
    y = pdf.get_y() + 1
    pdf.set_draw_color(190, 190, 190)
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(4)


def _render_markdown(pdf, family, content, bullet="-", md=True):
    """Render Markdown text into the PDF with real headings and lists."""
    in_fence = False

    for raw in content.split("\n"):
        line = raw.rstrip()
        stripped = line.strip()

        # Code fences: the writer prompt forbids them, but be tolerant.
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            _write(pdf, family, 9.5, line, indent=5, md=False)
            continue

        if not stripped:
            pdf.ln(3)
            continue

        # Horizontal rule (---, ***, ___)
        if re.fullmatch(r"([-*_])\1{2,}", stripped):
            _rule(pdf)
            continue

        # Markdown table separator (|---|---|) -- drop it, keep the rows
        if "|" in stripped and re.fullmatch(r"\|?[\s:|-]+\|[\s:|-]*", stripped):
            continue

        # Table row -> readable text
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            _write(pdf, family, 10, _clean_inline("   ".join(cells)),
                   indent=3, md=md)
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)", stripped)
        if heading:
            level = len(heading.group(1))
            # Headings are already bold, so drop inline emphasis markers.
            text = _strip_emphasis(_clean_inline(heading.group(2))).strip()
            if level == 1:
                pdf.ln(2)
                _font(pdf, family, "B", 22)
                pdf.set_x(pdf.l_margin)
                pdf.multi_cell(0, 11, text or " ", align="C")
                _rule(pdf)
                pdf.ln(2)
            elif level == 2:
                pdf.ln(4)
                _write(pdf, family, 15, text, style="B", height=8.5, md=False)
                pdf.ln(1.5)
            elif level == 3:
                pdf.ln(2.5)
                _write(pdf, family, 12.5, text, style="B", height=7, md=False)
                pdf.ln(1)
            else:
                pdf.ln(2)
                _write(pdf, family, 11, text, style="B", height=6.5, md=False)
            continue

        # Bulleted list (supports one level of nesting)
        blist = re.match(r"^(\s*)[-*\u2022]\s+(.*)", raw)
        if blist:
            depth = 1 if len(blist.group(1)) >= 2 else 0
            glyph = bullet if depth == 0 else ("-" if bullet != "-" else "o")
            _write(pdf, family, 11,
                   f"{glyph}  {_clean_inline(blist.group(2))}",
                   indent=5 + depth * 6, md=md)
            continue

        # Numbered list
        nlist = re.match(r"^(\s*)(\d+)[.)]\s+(.*)", raw)
        if nlist:
            _write(pdf, family, 11,
                   f"{nlist.group(2)}.  {_clean_inline(nlist.group(3))}",
                   indent=5, md=md)
            continue

        # Blockquote
        if stripped.startswith(">"):
            _write(pdf, family, 10.5, _clean_inline(stripped.lstrip("> ")),
                   style="I", indent=6, md=md)
            continue

        _write(pdf, family, 11, _clean_inline(line), md=md)


def _is_unusable(content):
    """True when the model gave us a placeholder instead of real content."""
    if not content:
        return True
    # Strip whitespace and filler punctuation; a placeholder leaves nothing.
    core = re.sub(r"[\s.\u2026\-_*#|]", "", content)
    if not core:
        return True
    return content.strip().lower() in {
        "content", "n/a", "na", "none", "null", "todo", "text", "your content here",
    }


def _safe_name(file_name, fallback="document"):
    """LLM-supplied names are untrusted: basename only, no illegal chars."""
    name = os.path.basename(str(file_name or "")).strip()
    if name.lower().endswith(".pdf"):
        name = name[:-4]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(". _")[:80]
    return name or fallback


def _documents_dir():
    """Resolve the real Documents folder.

    Asks Windows directly (SHGetKnownFolderPath), because OneDrive folder
    redirection means ~/Documents often is not the user's Documents at all.
    """
    try:
        import ctypes

        class GUID(ctypes.Structure):
            _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort),
                        ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]

        # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
        folder_id = GUID(0xFDD39AD0, 0x238F, 0x46AF,
                         (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85,
                                              0x48, 0x03, 0x69, 0xC7))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(folder_id), 0, None, ctypes.byref(out)) == 0:
            path = out.value
            ctypes.windll.ole32.CoTaskMemFree(out)
            if path and os.path.isdir(path):
                return path
    except Exception:
        pass

    # Fallback for non-Windows or unusual setups.
    home = os.path.expanduser("~")
    candidates = []
    onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer")
    if onedrive:
        candidates.append(os.path.join(onedrive, "Documents"))
    candidates += [os.path.join(home, "Documents"),
                   os.path.join(home, "OneDrive", "Documents")]

    for path in candidates:
        if os.path.isdir(path):
            return path

    os.makedirs(candidates[0], exist_ok=True)
    return candidates[0]


def _build_pdf(content, title, name, md):
    """Render the document into a fresh FPDF instance."""
    pdf = RonPDF()
    pdf.set_margins(20, 18, 20)
    pdf.set_auto_page_break(auto=True, margin=20)

    # Metadata must be latin-1 clean on legacy fpdf: _textstring() drops the
    # raw string straight into the output buffer with no UTF-16BE encoding, and
    # output() opens (and truncates) the target file *before* encoding that
    # buffer as latin-1. One curly quote in the title would therefore leave a
    # 0-byte .pdf behind. Wrapping set_title in try/except does not help --
    # the failure happens later, inside output().
    meta = (title or name)
    if _IS_LEGACY_FPDF:
        meta = _sanitize_latin1(meta)
    try:
        pdf.set_title(meta)
        pdf.set_author("Ron")
        pdf.set_creator("Ron AI Assistant")
    except Exception:
        pass  # metadata is cosmetic; never fail the document over it

    pdf.add_page()

    family, unicode_ok = _load_fonts(pdf, content)
    if not unicode_ok:
        content = _sanitize_latin1(content)

    _render_markdown(pdf, family, content,
                     bullet="\u2022" if unicode_ok else "-", md=md)
    return pdf


def generate_pdf(file_name: str, content: str, title: str = None):
    """Render Markdown `content` to a formatted PDF in the user's Documents."""
    try:
        if _is_unusable(content):
            return ("I could not write the document content, so I did not save "
                    "an empty PDF. Please try again, Sir.")

        content = content.strip()

        # If the content has no title of its own, add one so page 1 has a header.
        if title and not content.lstrip().startswith("#"):
            content = f"# {title}\n\n{content}"

        name = _safe_name(file_name, fallback=_safe_name(title or "document"))
        folder = _documents_dir()

        save_path = os.path.join(folder, f"{name}.pdf")
        counter = 2
        while os.path.exists(save_path):
            save_path = os.path.join(folder, f"{name}-{counter}.pdf")
            counter += 1

        try:
            pdf = _build_pdf(content, title, name, md=True)
        except Exception as e:
            # Inline emphasis is the only fragile part of the render; retry
            # without it rather than lose the whole document.
            print(f"[Markdown render failed, retrying plain: {e}]")
            pdf = _build_pdf(content, title, name, md=False)

        pages = pdf.page_no()
        pdf.output(save_path)

        try:
            # RON_NO_OPEN lets the test suite render without spawning viewers.
            if hasattr(os, "startfile") and not os.environ.get("RON_NO_OPEN"):
                os.startfile(save_path)
        except Exception as e:
            print(f"[Could not open PDF: {e}]")

        print(f"[PDF] {save_path} ({pages} page(s), "
              f"{os.path.getsize(save_path)} bytes)")
        return f"Your {pages}-page PDF on {title or name} is saved and open, Sir."

    except Exception as e:
        return f"Could not create the PDF: {e}"

# "open my files", "open document folder" and friends are folder requests, not
# apps. Windows ships no Files.exe, so without this table they fall through to the
# blind shell fallback in open_app(), which prints "'Files' is not recognized" to
# the console while Ron cheerfully reports success. None => Explorer's home view.
_FOLDER_ALIASES = {
    "files": None, "my files": None, "file explorer": None, "explorer": None,
    "windows explorer": None, "this pc": None, "my computer": None,
    "documents": "Documents", "document": "Documents",
    "my documents": "Documents", "document folder": "Documents",
    "documents folder": "Documents", "my document": "Documents",
    "downloads": "Downloads", "download": "Downloads",
    "my downloads": "Downloads", "downloads folder": "Downloads",
    "desktop": "Desktop", "my desktop": "Desktop",
    "pictures": "Pictures", "photos": "Pictures", "my pictures": "Pictures",
    "music": "Music", "my music": "Music",
    "videos": "Videos", "video": "Videos", "my videos": "Videos",
}


def resolve_user_folder(sub):
    """Absolute path of a well-known user folder, honouring OneDrive redirection."""
    if sub.lower() == "documents":
        return _documents_dir()          # already handles the OneDrive shuffle
    home = os.path.expanduser("~")
    for base in (home, os.path.join(home, "OneDrive")):
        candidate = os.path.join(base, sub)
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(home, sub)


def open_folder(path=None):
    """Open Explorer at `path`, or Explorer's home view when path is None."""
    try:
        if path is None:
            subprocess.Popen(["explorer.exe"], shell=False)
            return "Opening File Explorer, Sir."
        if not os.path.isdir(path):
            return f"I could not find that folder, Sir: {path}"
        os.startfile(path)
        return f"Opening {os.path.basename(path.rstrip(os.sep)) or path}, Sir."
    except Exception as e:
        return f"Could not open the folder: {e}"


def open_app(app_name: str):
    key = app_name.lower().strip()

    if key in _FOLDER_ALIASES:
        sub = _FOLDER_ALIASES[key]
        return open_folder(resolve_user_folder(sub) if sub else None)

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

    # Unknown name. Popen(name, shell=True) always "succeeds" here -- it launches
    # cmd, which then prints "'X' is not recognized" to the console while we report
    # success. Resolve the executable up front so the answer is honest.
    exe = shutil.which(app_name) or shutil.which(f"{app_name}.exe")
    if exe:
        try:
            subprocess.Popen([exe], shell=False)
            return f"Opening {app_name}, Sir."
        except Exception as e:
            return f"Could not open {app_name}: {e}"

    # Could still be a folder path, a Store alias, or a protocol Explorer knows.
    try:
        os.startfile(app_name)
        return f"Opening {app_name}, Sir."
    except Exception:
        pass

    return f"I could not find anything called {app_name} on this PC, Sir."
