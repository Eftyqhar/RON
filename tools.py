import subprocess
import os
import re
import shutil
import inspect
import tempfile
import webbrowser

import fpdf as _fpdf
from fpdf import FPDF

def _find_brave_binary() -> str | None:
    """Return Brave's executable path when installed, otherwise None."""
    candidates = [
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
    ]
    return next((p for p in candidates if os.path.isfile(p)), None)


def _find_browser_binary() -> str | None:
    """Return an available Chromium browser binary (Brave, Chrome, Edge) or None."""
    candidates = [
        _find_brave_binary(),
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Microsoft", "Edge", "Application", "msedge.exe"),
    ]
    return next((p for p in candidates if p and os.path.isfile(p)), None)


APPS = {
    "notepad": ("exe", "notepad.exe"),
    "calculator": ("exe", "calc.exe"),
    "chrome": ("exe", "chrome.exe"),
    "firefox": ("exe", "firefox.exe"),
    "brave": ("exe", _find_brave_binary() or "C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe"),
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
        import pywhatkit
        video_url = pywhatkit.playonyt(search_query, open_video=False)
        if video_url:
            open_website(video_url)
            return f"Playing {search_query} on YouTube, Sir."
    except Exception:
        pass

    import urllib.parse
    search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(search_query)}"
    open_website(search_url)
    return f"Playing {search_query} on YouTube, Sir."

def open_website(url: str):
    try:
        clean_url = str(url or "").strip()
        if not re.match(r"^[a-zA-Z]+://", clean_url):
            clean_url = f"https://{clean_url}"

        brave = _find_brave_binary()
        if brave:
            # Invoking brave with the URL without --new-window or --user-data-dir
            # instructs the existing running Brave instance to open the URL in
            # a new tab within the previous window using the user's regular profile.
            subprocess.Popen([brave, clean_url])
        else:
            webbrowser.open_new_tab(clean_url)

        domain = re.sub(r"^https?://(?:www\.)?", "", clean_url).split("/")[0].split(".")[0]
        label = domain.capitalize() if domain else clean_url
        return f"Opening {label}, Sir."
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

_BANGLA_FONTS = [
    ("RonSans", "kalpurush.ttf", "kalpurush.ttf", "kalpurush.ttf", "kalpurush.ttf"),
    ("RonSans", "Nirmala.ttf", "NirmalaB.ttf", "Nirmala.ttf", "NirmalaB.ttf"),
    ("RonSans", "ARIALUNI.TTF", "ARIALUNI.TTF", "ARIALUNI.TTF", "ARIALUNI.TTF"),
]

