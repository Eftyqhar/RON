"""Email sending and scheduled reminders for RON.

Two responsibilities:

1. **SMTP sending** -- stdlib ``smtplib`` + ``email.mime.text``. Reads its settings
   lazily from ``config.json`` so the app-password can be filled in (or rotated)
   after the module is imported. Any fault is swallowed and surfaced as
   ``False`` rather than being allowed to escape the caller.

2. **Scheduled reminders** -- a wall-clock analogue of ``timer.py``. The user says
   "notify me at 5 pm for study" and a daemon thread sleeps until that moment in
   Bangladesh Standard Time (via ``clock.tz()``), then sends an email to the
   configured ``reminder_email`` and rings a local alarm. The spec dict it
   returns mirrors the timer spec so ``main.py`` can publish it through
   ``bus.reminder()`` for the HUD.

Two rules, inherited from the rest of RON:

1. **Never raises into the caller.** A missing config file, a bad password, or a
   network fault returns a plain-spoken answer, never an exception.
2. **Nothing at import time.** ``config.json`` is read lazily so it can be placed
   or edited after the module is imported.
"""

import datetime
import email
from email.header import decode_header
from email.message import EmailMessage
import email.utils
import html
import imaplib
import os
import re
import smtplib
import threading
import time

import clock

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_config_cache = None


def _dir():
    """The directory RON lives in -- resolved lazily, not at import time."""
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except Exception:
        return os.getcwd()


