"""Event bus: one-way channel from RON's pipeline to any attached UI.

The voice loop, the LLM turn and the tool layer all publish here. The HUD server
(`ui_server.py`) subscribes and forwards to the browser over SSE.

Two rules keep this safe to call from the existing CLI:

1. Publishing with nobody attached is a no-op. `main.py` and `voice.py` can call
   into this unconditionally, so running `python main.py` behaves exactly as it
   did before the UI existed.
2. Nothing here ever raises into the caller. A UI bug must not take down the
   assistant, so every publish path is wrapped.

A rolling snapshot is kept so a browser that connects late (or reloads) is
immediately caught up instead of staring at an empty HUD until the next event.
"""

import queue
import threading
import time

# States the HUD knows how to render. Anything else falls back to IDLE.
IDLE = "idle"
LISTENING = "listening"
THINKING = "thinking"
SPEAKING = "speaking"
EXECUTING = "executing"
ERROR = "error"
OFFLINE = "offline"

_MAX_TRANSCRIPT = 12
_MAX_ACTIVITY = 40
# Bounded so a browser tab that stops reading (backgrounded, suspended, or a
# half-dead socket) cannot grow its queue until the process runs out of memory.
_QUEUE_LIMIT = 256

_lock = threading.Lock()
_subscribers = []
_seq = 0

_snapshot = {
    "state": OFFLINE,
    "detail": "",
    "transcript": [],
    "activity": [],
    "metrics": {},
    "meta": {},
}


def _emit(kind, payload):
    """Fan out one event. Never raises."""
    global _seq
    try:
        with _lock:
            _seq += 1
            event = {"kind": kind, "seq": _seq, "t": time.time()}
            event.update(payload)
            dead = []
            for q in _subscribers:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    # A subscriber this far behind is not going to catch up.
                    dead.append(q)
            for q in dead:
                _subscribers.remove(q)
    except Exception:
        pass


def subscribe():
    """Register a listener. Returns the queue its events will arrive on."""
    q = queue.Queue(maxsize=_QUEUE_LIMIT)
    with _lock:
        _subscribers.append(q)
    return q


def unsubscribe(q):
    with _lock:
        if q in _subscribers:
            _subscribers.remove(q)


def has_listeners():
    with _lock:
        return bool(_subscribers)


def snapshot():
    """A copy of current state, for a client that just connected."""
    with _lock:
        return {
            "kind": "snapshot",
            "seq": _seq,
            "t": time.time(),
            "state": _snapshot["state"],
            "detail": _snapshot["detail"],
            "transcript": list(_snapshot["transcript"]),
            "activity": list(_snapshot["activity"]),
            "metrics": dict(_snapshot["metrics"]),
            "meta": dict(_snapshot["meta"]),
        }


def set_state(state, detail="", amplitude=None):
    """Move the HUD to a new state.

    `detail` is the small line under the state label (the recognised phrase, the
    tool being run, the error text). `amplitude` is an optional 0..1 hint used by
    the core's pulse while speaking.
    """
    with _lock:
        _snapshot["state"] = state
        _snapshot["detail"] = detail or ""
    payload = {"state": state, "detail": detail or ""}
    if amplitude is not None:
        payload["amplitude"] = amplitude
    _emit("state", payload)


def get_state():
    with _lock:
        return _snapshot["state"]


def activity(text, status="ok"):
    """Append a line to the activity feed. status: ok | pending | fail | info."""
    entry = {"text": str(text), "status": status, "t": time.time()}
    with _lock:
        _snapshot["activity"].append(entry)
        del _snapshot["activity"][:-_MAX_ACTIVITY]
    _emit("activity", {"entry": entry})


def transcript(role, text):
    """Record a conversation turn. role: user | ron."""
    entry = {"role": role, "text": str(text), "t": time.time()}
    with _lock:
        _snapshot["transcript"].append(entry)
        del _snapshot["transcript"][:-_MAX_TRANSCRIPT]
    _emit("transcript", {"entry": entry})


def metrics(**values):
    """Publish system telemetry (cpu, ram, gpu, disk, net...)."""
    with _lock:
        _snapshot["metrics"].update(values)
    _emit("metrics", {"metrics": values})


def level(value):
    """Publish an instantaneous 0..1 amplitude for the waveform.

    Deliberately kept out of the snapshot: it arrives many times a second and is
    meaningless a moment later, so a reconnecting client should not replay it.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return
    _emit("level", {"level": 0.0 if v < 0 else (1.0 if v > 1 else v)})


def meta(**values):
    """Publish slow-moving facts: model name, mic device, module health."""
    with _lock:
        _snapshot["meta"].update(values)
    _emit("meta", {"meta": values})