_CJK_RE = re.compile(r"[\u3000-\u9fff\uac00-\ud7af\uff00-\uffef]")
_BANGLA_RE = re.compile(r"[\u0980-\u09ff]")
_COMPLEX_SCRIPT_RE = re.compile(
    r"[\u0980-\u09ff"  # Bengali / Bangla
    r"\u0900-\u097f"  # Devanagari
    r"\u0a00-\u0a7f"  # Gurmukhi
    r"\u0a80-\u0aff"  # Gujarati
    r"\u0b00-\u0b7f"  # Odia
    r"\u0b80-\u0bff"  # Tamil
    r"\u0c00-\u0c7f"  # Telugu
    r"\u0c80-\u0cff"  # Kannada
    r"\u0d00-\u0d7f"  # Malayalam
    r"\u0d80-\u0dff"  # Sinhala
    r"\u0600-\u06ff"  # Arabic / Urdu / Persian
    r"]"
)

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
    """FPDF with a page-number footer and zero-width mark width fix."""

    def get_string_width(self, s):
        """Fix FPDF 1.7.2 adding 65535 for zero-width combining marks (hasant/matras)."""
        s = self.normalize_text(s)
        cw = self.current_font['cw']
        w = 0
        if self.unifontsubset:
            for char in s:
                code = ord(char)
                if len(cw) > code:
                    val = cw[code]
                    if val != 65535:
                        w += val
                elif self.current_font['desc']['MissingWidth']:
                    w += self.current_font['desc']['MissingWidth']
                else:
                    w += 500
        else:
            for ch in s:
                w += cw.get(ch, 0)
        return w * self.font_size / 1000.0

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
    needs_bangla = bool(_BANGLA_RE.search(content))
    needs_cjk = bool(_CJK_RE.search(content))
    if needs_bangla:
        candidates = _BANGLA_FONTS + _LATIN_FONTS
    elif needs_cjk:
        candidates = _CJK_FONTS + _LATIN_FONTS
    else:
        candidates = list(_LATIN_FONTS)

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
            _write(pdf, family, 12,
                   f"{glyph}  {_clean_inline(blist.group(2))}",
                   height=7.0, indent=5 + depth * 6, md=md)
            continue

        # Numbered list
        nlist = re.match(r"^(\s*)(\d+)[.)]\s+(.*)", raw)
        if nlist:
            _write(pdf, family, 12,
                   f"{nlist.group(2)}.  {_clean_inline(nlist.group(3))}",
                   height=7.0, indent=5, md=md)
            continue

        # Blockquote
        if stripped.startswith(">"):
            _write(pdf, family, 11.5, _clean_inline(stripped.lstrip("> ")),
                   style="I", height=6.8, indent=6, md=md)
            continue

        _write(pdf, family, 12, _clean_inline(line), height=7.0, md=md)


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



def _is_html(content: str) -> bool:
    """Check if content is an HTML document or page section layout."""
    if not content:
        return False
    stripped = content.strip()
    return bool(re.search(r"<!doctype\s+html|<html[\s>]|<section\s+class=[\"']page", stripped, re.IGNORECASE))


def _ensure_unicode_fonts(html_doc: str) -> str:
    """Ensure HTML includes proper font-family fallback for Bengali and complex scripts."""
    font_stack = "'Kalpurush', 'SolaimanLipi', 'Siyam Rupali', 'Nirmala UI', Inter, 'Segoe UI', Arial, sans-serif"
    if "Kalpurush" not in html_doc:
        if re.search(r"font-family\s*:", html_doc):
            html_doc = re.sub(r"font-family\s*:\s*[^;}]+;", f"font-family: {font_stack};", html_doc, count=1)
        elif "<head>" in html_doc:
            html_doc = html_doc.replace("<head>", f"<head><style>body {{ font-family: {font_stack} !important; }}</style>")
    return html_doc