def _config():
    """Lazy-load ``config.json`` from RON's directory.

    Returns a dict. Any fault (missing file, bad JSON, permission error) yields
    an empty dict rather than propagating, so the rest of RON keeps working.
    """
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    try:
        import json
        with open(os.path.join(_dir(), "config.json"), "r",
                  encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
        _config_cache = data
        return _config_cache
    except Exception:
        _config_cache = {}
        return _config_cache


def _smtp_config():
    """The SMTP settings block, with sane Gmail defaults filled in."""
    cfg = _config()
    smtp = cfg.get("smtp") or {}
    return {
        "user": smtp.get("user", "mohammadeftyqhar@gmail.com"),
        "app_password": smtp.get("app_password", ""),
        "host": smtp.get("host", "smtp.gmail.com"),
        "port": int(smtp.get("port", 587)),
    }


def _imap_config():
    """The IMAP settings block for reading incoming mail, defaulting to Gmail."""
    cfg = _config()
    imap = cfg.get("imap") or {}
    smtp = cfg.get("smtp") or {}
    return {
        "user": (imap.get("user") or smtp.get("user", "mohammadeftyqhar@gmail.com")).strip(),
        "app_password": (imap.get("app_password") or smtp.get("app_password", "")).strip(),
        "host": (imap.get("host") or "imap.gmail.com").strip(),
        "port": int(imap.get("port") or 993),
    }


def _reminder_to():
    """The address a scheduled reminder is sent to -- the user's own by default."""
    cfg = _config()
    return (cfg.get("reminder_email") or
            _smtp_config()["user"]).strip()


_contacts_cache = None


def _contacts():
    """Lazy-load ``contacts.json`` from RON's directory.

    Returns a dict mapping alias -> email. Any fault yields an empty dict.
    """
    global _contacts_cache
    if _contacts_cache is not None:
        return _contacts_cache
    try:
        import json
        with open(os.path.join(_dir(), "contacts.json"), "r",
                  encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
        _contacts_cache = {str(k).lower().strip(): str(v).strip()
                           for k, v in data.items()}
        return _contacts_cache
    except Exception:
        _contacts_cache = {}
        return _contacts_cache


def resolve_recipient(raw):
    """Turn a spoken recipient into an email address.

    Accepts:

    * a real email address;
    * an alias from ``contacts.json`` (case-insensitive);
    * the special token ``"me"``, which resolves to the SMTP user / reminder email.

    Returns the resolved address, or ``None`` if it cannot be resolved.
    """
    candidate = (raw or "").strip()
    if not candidate:
        return None
    low = candidate.lower()
    contacts = _contacts()
    if low in contacts:
        return contacts[low]
    if low == "me":
        return (_reminder_to() or _smtp_config()["user"]).strip()
    if "@" in candidate and "." in candidate:
        return candidate
    return None


# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------

# Patterns that turn a phrase like "5 pm", "5:30 pm", "5.pm", "17:00" into a
# (hour, minute, ampm) triple. The ampm slot is None for a 24-hour figure.
_TIME_PATTERNS = [
    # "5 pm", "5pm", "5.30 pm", "5.30pm", "five pm"
    re.compile(
        r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten"
        r"|eleven|twelve)\s*"
        r"(?::(\d{2}))?"
        r"(?:\s*[.:\s]\s*(\d{2}))?"
        r"\s*(a\.?m\.?|p\.?m\.?)\b", re.IGNORECASE),
    # "at 5 pm", "on 5.pm" -- the preposition is stripped before we get here,
    # but keep this for bare "5 pm" with a leading boundary.
    re.compile(
        r"\b(\d{1,2})\s*(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)\b",
        re.IGNORECASE),
    # 24-hour "17:00", "09:30"
    re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b"),
]

_WORD_HOURS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


def parse_time(time_str):
    """Turn a time phrase into a ``datetime.time``.

    Accepts ``"5 pm"``, ``"5:30 pm"``, ``"5.pm"``, ``"17:00"``, word-hours
    (``"five pm"``). Returns ``None`` on anything unrecognised rather than
    raising.
    """
    text = (time_str or "").strip().lower()
    if not text:
        return None
    # Normalise "noon" / "midnight" shortcuts first.
    if text == "noon":
        return datetime.time(12, 0)
    if text == "midnight":
        return datetime.time(0, 0)
    # Normalise "5.pm", "5.30 pm" -> "5:30 pm" so one parser handles the lot.
    # A dot can also sit directly before the meridiem ("5.pm"), so collapse
    # that separator while keeping the hour digit.
    text = re.sub(r"\.(\d)", r":\1", text)
    text = re.sub(r"(\d)\s*\.\s*(a\.?m\.?|p\.?m\.?)", r"\1 \2", text)
    text = re.sub(r"\s+", " ", text).strip()

    # 24-hour "HH:MM"
    m = re.match(r"^\s*([01]?\d|2[0-3]):([0-5]\d)\s*$", text)
    if m:
        return datetime.time(int(m.group(1)), int(m.group(2)))

    # "H[:MM] am/pm", with optional word-hours, and optional bare hour without
    # meridiem (assumed daytime).
    m = re.match(
        r"^\s*(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten"
        r"|eleven|twelve)"
        r"(?:\s+(\d{1,2}))?"          # word-minute after a space: "five thirty"
        r"(?::(\d{1,2}))?"            # numeric minute after colon: "5:30"
        r"\s*(a\.?m\.?|p\.?m\.?)?"   # optional meridiem
        r"\s*$", text, re.IGNORECASE)
    if not m:
        return None
    hour_raw = m.group(1).lower()
    hour = _WORD_HOURS.get(hour_raw)
    if hour is None:
        try:
            hour = int(hour_raw)
        except ValueError:
            return None
    if not (1 <= hour <= 12):
        return None
    # Prefer numeric minute; fall back to word-minute if present.
    minute_raw = m.group(3)
    if minute_raw is None:
        minute_raw = m.group(2)
    minute = int(minute_raw) if minute_raw else 0
    if minute < 0 or minute > 59:
        return None
    meridiem = (m.group(4) or "am").lower().replace(".", "")
    if meridiem.startswith("p") and hour != 12:
        hour += 12
    elif meridiem.startswith("a") and hour == 12:
        hour = 0
    return datetime.time(hour, minute)


def next_occurrence(target_time, tz):
    """The next ``datetime.datetime`` in `tz` whose clock reads `target_time`.

    If that time has already passed today, rolls to tomorrow. Midnight is
    treated as "tonight coming up" rather than "this instant" when the clock is
    already past it, so a reminder set at 00:05 for "midnight" lands on the
    approaching midnight, not the one just gone.
    """
    now = datetime.datetime.now(tz)
    candidate = now.replace(
        hour=target_time.hour, minute=target_time.minute,
        second=0, microsecond=0)
    if candidate <= now:
        candidate += datetime.timedelta(days=1)
    return candidate


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

# A clean, company-style email body. The accent colour and brand label are
# parameterised so reminders (Ron) and user messages (your name) share the same
# template. Inline CSS only -- email clients strip <style> blocks.
_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f4f7;font-family:Segoe UI,Helvetica Neue,Arial,sans-serif;">
<table role="presentation" style="width:100%;border-collapse:collapse;background:#f4f4f7;">
<tr><td align="center" style="padding:40px 0;">
<table role="presentation" style="width:600px;max-width:600px;border-collapse:collapse;background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.06);">
<tr>
  <td style="background:{accent};padding:32px 40px;text-align:center;">
    <div style="font-size:14px;font-weight:700;letter-spacing:1.2px;color:#ffffff;text-transform:uppercase;margin:0 0 6px;">{brand}</div>
  </td>
</tr>
<tr><td style="padding:40px 40px 32px;">
  <h2 style="margin:0 0 16px;color:#1a1a2e;font-size:22px;font-weight:700;line-height:1.35;">{subject}</h2>
  <div style="color:#4a4a68;font-size:15px;line-height:1.75;">
  {body}
  </div>
</td></tr>
<tr><td style="padding:0 40px 32px;">
  <p style="margin:0 0 8px;color:#6b6b8a;font-size:13px;">{closing}</p>
  <p style="margin:0;color:#9b9bb5;font-size:12px;">{signature}</p>
</td></tr>
<tr>
  <td style="background:#f8f8fb;padding:16px 40px;border-top:1px solid #eeeeee;">
    <p style="margin:0;color:#9b9bb5;font-size:11px;line-height:1.6;">
    {footer}
    </p>
  </td>
</tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def _render_template(subject, body_html, brand="Ron", accent="#2563eb",
                     closing="", signature="Ron Voice Assistant",
                     footer="This is an automated message sent by Ron, Ifteqhar's assistant."):
    """Fill the HTML email template. `body_html` may contain inline HTML tags."""
    return _TEMPLATE.format(
        accent=accent, brand=brand, subject=_esc_html(subject),
        body=body_html, closing=_esc_html(closing),
        signature=_esc_html(signature), footer=_esc_html(footer),
    )


def _esc_html(s):
    """Minimal HTML escaping for user-supplied strings injected into the template."""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(
        ">", "&gt;").replace('"', "&quot;")


def _message_body(subject, body, is_reminder=False, recipient_name="",
                  body_is_html=False):
    """Build the HTML body fragment and pick brand/accent/closing for the template.

    Reminders get a green "Reminder" header; user messages get a blue "Message"
    header. The plain body is wrapped in a styled paragraph, framed by a
    professional greeting and a polite closing line.

    When `body_is_html` is true the body is already an inline-CSS HTML fragment
    (from the LLM writer turn) and is injected verbatim -- no escaping, no
    re-wrapping in <p> -- so its tags and formatting survive intact. The
    greeting and closing line are still added around it.
    """
    accent = "#16a34a" if is_reminder else "#2563eb"
    brand = "Reminder" if is_reminder else "Message"
    closing = "Best regards" if not is_reminder else ""
    signature = "Ron Voice Assistant"

    if is_reminder:
        # Reminder body is already a fully-formed HTML fragment; just return it.
        return body, brand, accent, closing, signature

    name = (recipient_name or "").strip()
    greeting = (
        f'<p style="margin:0 0 16px;font-size:15px;">'
        f'Dear {_esc_html(name)},</p>' if name else
        '<p style="margin:0 0 16px;font-size:15px;">Hello,</p>')
    close_line = (
        '<p style="margin:16px 0 0;color:#6b6b8a;font-size:14px;">'
        'If you have any questions, please feel free to reach out.</p>'
    )

    if body_is_html:
        # The LLM writer already produced inline-CSS HTML; inject it between
        # greeting and closing without escaping or re-wrapping.
        body_html = f"{greeting}{body}{close_line}"
        return body_html, brand, accent, closing, signature

    # Wrap the raw body in <p> tags, preserving line breaks.
    paras = [p.strip() for p in (body or "").split("\n") if p.strip()]
    if not paras:
        paras = [""]
    body_paras = "\n".join(
        f'<p style="margin:0 0 12px;">{_esc_html(p)}</p>' for p in paras
    )
    body_html = f"{greeting}{body_paras}{close_line}"
    return body_html, brand, accent, closing, signature


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def send_email(to, subject, body, is_html=False, recipient_name="",
               body_is_html=False):
    """Send an email through the configured SMTP host.

    Returns ``True`` on success, ``False`` on any fault (missing config, bad
    password, network problem, invalid address). The fault is logged to stdout
    for the developer but never raised, so a broken mail run cannot take down
    the assistant.

    Each message is sent as ``multipart/alternative`` with both a plain-text
    and an HTML part, so it reads cleanly in any mail client.

    `recipient_name` personalises the salutation (``"Dear Rifat,"``); if
    omitted the template falls back to ``"Hello,"``.

    There are two HTML-related flags because two callers hand us HTML at
    different stages:

    - `is_html=True` -- the body is already a complete, template-ready HTML
      fragment (e.g. a scheduled reminder that has been through
      ``_message_body(is_reminder=True)``). It is injected verbatim.
    - `body_is_html=True` -- the body is an inline-CSS HTML fragment produced
      by the LLM writer turn. It is wrapped with a greeting and closing line
      (without escaping or re-wrapping in ``<p>``) before being placed in the
      template.
    """
    to = (to or "").strip()
    subject = (subject or "").strip()
    body = (body or "").strip()
    if not to or not subject:
        return False

    resolved = resolve_recipient(to)
    if not resolved:
        print(f"[email_notify] could not resolve recipient: {to!r}")
        return False

    smtp = _smtp_config()
    if not smtp["app_password"]:
        print("[email_notify] SMTP app_password not set in config.json")
        return False

    try:
        # Plain-text fallback for clients that cannot render HTML.
        plain = body

        if is_html:
            # Caller supplied a complete HTML fragment (reminder path); use it
            # verbatim inside the template.
            html = body
        else:
            # Wrap the body in the professional company-style template.
            # `body_is_html` tells the helper that the body is already an
            # inline-CSS HTML fragment (from the LLM writer turn) and must be
            # injected without escaping or re-wrapping.
            body_html, brand, accent, closing, signature = _message_body(
                subject, body, recipient_name=recipient_name,
                body_is_html=body_is_html)
            html = _render_template(
                subject, body_html, brand=brand, accent=accent,
                closing=closing, signature=signature)

        msg = EmailMessage()
        msg["From"] = smtp["user"]
        msg["To"] = resolved
        msg["Subject"] = subject
        msg.set_content(plain)
        msg.add_alternative(html, subtype="html", charset="utf-8")

        with smtplib.SMTP(smtp["host"], smtp["port"], timeout=20) as server:
            server.starttls()
            server.login(smtp["user"], smtp["app_password"])
            server.sendmail(smtp["user"], [resolved], msg.as_string())
        return True
    except Exception as e:
        print(f"[email_notify] send failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Scheduled reminder -- wall-clock analogue of timer.py
# ---------------------------------------------------------------------------

def schedule_reminder(at, message):
    """Arm a reminder to fire at the ``datetime.datetime`` `at`.

    `at` should be an aware datetime in RON's zone (use `clock.tz()` and
    `next_occurrence()`). Returns a spec dict immediately -- the same shape
    `timer.run()` returns, so `main.py` can publish it through `bus.reminder()`.

    A daemon thread sleeps until `at`, then sends the reminder email to the
    configured ``reminder_email`` and rings a local alarm. A fault in the
    background loop is surfaced as an ``error`` frame rather than escaping the
    thread.
    """
    try:
        tz = clock.tz()
        now = datetime.datetime.now(tz)
        # Make `at` aware in RON's zone if a naive datetime was handed in.
        if at.tzinfo is None:
            at = at.replace(tzinfo=tz)
        delay = max(0.0, (at - now).total_seconds())
    except Exception:
        delay = 0.0

    spec = {
        "scheduled_at": at.isoformat() if at else "",
        "message": message or "",
        "status": "set",
        "ok": True,
        "cancelled": False,
        "done": False,
        "_at": at,
        "_delay": delay,
        "_thread": None,
        "_stop": threading.Event(),
    }

    def _wait():
        try:
            if not spec["_stop"].wait(delay):
                # The wait returned because the deadline arrived (not cancelled).
                spec["status"] = "running"
                _reminder_bus(**hud_payload(spec))
                # Send the mail.
                subject = f"Reminder: {spec['message']}"
                body_html, brand, accent, closing, signature = _message_body(
                    subject, spec["message"], is_reminder=True)
                extra = (
                    f'<p style="margin:8px 0 0;color:#6b6b8a;font-size:13px;">'
                    f'Scheduled for <b>{at.strftime("%I:%M %p %Z, %d %b %Y")}'
                    f'</b>.</p>')
                body_html += extra
                ok = send_email(
                    _reminder_to(), subject, body_html, is_html=True)
                if ok:
                    spec["status"] = "done"
                    spec["done"] = True
                    _ring()
                else:
                    spec["status"] = "error"
                    spec["ok"] = False
                    spec["error"] = "sending failed"
                _reminder_bus(**hud_payload(spec))
        except Exception:
            try:
                spec["status"] = "error"
                spec["ok"] = False
                _reminder_bus(**hud_payload(spec))
            except Exception:
                pass

    spec["_thread"] = threading.Thread(target=_wait, daemon=True)
    spec["_thread"].start()
    # Publish the "set" frame synchronously so the HUD opens the moment the
    # reminder is armed, before the background thread wakes.
    _reminder_bus(**hud_payload(spec))
    return spec


def cancel(spec):
    """Stop a scheduled reminder started by `schedule_reminder()`. Idempotent;
    None-safe."""
    if not spec:
        return
    spec["cancelled"] = True
    spec["done"] = False
    spec["status"] = "cancelled"
    try:
        spec["_stop"].set()
    except Exception:
        pass
    try:
        _reminder_bus(**hud_payload(spec))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Sound
# ---------------------------------------------------------------------------

try:
    import winsound as _winsound
except ImportError:  # pragma: no cover -- non-Windows
    _winsound = None


def _ring():
    """A short alarm for when a reminder fires. Swallowed on any fault."""
    if _winsound is not None:
        try:
            for freq, dur in ((600, 200), (800, 200), (1000, 300)):
                _winsound.Beep(freq, dur)
                _winsound.Beep(400, 80)
            return
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Answers & HUD payload
# ---------------------------------------------------------------------------

# The bus publish function is imported lazily inside `_reminder_bus` so this
# module stays importable even if `bus` is not yet loaded (it is a leaf).
_bus_reminder = None


def _reminder_bus(**values):
    """Publish to `bus.reminder()` if the bus is available. Fault-contained."""
    global _bus_reminder
    try:
        if _bus_reminder is None:
            import bus as _b
            _bus_reminder = _b.reminder
        _bus_reminder(**values)
    except Exception:
        pass


def describe(spec):
    """Spoken answer for a reminder request.

    `spec` may be the live dict returned by `schedule_reminder()` (status
    "set") or a terminal snapshot (status "done"/"cancelled"/"error").
    """
    if not spec:
        return "I could not set the reminder, Sir."
    status = spec.get("status", "")
    message = spec.get("message", "")
    at = spec.get("_at")
    fmt = ""
    if at is not None:
        try:
            fmt = at.strftime("%I:%M %p")
        except Exception:
            pass
    if status in ("set", "running"):
        when = f" for {fmt}" if fmt else ""
        what = f": {message}" if message else ""
        return f"Reminder scheduled{when}{what}, Sir."
    if status == "done":
        what = f": {message}" if message else ""
        return f"Your reminder{what} has been sent, Sir."
    if status == "cancelled":
        return "Reminder cancelled."
    return "I ran into a problem setting the reminder, Sir."


def hud_payload(spec):
    """Flat dict the HUD renders. Tolerant of None and of missing keys."""
    if not spec:
        return {
            "status": "error", "ok": False, "error": "no reminder",
            "scheduled_fmt": "", "message": "",
            "done": False, "cancelled": False,
        }
    status = spec.get("status", "error")
    ok = bool(spec.get("ok", status != "error"))
    at = spec.get("_at")
    fmt = ""
    if at is not None:
        try:
            fmt = at.strftime("%I:%M %p %Z")
        except Exception:
            pass
    return {
        "status": status,
        "ok": ok,
        "error": spec.get("error") if status == "error" else None,
        "scheduled_fmt": fmt,
        "message": spec.get("message", ""),
        "done": bool(spec.get("done")),
        "cancelled": bool(spec.get("cancelled")),
    }


# ---------------------------------------------------------------------------
# Inbox Reading & Checking (IMAP)
# ---------------------------------------------------------------------------

def _decode_header_str(raw):
    """Safely decode MIME-encoded headers (UTF-8, ISO-8859-1, etc.)."""
    if not raw:
        return ""
    try:
        parts = decode_header(raw)
        res = []
        for part, enc in parts:
            if isinstance(part, bytes):
                res.append(part.decode(enc or "utf-8", errors="replace"))
            else:
                res.append(str(part))
        return "".join(res).strip()
    except Exception:
        return str(raw).strip()


def _clean_html_text(html_text):
    """Convert raw HTML markup to clean, concise plain text."""
    if not html_text:
        return ""
    clean = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html_text, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r"</?(?:p|div|br|tr|h[1-6])[^>]*>", "\n", clean, flags=re.IGNORECASE)
    clean = re.sub(r"<[^>]+>", " ", clean)
    clean = html.unescape(clean)
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    return " ".join(lines)


def _extract_body(msg, max_chars=350):
    """Extract and clean the plain-text body or cleaned HTML from an email message."""
    text_parts = []
    html_parts = []

    try:
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                disp = str(part.get("Content-Disposition") or "")
                if "attachment" in disp:
                    continue
                if ctype == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        text_parts.append(payload.decode(charset, errors="replace"))
                elif ctype == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        html_parts.append(payload.decode(charset, errors="replace"))
        else:
            ctype = msg.get_content_type()
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                decoded = payload.decode(charset, errors="replace")
                if ctype == "text/html":
                    html_parts.append(decoded)
                else:
                    text_parts.append(decoded)

        body = ""
        if text_parts:
            body = "\n".join(text_parts).strip()
        elif html_parts:
            body = _clean_html_text("\n".join(html_parts))

        body = re.sub(r"\s+", " ", body).strip()
        if len(body) > max_chars:
            return body[:max_chars].rstrip() + "..."
        return body
    except Exception as e:
        print(f"[email_notify] body extraction error: {e}")
        return ""


def check_inbox(filter_type="unread", limit=3, sender=None, lang="en"):
    """Connect to IMAP and inspect the inbox.

    Returns a dict with:
      - 'ok': bool
      - 'total_unread': int
      - 'messages': list of dicts [{'from', 'from_name', 'subject', 'date', 'snippet'}]
      - 'summary_spoken': formatted natural speech string for Ron
    """
    imap_cfg = _imap_config()
    if not imap_cfg["app_password"]:
        return {
            "ok": False,
            "error": "IMAP app_password not configured in config.json",
            "summary_spoken": "My email password is not configured in config.json, Sir." if lang != "bn" else "স্যার, config.json ফাইলে ইমেইল পাসওয়ার্ড কনফিগার করা নেই।",
            "total_unread": 0,
            "messages": [],
        }

    mail = None
    try:
        mail = imaplib.IMAP4_SSL(imap_cfg["host"], imap_cfg["port"], timeout=15)
        mail.login(imap_cfg["user"], imap_cfg["app_password"])
        status, counts = mail.select("INBOX", readonly=True)
        if status != "OK":
            return {
                "ok": False,
                "error": f"Failed to select INBOX: {status}",
                "summary_spoken": "I could not open the inbox, Sir." if lang != "bn" else "স্যার, ইনবক্স খুলতে সমস্যা হচ্ছে।",
                "total_unread": 0,
                "messages": [],
            }

        # Count total unread
        status, unseen_data = mail.search(None, "UNSEEN")
        unseen_ids = unseen_data[0].split() if status == "OK" and unseen_data[0] else []
        total_unread = len(unseen_ids)

        # Decide which messages to fetch
        ids_to_fetch = []
        if sender:
            # Match sender via IMAP search
            status, s_data = mail.search(None, f'(FROM "{sender}")')
            s_ids = s_data[0].split() if status == "OK" and s_data[0] else []
            ids_to_fetch = s_ids[-limit:]
        elif filter_type == "unread" and unseen_ids:
            ids_to_fetch = unseen_ids[-limit:]
        else:
            status, all_data = mail.search(None, "ALL")
            all_ids = all_data[0].split() if status == "OK" and all_data[0] else []
            ids_to_fetch = all_ids[-limit:]

        messages = []
        for msg_id in reversed(ids_to_fetch):
            status, data = mail.fetch(msg_id, "(RFC822)")
            if status != "OK" or not data or not data[0]:
                continue
            raw_email = data[0][1]
            parsed = email.message_from_bytes(raw_email)

            raw_from = parsed.get("From") or "Unknown"
            from_decoded = _decode_header_str(raw_from)
            from_name, from_addr = email.utils.parseaddr(from_decoded)
            display_sender = from_name if from_name else (from_addr or from_decoded)

            subject_decoded = _decode_header_str(parsed.get("Subject") or "(No Subject)")
            date_str = parsed.get("Date") or ""
            body_snippet = _extract_body(parsed, max_chars=220)
            full_body = _extract_body(parsed, max_chars=4000)

            messages.append({
                "from": from_decoded,
                "from_name": display_sender,
                "from_addr": from_addr,
                "subject": subject_decoded,
                "date": date_str,
                "snippet": body_snippet,
                "body": full_body or body_snippet,
            })

        # Build natural spoken summary
        if lang == "bn":
            if total_unread > 0:
                top = messages[0] if messages else None
                highlight = f" সর্বশেষ ইমেইলটি এসেছে {top['from_name']} এর কাছ থেকে, বিষয়: '{top['subject']}'।" if top else ""
                summary = f"স্যার, আপনার {total_unread}টি অপঠিত ইমেইল রয়েছে।{highlight}"
            elif messages:
                top = messages[0]
                summary = f"স্যার, আপনার কোনো নতুন অপঠিত ইমেইল নেই। সর্বশেষ ইমেইলটি এসেছে {top['from_name']} এর কাছ থেকে, বিষয়: '{top['subject']}'।"
            else:
                summary = "স্যার, আপনার ইনবক্স সম্পূর্ণ পরিষ্কার। কোনো ইমেইল পাওয়া যায়নি।"
        else:
            if total_unread > 0:
                top = messages[0] if messages else None
                highlight = f" The latest is from {top['from_name']} regarding '{top['subject']}'." if top else ""
                summary = f"You have {total_unread} unread email{'s' if total_unread != 1 else ''}, Sir.{highlight}"
            elif messages:
                top = messages[0]
                summary = f"Your inbox has no unread emails, Sir. The latest message was from {top['from_name']} regarding '{top['subject']}'."
            else:
                summary = "Your inbox is empty, Sir."

        return {
            "ok": True,
            "total_unread": total_unread,
            "messages": messages,
            "summary_spoken": summary,
        }

    except Exception as e:
        print(f"[email_notify] check_inbox error: {e}")
        err_spoken = f"I encountered an error checking your emails, Sir: {e}" if lang != "bn" else f"স্যার, ইমেইল চেক করার সময় একটি ত্রুটি হয়েছে: {e}"
        return {
            "ok": False,
            "error": str(e),
            "summary_spoken": err_spoken,
            "total_unread": 0,
            "messages": [],
        }
    finally:
        if mail:
            try:
                mail.close()
                mail.logout()
            except Exception:
                pass


def read_latest_email(lang="en"):
    """Fetch and read the newest email's content aloud."""
    inbox = check_inbox(filter_type="all", limit=1, lang=lang)
    if not inbox.get("ok") or not inbox.get("messages"):
        return inbox.get("summary_spoken") or ("No emails found to read, Sir." if lang != "bn" else "স্যার, পড়ার মতো কোনো ইমেইল পাওয়া যায়নি।")

    msg = inbox["messages"][0]
    sender = msg["from_name"]
    subject = msg["subject"]
    body = msg.get("body") or msg.get("snippet") or ("No plain text content." if lang != "bn" else "কোনো বার্তা নেই।")

    if lang == "bn":
        return f"সর্বশেষ ইমেইলটি এসেছে {sender} এর কাছ থেকে। বিষয়: '{subject}'। বার্তাটি হলো: {body}"
    return f"Latest email from {sender}, subject: '{subject}'. Here is what it says: {body}"


def reply_to_latest_email(reply_body: str, lang="en", target_msg: dict = None) -> dict:
    """Find the latest email sender and send a reply message to them.

    Accepts an optional `target_msg` dict to avoid re-fetching via IMAP.
    Returns a dict with {"ok": bool, "message": str, "recipient": str, "subject": str}.
    """
    reply_text = (reply_body or "").strip()
    if not reply_text:
        err = "I need a message to reply with, Sir." if lang != "bn" else "স্যার, রিপ্লাই দেওয়ার জন্য কোনো বার্তা পাইনি।"
        return {"ok": False, "message": err}

    msg = target_msg
    if not msg:
        # Fetch latest email to determine sender and subject
        inbox = check_inbox(filter_type="all", limit=1, lang=lang)
        if not inbox.get("ok") or not inbox.get("messages"):
            err = "Could not find any recent email to reply to, Sir." if lang != "bn" else "স্যার, রিপ্লাই করার জন্য কোনো সাম্প্রতিক ইমেইল পাওয়া যায়নি।"
            return {"ok": False, "message": err}
        msg = inbox["messages"][0]

    to_addr = msg.get("from_addr") or ""
    to_name = msg.get("from_name") or to_addr
    subject = msg.get("subject") or "Message"

    if not to_addr:
        err = f"Could not determine the email address for {to_name}, Sir." if lang != "bn" else f"স্যার, {to_name}-এর ইমেইল ঠিকানা খুঁজে পাওয়া যায়নি।"
        return {"ok": False, "message": err}

    # Threading subject convention
    if not subject.lower().startswith("re:"):
        reply_subject = f"Re: {subject}"
    else:
        reply_subject = subject

    # Send the email
    ok = send_email(to=to_addr, subject=reply_subject, body=reply_text, recipient_name=to_name)
    if ok:
        clean_inline = " ".join(reply_text.split())
        first_sentence = re.split(r"[।.]", clean_inline)[0].strip()
        if len(first_sentence) > 90:
            first_sentence = first_sentence[:87] + "..."
        if not first_sentence:
            first_sentence = clean_inline[:90]

        if lang == "bn":
            spoken = f"আমি {to_name}-কে আপনার পেশাদার উত্তরটি পাঠিয়ে দিয়েছি, স্যার: '{first_sentence}'।"
        else:
            spoken = f"I have sent your reply to {to_name}, Sir: '{first_sentence}'."
        return {
            "ok": True,
            "message": spoken,
            "recipient": to_name,
            "recipient_addr": to_addr,
            "subject": reply_subject,
        }
    else:
        if lang == "bn":
            spoken = f"স্যার, {to_name}-কে রিপ্লাই পাঠানোর সময় একটি সমস্যা হয়েছে।"
        else:
            spoken = f"Failed to send the reply to {to_name}, Sir. Please check SMTP settings."
        return {"ok": False, "message": spoken}

