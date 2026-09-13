"""Timer engine for RON.

Runs a countdown on a background thread and publishes progress so the HUD can
render a live timer. The only job of this module is the timing and the answer
shaping; `main.py` owns the trigger phrases and the bus publishing.

Usage::

    result = run(120)          # two minutes
    print(describe(result))    # "Timer set for 2 minutes."

    # What the HUD sees:
    print(run.hud_payload(result))

A timer has three states the HUD distinguishes through `status`:

* ``"set"``     -- accepted, countdown armed (the moment it starts).
* ``"running"`` -- tick update; `remaining_sec` shrinks toward zero.
* ``"done"``    -- elapsed; the assistant speaks and the overlay alerts.
* ``"cancelled"`` -- the user stopped it before it elapsed.
* ``"error"``   -- something went wrong.

Nothing here raises into the caller and nothing happens at import time.
"""

import threading
import time

from bus import timer as _bus_timer

# ---------------------------------------------------------------------------
# Sound
# ---------------------------------------------------------------------------

# Try the Windows-only stdlib first; fall back to the cross-platform ``beep``
# CLI so the module stays importable on non-Windows hosts.
try:
    import winsound as _winsound
except ImportError:  # pragma: no cover -- non-Windows
    _winsound = None

import subprocess as _subprocess


def _ring():
    """Play a short alarm so the user hears the timer even away from the screen.

    On Windows this uses ``winsound.Beep`` — a rising four-tone sequence.
    Elsewhere it falls back to the POSIX ``beep`` utility if available.
    Any failure is swallowed so a missing speaker can never break the timer.
    """
    if _winsound is not None:
        try:
            # Rising four-beep alarm: 600 → 900 Hz, 200 ms each, 80 ms apart.
            for freq, dur in ((600, 200), (700, 200), (800, 200), (900, 300)):
                _winsound.Beep(freq, dur)
                _winsound.Beep(400, 80)
            return
        except Exception:
            pass
    # POSIX fallback.
    try:
        _subprocess.call(["beep", "-f", "800", "-l", "400"],
                         stdout=_subprocess.DEVNULL, stderr=_subprocess.DEVNULL)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run(duration_sec):
    """Start a countdown of `duration_sec` seconds on a daemon thread.

    Returns a spec dict immediately so the caller can publish the "set" frame
    and keep the handle for cancellation. The thread publishes `running` ticks
    once a second and a final `done` frame when the countdown reaches zero.

    A fault in the background loop is swallowed and surfaced as an `error`
    frame rather than being allowed to escape the thread.
    """
    duration_sec = max(0, int(duration_sec or 0))
    t0 = time.time()
    spec = {
        "duration_sec": duration_sec,
        "remaining_sec": duration_sec,
        "elapsed_sec": 0,
        "status": "set",
        "ok": True,
        "cancelled": False,
        "done": False,
        "_t0": t0,
        "_thread": None,
        "_stop": threading.Event(),
    }

    def _tick():
        try:
            while not spec["_stop"].wait(1.0):
                if spec["cancelled"]:
                    return
                elapsed = time.time() - t0
                remaining = max(0, duration_sec - int(elapsed))
                spec["remaining_sec"] = remaining
                spec["elapsed_sec"] = int(elapsed)
                spec["status"] = "running"
                _bus_timer(**hud_payload(spec))
                if remaining <= 0:
                    break
            if not spec["cancelled"]:
                spec["done"] = True
                spec["remaining_sec"] = 0
                spec["elapsed_sec"] = duration_sec
                spec["status"] = "done"
                _bus_timer(**hud_payload(spec))
                _ring()
        except Exception:
            try:
                _bus_timer(status="error", ok=False,
                           error="timer fault", duration_sec=duration_sec,
                           remaining_sec=0, elapsed_sec=0,
                           cancelled=False, done=False)
            except Exception:
                pass

    spec["_thread"] = threading.Thread(target=_tick, daemon=True)
    # Publish the "set" frame synchronously so the HUD opens the moment the
    # timer is armed, before the first one-second tick arrives.
    _bus_timer(**hud_payload(spec))
    spec["_thread"].start()
    return spec


def cancel(spec):
    """Stop a running timer started by `run()`. Idempotent; None-safe."""
    if not spec:
        return
    spec["cancelled"] = True
    spec["done"] = False
    spec["status"] = "cancelled"
    spec["remaining_sec"] = 0
    try:
        spec["_stop"].set()
    except Exception:
        pass
    try:
        _bus_timer(**hud_payload(spec))
    except Exception:
        pass


def describe(spec):
    """Spoken answer for a timer request.

    `spec` may be the live dict returned by `run()` (status "set"/"running")
    or a terminal snapshot (status "done"/"cancelled"/"error").
    """
    if not spec:
        return "I could not set the timer, Sir."
    status = spec.get("status", "")
    dur = int(spec.get("duration_sec", 0))
    if status in ("set", "running"):
        return f"Timer set for {_format_duration(dur)}."
    if status == "done":
        return f"Your {_format_duration(dur)} timer is up, Sir."
    if status == "cancelled":
        return f"Timer for {_format_duration(dur)} cancelled."
    # error
    return "I ran into a problem setting the timer, Sir."


def hud_payload(spec):
    """Flat dict the HUD renders. Tolerant of None and of missing keys."""
    if not spec:
        return {
            "status": "error", "ok": False, "error": "no timer",
            "duration_sec": 0, "remaining_sec": 0, "elapsed_sec": 0,
            "duration_fmt": "0s", "remaining_fmt": "0s",
            "cancelled": False, "done": False,
        }
    dur = int(spec.get("duration_sec") or 0)
    rem = max(0, int(spec.get("remaining_sec") or 0))
    ela = int(spec.get("elapsed_sec") or 0)
    status = spec.get("status", "error")
    ok = bool(spec.get("ok", status != "error"))
    return {
        "status": status,
        "ok": ok,
        "error": spec.get("error") if status == "error" else None,
        "duration_sec": dur,
        "remaining_sec": rem,
        "elapsed_sec": ela,
        "duration_fmt": _format_duration(dur),
        "remaining_fmt": _format_duration(rem),
        "cancelled": bool(spec.get("cancelled")),
        "done": bool(spec.get("done")),
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _format_duration(total_sec):
    """Human label for a number of seconds: '45s', '2m', '1h 30m'."""
    total_sec = max(0, int(total_sec or 0))
    if total_sec < 60:
        return f"{total_sec}s"
    h = total_sec // 3600
    m = (total_sec % 3600) // 60
    s = total_sec % 60
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s:
        parts.append(f"{s}s")
    return " ".join(parts) or "0s"