_EXECUTIVE_CSS = """
@page {
    size: A4;
    margin: 18mm 16mm 18mm 16mm;
    @top-left {
        content: "RON · INTELLIGENCE";
        font-family: 'Kalpurush', 'SolaimanLipi', 'Siyam Rupali', 'Nirmala UI', Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        font-size: 8.5pt;
        font-weight: 800;
        color: #0f172a;
        letter-spacing: 0.5px;
    }
    @top-right {
        content: "EXECUTIVE REPORT";
        font-family: 'Kalpurush', 'SolaimanLipi', 'Siyam Rupali', 'Nirmala UI', Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        font-size: 8pt;
        font-weight: 600;
        color: #64748b;
        letter-spacing: 0.8px;
    }
    @bottom-left {
        content: "CONFIDENTIAL & PROPRIETARY";
        font-family: 'Kalpurush', 'SolaimanLipi', 'Siyam Rupali', 'Nirmala UI', Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        font-size: 8pt;
        color: #94a3b8;
    }
    @bottom-right {
        content: "Page " counter(page);
        font-family: 'Kalpurush', 'SolaimanLipi', 'Siyam Rupali', 'Nirmala UI', Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        font-size: 8.5pt;
        font-weight: 600;
        color: #64748b;
    }
}

@page :first {
    @top-left { content: normal; }
    @top-right { content: normal; }
}

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    padding: 0;
    background: #fff;
    color: #1e293b;
    font-family: 'Kalpurush', 'SolaimanLipi', 'Siyam Rupali', 'Nirmala UI', Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 14px;
    line-height: 1.65;
    -webkit-font-smoothing: antialiased;
}

/* Executive Header Banner at the start of Page 1 */
.doc-header {
    border-bottom: 2px solid #0f172a;
    padding-bottom: 14px;
    margin-bottom: 18px;
}
.brand-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 4px;
}
.brand-bar .logo {
    font-weight: 800;
    font-size: 22px;
    letter-spacing: -0.5px;
    color: #0f172a;
}
.brand-bar .logo span {
    color: #2563eb;
}
.brand-bar .kicker {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    font-weight: 700;
    color: #64748b;
}
.doc-title {
    font-size: 30px;
    line-height: 1.2;
    font-weight: 800;
    color: #0f172a;
    letter-spacing: -0.6px;
    margin: 6px 0 10px;
}
.meta-strip {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 8px;
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    padding: 8px 12px;
    border-radius: 6px;
    margin-bottom: 12px;
}
.meta-item .lbl {
    display: block;
    color: #64748b;
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    font-weight: 700;
}
.meta-item .val {
    font-size: 12.5px;
    font-weight: 700;
    color: #0f172a;
}
.exec-summary-box {
    background: #f1f5f9;
    border-left: 4px solid #2563eb;
    padding: 10px 14px;
    border-radius: 0 6px 6px 0;
}
.exec-summary-box .summary-label {
    font-size: 9.5px;
    font-weight: 800;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: #2563eb;
    margin-bottom: 3px;
}
.exec-summary-box p {
    margin: 0;
    font-size: 13.5px;
    line-height: 1.55;
    color: #1e293b;
}

/* Headings with orphan protection */
h1, h2, h3, h4 {
    color: #0f172a;
    break-after: avoid;
    page-break-after: avoid;
}
h2 {
    font-size: 18px;
    font-weight: 700;
    letter-spacing: -0.3px;
    margin: 18px 0 8px;
    border-bottom: 1.5px solid #e2e8f0;
    padding-bottom: 4px;
}
h3 {
    font-size: 15px;
    font-weight: 600;
    margin: 14px 0 5px;
    color: #1e293b;
}

/* Paragraphs and Lists */
p {
    margin: 7px 0 9px;
    color: #334155;
    font-size: 14px;
    line-height: 1.65;
}
ul, ol {
    margin: 6px 0 10px;
    padding-left: 20px;
}
li {
    margin-bottom: 4px;
    color: #334155;
    font-size: 14px;
    line-height: 1.6;
}

/* Tables */
table {
    width: 100%;
    border-collapse: collapse;
    margin: 12px 0 16px;
    font-size: 13px;
    break-inside: avoid;
    page-break-inside: avoid;
}
th {
    background: #1e293b;
    color: #fff;
    text-align: left;
    padding: 8px 10px;
    font-weight: 600;
}
td {
    padding: 7px 10px;
    border-bottom: 1px solid #e2e8f0;
    color: #334155;
}
tr:nth-child(even) td {
    background: #f8fafc;
}

/* Callouts & Summaries */
.summary {
    background: #f8fafc;
    border-left: 4px solid #2563eb;
    padding: 10px 14px;
    margin: 12px 0;
    border-radius: 0 6px 6px 0;
    break-inside: avoid;
    page-break-inside: avoid;
}
.summary p {
    margin: 0;
    color: #1e293b;
    font-size: 13.5px;
    line-height: 1.6;
}

/* Cards & Multi-column */
.card {
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 10px 12px;
    margin: 6px 0;
    background: #f8fafc;
    break-inside: avoid;
    page-break-inside: avoid;
}
.card-title {
    font-weight: 700;
    font-size: 13.5px;
    color: #0f172a;
}
.card p {
    margin: 3px 0 0;
    font-size: 13px;
    color: #475569;
}
.two {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    break-inside: avoid;
    page-break-inside: avoid;
}

/* Timeline */
.timeline {
    position: relative;
    margin: 12px 0 6px;
    padding-left: 18px;
    border-left: 2px solid #cbd5e1;
    break-inside: avoid;
    page-break-inside: avoid;
}
.event {
    position: relative;
    margin: 0 0 12px;
    padding-left: 8px;
}
.event:before {
    content: "";
    position: absolute;
    left: -24px;
    top: 6px;
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: #2563eb;
}
.year {
    font-size: 13px;
    font-weight: 800;
    color: #0f172a;
}
.event strong {
    font-size: 13px;
    color: #1e293b;
}
.event p {
    margin-top: 2px;
    font-size: 13px;
}

/* Code */
pre {
    background: #0f172a;
    color: #f8fafc;
    padding: 10px 12px;
    border-radius: 6px;
    font-size: 12.5px;
    overflow-x: auto;
    break-inside: avoid;
    page-break-inside: avoid;
}
code {
    font-family: Consolas, Monaco, "Courier New", monospace;
    font-size: 12.5px;
}

/* Flow Diagram */
.flow {
    display: flex;
    align-items: center;
    gap: 6px;
    margin: 14px 0;
    break-inside: avoid;
    page-break-inside: avoid;
}
.node {
    flex: 1;
    text-align: center;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 8px 6px;
    font-size: 11px;
    font-weight: 700;
    background: #f8fafc;
    color: #0f172a;
}
.arrow {
    color: #64748b;
    font-weight: bold;
}
"""


