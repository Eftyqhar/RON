"""Event bus: one-way channel from RON's pipeline to the UI and to storage.

The voice loop, the LLM turn and the tool layer all publish here. Two things
consume it: the HUD server (`ui_server.py`) subscribes and forwards to the
browser over SSE, and `history` writes the durable record to SQLite.

Two rules keep this safe to call from the existing CLI:

1. Publishing with no UI attached fans out to nobody. `main.py` and `voice.py`
   can call into this unconditionally, so running `python main.py` behaves
   exactly as it did before the UI existed. Note that publishing is no longer
   free of side effects: conversation turns and activity lines are persisted
   whether or not a browser is watching.
2. Nothing here ever raises into the caller. A UI bug or a storage fault must not
   take down the assistant, so every publish path is wrapped.

A rolling snapshot is kept so a browser that connects late (or reloads) is
immediately caught up instead of staring at an empty HUD until the next event.
"""

import queue
import threading
import time

import history

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
    "weather": {},
    "search": {},
    "netspeed": {},
    "timer": {},
    "volume": {},
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
            "weather": dict(_snapshot["weather"]),
            "search": dict(_snapshot["search"]),
            "netspeed": dict(_snapshot["netspeed"]),
            "timer": dict(_snapshot["timer"]),
            "volume": dict(_snapshot["volume"]),
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
    # After the fan-out, unlike transcript(): the HUD never pages the activity
    # feed, so it has no use for the row id and should not wait on the disk.
    history.record_event(entry["text"], status, entry["t"])


def transcript(role, text):
    """Record a conversation turn. role: user | ron.

    This is the choke point for durable history -- every exchange reaches it,
    including the folder and website commands that `main.py` routes directly
    without ever consulting the LLM.
    """
    text = str(text)
    t = time.time()
    # Written *before* the fan-out so the row id can travel with the event: the
    # HUD pages backwards from the oldest id it holds, and an entry with no id
    # would leave it unable to ask for anything older. One WAL insert, taken
    # outside `_lock` so the SSE fan-out never queues behind the disk.
    rowid = history.record_turn(role, text, t)
    entry = {"role": role, "text": text, "t": t}
    if rowid is not None:
        entry["id"] = rowid
    with _lock:
        _snapshot["transcript"].append(entry)
        del _snapshot["transcript"][:-_MAX_TRANSCRIPT]
    _emit("transcript", {"entry": entry})


def metrics(**values):
    """Publish system telemetry (cpu, ram, gpu, disk, net...)."""
    with _lock:
        _snapshot["metrics"].update(values)
    _emit("metrics", {"metrics": values})


def weather(**values):
    """Publish the current weather reading for the HUD's panel 05.

    Ephemeral telemetry, like `metrics()`: it lands in the snapshot so a browser
    connecting late sees a populated panel, but it is not written to `history.db`.
    The spoken answer is persisted anyway, for free, because `speak()` goes
    through `transcript()`.

    Replaces rather than merges. A failed poll publishes `ok=False` with nothing
    else, and merging would leave last hour's temperature sitting beside it.
    """
    with _lock:
        _snapshot["weather"] = dict(values)
    _emit("weather", {"weather": values})


def search(**values):
    """Publish the state of a disk search for the HUD's cinematic overlay.

    Ephemeral telemetry like `weather()`: it lands in the snapshot so a browser
    connecting mid-scan is caught up, but it is never written to `history.db`.
    The spoken result is persisted for free through `speak()` -> `transcript()`.

    A search publishes twice -- `status="scanning"` the moment it starts, then
    the finished payload (`done` | `empty` | `error`). Replaces rather than
    merges, so the scanning frame's counters never bleed into the result.
    """
    with _lock:
        _snapshot["search"] = dict(values)
    _emit("search", {"search": values})


def netspeed(**values):
    """Publish the state of an internet speed test for the HUD's overlay.

    Same contract as `search()`: ephemeral, snapshot-cached, replaces rather
    than merges so a running test's progress never bleeds into the result.
    """
    with _lock:
        _snapshot["netspeed"] = dict(values)
    _emit("netspeed", {"netspeed": values})


def timer(**values):
    """Publish the state of a countdown timer for the HUD's overlay.

    Same contract as `search()` and `netspeed()`: ephemeral, snapshot-cached,
    replaces rather than merges so a running countdown never bleeds into the
    terminal `done`/`cancelled` frame.
    """
    with _lock:
        _snapshot["timer"] = dict(values)
    _emit("timer", {"timer": values})


def volume(**values):
    """Publish the system master-volume state for the HUD's overlay.

    Same contract as `search()` and `timer()`: ephemeral, snapshot-cached,
    replaces rather than merges so a mute change never bleeds into a later
    absolute-level frame.
    """
    with _lock:
        _snapshot["volume"] = dict(values)
    _emit("volume", {"volume": values})



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
    if "model" in values:
        history.note_model(values["model"])