def _markdown_to_html_doc(md_text: str, title: str = "Document") -> str:
    """Format Markdown into high-density executive layout with natural pagination and zero empty pages."""
    import html
    is_bangla = bool(re.search(r"[\u0980-\u09FF]", md_text))
    lines = md_text.strip().splitlines()

    doc_title = title or "Document"
    for line in lines:
        if line.startswith("# "):
            doc_title = line[2:].strip()
            break

    def format_inline(text: str) -> str:
        text = html.escape(text)
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"__(.+?)__", r"<strong>\1</strong>", text)
        text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
        text = re.sub(r"_([^_]+?)_", r"<em>\1</em>", text)
        text = re.sub(r"`([^`]+?)`", r"<code>\1</code>", text)
        return text

    # Split into sections by ##
    raw_sections = []
    current_heading = ""
    current_lines = []
    lead_paragraph = ""

    for line in lines:
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            if current_heading or current_lines:
                raw_sections.append((current_heading, current_lines))
            current_heading = line[3:].strip()
            current_lines = []
        else:
            if not current_heading and line.strip() and not lead_paragraph:
                lead_paragraph = line.strip()
            elif current_heading:
                current_lines.append(line)
            elif line.strip():
                current_lines.append(line)

    if current_heading or current_lines:
        raw_sections.append((current_heading, current_lines))

    if not lead_paragraph and raw_sections:
        for l in raw_sections[0][1]:
            if l.strip() and not l.startswith("#") and not l.startswith("-") and not l.startswith("|") and not l.startswith(">"):
                lead_paragraph = l.strip()
                break

    if not lead_paragraph:
        lead_paragraph = "Comprehensive executive report and reference analysis." if not is_bangla else "বিস্তারিত গবেষণা ও তথ্যবহুল প্রতিবেদন।"

    kicker = "EXECUTIVE DOSSIER · INTELLIGENCE REPORT" if not is_bangla else "নির্বাহী গবেষণা প্রতিবেদন · বিশেষ প্রকাশনা"
    rep_type = "Executive Dossier" if not is_bangla else "গবেষণা প্রতিবেদন"
    period = "2026 Edition" if not is_bangla else "২০২৬ সংস্করণ"

    html_parts = []

    # 1. Page 1 Executive Header Banner
    header_html = f"""<header class="doc-header">
  <div class="brand-bar">
    <div class="logo">RON<span>TECH</span></div>
    <div class="kicker">{html.escape(kicker)}</div>
  </div>
  <h1 class="doc-title">{format_inline(doc_title)}</h1>
  <div class="meta-strip">
    <div class="meta-item"><span class="lbl">{'Report Type' if not is_bangla else 'প্রতিবেদন ধরন'}</span><span class="val">{html.escape(rep_type)}</span></div>
    <div class="meta-item"><span class="lbl">{'Edition' if not is_bangla else 'সংস্করণ'}</span><span class="val">{html.escape(period)}</span></div>
    <div class="meta-item"><span class="lbl">{'Verification' if not is_bangla else 'যাচাইকরণ'}</span><span class="val">AI Synthesized</span></div>
    <div class="meta-item"><span class="lbl">{'Classification' if not is_bangla else 'শ্রেণিবিভাগ'}</span><span class="val">Unrestricted</span></div>
  </div>
  <div class="exec-summary-box">
    <div class="summary-label">{'Executive Summary' if not is_bangla else 'সারসংক্ষেপ'}</div>
    <p>{format_inline(lead_paragraph)}</p>
  </div>
</header>"""
    html_parts.append(header_html)

    # 2. Continuous Sections with zero forced blank pages
    for sec_idx, (h, sec_lines) in enumerate(raw_sections):
        if h:
            html_parts.append(f"<h2>{sec_idx+1:02d} · {format_inline(h)}</h2>")

        i = 0
        while i < len(sec_lines):
            line = sec_lines[i].strip()
            if not line:
                i += 1
                continue

            # Code fence
            if line.startswith("```"):
                i += 1
                code_lines = []
                while i < len(sec_lines) and not sec_lines[i].strip().startswith("```"):
                    code_lines.append(html.escape(sec_lines[i]))
                    i += 1
                i += 1
                html_parts.append(f"<pre><code>{'<br>'.join(code_lines)}</code></pre>")
                continue

            # Horizontal rule
            if re.fullmatch(r"([-*_])\s{2,}", line) or re.fullmatch(r"([-*_]){3,}", line):
                html_parts.append("<hr style='border:none;border-top:1px solid #e2e8f0;margin:12px 0;'>")
                i += 1
                continue

            # Table detection
            if line.startswith("|") and line.endswith("|"):
                table_lines = []
                while i < len(sec_lines) and sec_lines[i].strip().startswith("|"):
                    table_lines.append(sec_lines[i].strip())
                    i += 1
                if len(table_lines) >= 2:
                    t_html = ["<table>"]
                    th_cols = [c.strip() for c in table_lines[0].split("|")[1:-1]]
                    t_html.append("<tr>" + "".join(f"<th>{format_inline(c)}</th>" for c in th_cols) + "</tr>")
                    start_row = 2 if "---" in table_lines[1] else 1
                    for r_line in table_lines[start_row:]:
                        td_cols = [c.strip() for c in r_line.split("|")[1:-1]]
                        t_html.append("<tr>" + "".join(f"<td>{format_inline(c)}</td>" for c in td_cols) + "</tr>")
                    t_html.append("</table>")
                    html_parts.append("\n".join(t_html))
                continue

            # Subheading
            if line.startswith("### "):
                html_parts.append(f"<h3>{format_inline(line[4:].strip())}</h3>")
                i += 1
                continue

            # Quote / Callout
            if line.startswith(">"):
                quote_lines = []
                while i < len(sec_lines) and sec_lines[i].strip().startswith(">"):
                    quote_lines.append(sec_lines[i].strip().lstrip("> ").strip())
                    i += 1
                quote_text = " ".join(quote_lines)
                html_parts.append(f'<div class="summary"><p>{format_inline(quote_text)}</p></div>')
                continue

            # Bullet list or timeline or card
            if line.startswith("- ") or line.startswith("* ") or re.match(r"^\d+[.)]\s+", line):
                bullet_lines = []
                is_ordered = bool(re.match(r"^\d+[.)]\s+", line))
                while i < len(sec_lines) and (sec_lines[i].strip().startswith("- ") or sec_lines[i].strip().startswith("* ") or re.match(r"^\d+[.)]\s+", sec_lines[i].strip())):
                    cleaned_b = re.sub(r"^[-*\d.)]+\s+", "", sec_lines[i].strip()).strip()
                    bullet_lines.append(cleaned_b)
                    i += 1

                # Timeline check
                is_timeline = any(re.match(r"^\*{0,2}[\d\w\s–—-]{3,15}\*{0,2}[:–—]", b) for b in bullet_lines)
                # Two-column cards check
                is_card = all(":" in b or " — " in b or " -- " in b for b in bullet_lines) and len(bullet_lines) in (2, 4, 6)

                if is_timeline:
                    tl_html = ['<div class="timeline">']
                    for b in bullet_lines:
                        m = re.match(r"^\*{0,2}([\d\w\s–—-]{3,20})\*{0,2}[:–—]\s*(.*)", b)
                        if m:
                            yr, rest = m.group(1).strip(), m.group(2).strip()
                            tl_html.append(f'<div class="event"><div class="year">{format_inline(yr)}</div><p>{format_inline(rest)}</p></div>')
                        else:
                            tl_html.append(f'<div class="event"><p>{format_inline(b)}</p></div>')
                    tl_html.append('</div>')
                    html_parts.append("\n".join(tl_html))
                elif is_card:
                    c_html = ['<div class="two">']
                    mid = (len(bullet_lines) + 1) // 2
                    col1 = bullet_lines[:mid]
                    col2 = bullet_lines[mid:]

                    c_html.append('<div>')
                    for b in col1:
                        parts = re.split(r"[:–—]\s*|\s+--\s+", b, maxsplit=1)
                        ctitle = parts[0].strip().strip("*")
                        cdesc = parts[1].strip() if len(parts) > 1 else ""
                        c_html.append(f'<div class="card"><div class="card-title">{format_inline(ctitle)}</div><p>{format_inline(cdesc)}</p></div>')
                    c_html.append('</div>')

                    c_html.append('<div>')
                    for b in col2:
                        parts = re.split(r"[:–—]\s*|\s+--\s+", b, maxsplit=1)
                        ctitle = parts[0].strip().strip("*")
                        cdesc = parts[1].strip() if len(parts) > 1 else ""
                        c_html.append(f'<div class="card"><div class="card-title">{format_inline(ctitle)}</div><p>{format_inline(cdesc)}</p></div>')
                    c_html.append('</div>')
                    c_html.append('</div>')
                    html_parts.append("\n".join(c_html))
                else:
                    tag = "ol" if is_ordered else "ul"
                    l_html = [f'<{tag}>']
                    for b in bullet_lines:
                        l_html.append(f'<li>{format_inline(b)}</li>')
                    l_html.append(f'</{tag}>')
                    html_parts.append("\n".join(l_html))
                continue

            # Flow diagram
            if ("->" in line or "→" in line) and len(line) < 140:
                nodes = [n.strip() for n in re.split(r"->|→", line)]
                if len(nodes) >= 2:
                    fl_html = ['<div class="flow">']
                    for idx, node in enumerate(nodes):
                        fl_html.append(f'<div class="node">{format_inline(node)}</div>')
                        if idx < len(nodes) - 1:
                            fl_html.append('<div class="arrow">→</div>')
                    fl_html.append('</div>')
                    html_parts.append("\n".join(fl_html))
                    i += 1
                    continue

            # Regular paragraph
            html_parts.append(f"<p>{format_inline(line)}</p>")
            i += 1

    return f"""<!DOCTYPE html>
<html lang="{'bn' if is_bangla else 'en'}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(doc_title)}</title>
<style>
{_EXECUTIVE_CSS}
</style>
</head>
<body>
{"\n".join(html_parts)}
</body>
</html>"""


def _render_html_to_pdf_browser(html_content: str, output_path: str) -> int | None:
    """Render HTML document to PDF via Chromium headless print engine."""
    browser = _find_browser_binary()
    if not browser:
        return None
    temp_html = output_path + ".temp.html"
    try:
        with open(temp_html, "w", encoding="utf-8") as f:
            f.write(html_content)
        abs_html = os.path.abspath(temp_html)
        abs_pdf = os.path.abspath(output_path)
        cmd = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={abs_pdf}",
            abs_html,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
        if res.returncode == 0 and os.path.isfile(abs_pdf) and os.path.getsize(abs_pdf) > 0:
            try:
                import fitz
                doc = fitz.open(abs_pdf)
                count = len(doc)
                doc.close()
                return max(count, 1)
            except Exception:
                with open(abs_pdf, "rb") as f:
                    data = f.read()
                count = len(re.findall(rb"/Type\s*/Page\b", data))
                return max(count, 1)
    except Exception as e:
        print(f"[Browser HTML PDF export error: {e}]")
    finally:
        if os.path.isfile(temp_html):
            try:
                os.remove(temp_html)
            except Exception:
                pass
    return None


def _render_markdown_to_pdf_browser(md_text: str, title: str, output_path: str) -> int | None:
    """High-fidelity PDF generation via Chromium engine with full OpenType shaping."""
    html_content = _markdown_to_html_doc(md_text, title=title)
    return _render_html_to_pdf_browser(html_content, output_path)


def generate_pdf(file_name: str, content: str, title: str = None):
    """Render document (Markdown or HTML) to a publication-quality PDF in user's Documents."""
    try:
        if _is_unusable(content):
            return ("I could not write the document content, so I did not save "
                    "an empty PDF. Please try again, Sir.")

        content = content.strip()
        is_html_doc = _is_html(content)

        # If Markdown content has no title, prepend one
        if not is_html_doc and title and not content.lstrip().startswith("#"):
            content = f"# {title}\n\n{content}"

        name = _safe_name(file_name, fallback=_safe_name(title or "document"))
        folder = _documents_dir()

        save_path = os.path.join(folder, f"{name}.pdf")
        counter = 2
        while os.path.exists(save_path):
            save_path = os.path.join(folder, f"{name}-{counter}.pdf")
            counter += 1

        pages = None
        browser = _find_browser_binary()

        # Render using Chromium headless engine whenever available for exact executive layout
        if browser:
            if is_html_doc:
                html_doc = _ensure_unicode_fonts(content)
                pages = _render_html_to_pdf_browser(html_doc, save_path)
            else:
                pages = _render_markdown_to_pdf_browser(content, title or name, save_path)

        # Fallback to legacy FPDF if browser is not installed or headless export failed
        if pages is None:
            try:
                pdf = _build_pdf(content, title, name, md=True)
            except Exception as e:
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


def _webpage_name(topic, fallback="page"):
    """A filesystem-safe slug for the webpage file name."""
    name = re.sub(r"[^\w\s-]", "", (topic or fallback).strip())
    name = re.sub(r"\s+", "_", name).strip("_").lower()[:60]
    return name or fallback


def _webpage_dir():
    """Save webpages to a 'Webpages' subfolder of Documents, creating it once."""
    folder = os.path.join(_documents_dir(), "Webpages")
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception:
        pass
    return folder


def generate_webpage(file_name: str, content: str, title: str = None):
    """Write a self-contained HTML page (inline CSS + JS) to the user's
    Webpages folder and open it in the default browser.

    `content` is expected to be a complete HTML document produced by the
    writer turn. A thin wrapper is added only when the model returned a
    fragment instead of a full document.
    """
    try:
        if _is_unusable(content):
            return ("I could not write the webpage content, so I did not save "
                    "an empty file. Please try again, Sir.")

        content = content.strip()

        # If the model returned a fragment (no doctype / <html>), wrap it so the
        # result is a valid, standalone page rather than a broken render.
        if not re.search(r"<!doctype\s+html|<html[\s>]", content, re.IGNORECASE):
            display_title = (title or file_name or "Ron Page").replace("<", "").replace(">", "")
            content = (
                "<!DOCTYPE html>\n"
                "<html lang=\"en\">\n"
                f"<head><meta charset=\"UTF-8\"><meta name=\"viewport\" "
                f"content=\"width=device-width, initial-scale=1.0\">"
                f"<title>{display_title}</title></head>\n"
                "<body>\n" + content + "\n</body>\n</html>"
            )

        name = _safe_name(file_name,
                          fallback=_webpage_name(title or "page", "page"))
        folder = _webpage_dir()

        save_path = os.path.join(folder, f"{name}.html")
        counter = 2
        while os.path.exists(save_path):
            save_path = os.path.join(folder, f"{name}-{counter}.html")
            counter += 1

        with open(save_path, "w", encoding="utf-8") as f:
            f.write(content)

        size = os.path.getsize(save_path)

        try:
            # RON_NO_OPEN lets the test suite render without spawning viewers.
            if hasattr(os, "startfile") and not os.environ.get("RON_NO_OPEN"):
                os.startfile(save_path)
        except Exception as e:
            print(f"[Could not open webpage: {e}]")

        print(f"[Webpage] {save_path} ({size} bytes)")
        return f"Your webpage on {title or name} is saved and open, Sir."

    except Exception as e:
        return f"Could not create the webpage: {e}"

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
    "ron share": "Documents\\RON-Share", "ron-share": "Documents\\RON-Share",
    "ron shared": "Documents\\RON-Share", "ron shered": "Documents\\RON-Share",
    "shared folder": "Documents\\RON-Share", "share folder": "Documents\\RON-Share",
    "shered folder": "Documents\\RON-Share", "shred folder": "Documents\\RON-Share",
    "shared files": "Documents\\RON-Share", "share files": "Documents\\RON-Share",
    "shered files": "Documents\\RON-Share", "shred files": "Documents\\RON-Share",
    "ron shared files": "Documents\\RON-Share", "ron share files": "Documents\\RON-Share",
    "ron shered files": "Documents\\RON-Share",
    "ron shared folder": "Documents\\RON-Share", "ron share folder": "Documents\\RON-Share",
    "ron shered folder": "Documents\\RON-Share",
    "my shared folder": "Documents\\RON-Share", "my share folder": "Documents\\RON-Share",
    "my shered folder": "Documents\\RON-Share",
    "shared": "Documents\\RON-Share", "shered": "Documents\\RON-Share", "share": "Documents\\RON-Share",
    "শেয়ার ফোল্ডার": "Documents\\RON-Share", "শেয়ার ফোল্ডার": "Documents\\RON-Share",
}


def get_ron_share_dir() -> str:
    """Return the absolute path to the Documents/RON-Share directory, creating it if needed."""
    path = os.path.join(_documents_dir(), "RON-Share")
    os.makedirs(path, exist_ok=True)
    return path


def resolve_user_folder(sub):
    """Absolute path of a well-known user folder, honouring OneDrive redirection."""
    if sub.lower() == "documents":
        return _documents_dir()          # already handles the OneDrive shuffle
    if sub.lower() in ("documents/ron-share", "documents\\ron-share", "ron-share"):
        return get_ron_share_dir()
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
        base = os.path.basename(path.rstrip(os.sep)) or path
        if base.lower() == "ron-share":
            return "Opening RON-Share folder, Sir."
        return f"Opening {base}, Sir."
    except Exception as e:
        return f"Could not open the folder: {e}"


def open_app(app_name: str):
    key = app_name.lower().strip()
    key = re.sub(r"\b(?:shered|shred|sheared|cher)\b", "shared", key)

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
