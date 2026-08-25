"""Durable chat history in SQLite.

`bus` publishes every exchange here, so what RON heard, said and did outlives the
process. Two things consume it:

  * `main.py` replays the most recent turns into the LLM context at startup, so
    RON carries a conversation across a restart.
  * the HUD pages backwards through `/api/history` to show earlier sessions.

Standard library only. `sqlite3` ships with Python, and this project already
declines dependencies it does not need (see `ui_server.py`). Nothing here imports
from RON either, so there is no cycle with `bus`.

Two rules, inherited from `bus`, make this safe to sit on the voice path:

1. Nothing here ever raises into the caller. A storage fault must not take the
   assistant down with it, so a failed open latches `_disabled` and every later
   call becomes a silent no-op.
2. Nothing here happens at import time. The database is opened on first use, so
   `RON_DB` can be pointed somewhere harmless *after* this module is imported --
   which is how the tests avoid writing into real history.
"""

import os
import socket
import sqlite3
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DB = os.path.join(_HERE, "history.db")

SCHEMA_VERSION = 1

# `turns.id` is an INTEGER PRIMARY KEY, i.e. the rowid, so it is already indexed
# -- the HUD's "WHERE id < ?" paging needs no index of its own. The session
# indexes are for the per-session queries a human runs against the file.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY,
    started_at REAL NOT NULL,
    ended_at   REAL,
    model      TEXT,
    host       TEXT
);
CREATE TABLE IF NOT EXISTS turns (
    id         INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL,
    role       TEXT NOT NULL,
    text       TEXT NOT NULL,
    t          REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL,
    text       TEXT NOT NULL,
    status     TEXT NOT NULL,
    t          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_session  ON turns(session_id);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
"""

_lock = threading.Lock()
_conn = None
_session_id = None
_disabled = False
_path = None


def db_path():
    """The database file in use, or the one that would be opened."""
    return _path or os.environ.get("RON_DB") or _DEFAULT_DB


def _ensure():
    """Open the database and start a session row. Caller must hold `_lock`.

    Returns True when the connection is usable.
    """
    global _conn, _session_id, _disabled, _path
    if _disabled:
        return False
    if _conn is not None:
        return True
    try:
        _path = os.environ.get("RON_DB") or _DEFAULT_DB
        if _path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(_path)), exist_ok=True)

        # check_same_thread=False because this single connection is shared across
        # threads: `bus` is reached from the main thread (console mode), the voice
        # loop, and a *fresh* thread per HUD command. `_lock` is what makes that
        # safe -- and one shared connection beats opening a new one per command
        # and never closing it. timeout covers the WAL writer lock.
        _conn = sqlite3.connect(_path, check_same_thread=False, timeout=5.0)
        # WAL so the /api/history reader never blocks behind a write.
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.executescript(_SCHEMA)
        _conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

        # Created lazily rather than at startup, so a one-shot
        # `python main.py "..."` gets its own session with no wiring, and a run
        # that never speaks leaves no empty row behind.
        cur = _conn.execute(
            "INSERT INTO sessions (started_at, host) VALUES (?, ?)",
            (time.time(), socket.gethostname().upper()))
        _session_id = cur.lastrowid
        _conn.commit()
        return True
    except Exception as e:
        # Latched: a directory we cannot write to will not become writable, and
        # retrying on every single turn would print this forever.
        _disabled = True
        _conn = None
        print(f"[History disabled -- chat will not be saved: {e}]")
        return False


# ------------------------------------------------------------------- writing --

def record_turn(role, text, t=None):
    """Store one conversation turn. Returns its row id, or None if unavailable.

    The id is the point: `bus` attaches it to the event it emits so the HUD knows
    where to page backwards from. A per-call failure returns None but does *not*
    disable history -- a busy-timeout is transient, unlike a bad path.
    """
    try:
        with _lock:
            if not _ensure():
                return None
            cur = _conn.execute(
                "INSERT INTO turns (session_id, role, text, t) VALUES (?, ?, ?, ?)",
                (_session_id, str(role), str(text),
                 float(t if t is not None else time.time())))
            _conn.commit()
            return cur.lastrowid
    except Exception:
        return None


def record_event(text, status="ok", t=None):
    """Store one activity line -- a tool call, a failure, a status change."""
    try:
        with _lock:
            if not _ensure():
                return None
            cur = _conn.execute(
                "INSERT INTO events (session_id, text, status, t) VALUES (?, ?, ?, ?)",
                (_session_id, str(text), str(status),
                 float(t if t is not None else time.time())))
            _conn.commit()
            return cur.lastrowid
    except Exception:
        return None


def note_model(model):
    """Record which model answered in this session."""
    try:
        with _lock:
            if not _ensure():
                return
            _conn.execute("UPDATE sessions SET model = ? WHERE id = ?",
                          (str(model), _session_id))
            _conn.commit()
    except Exception:
        pass


# ------------------------------------------------------------------- reading --

def recent_turns(limit=20):
    """The last `limit` turns, oldest first, as (role, text) pairs.

    Ordered DESC by the database and reversed here: taking the *newest* N and
    then putting them back in chronological order is what the LLM context wants.
    """
    try:
        with _lock:
            if not _ensure():
                return []
            rows = _conn.execute(
                "SELECT role, text FROM turns ORDER BY id DESC LIMIT ?",
                (int(limit),)).fetchall()
        return [(r[0], r[1]) for r in reversed(rows)]
    except Exception:
        return []


def page_turns(limit=100, before=None):
    """One page of turns for the HUD, oldest first.

    `before` is an exclusive upper bound on id, so the client walks backwards
    through history by passing the oldest id it currently holds. Returns
    (turns, more) where `more` reports whether older turns exist beyond the page
    -- found by asking for one extra row rather than a second COUNT query.
    """
    try:
        limit = max(1, int(limit))
        with _lock:
            if not _ensure():
                return [], False
            if before:
                rows = _conn.execute(
                    "SELECT id, role, text, t FROM turns WHERE id < ? "
                    "ORDER BY id DESC LIMIT ?", (int(before), limit + 1)).fetchall()
            else:
                rows = _conn.execute(
                    "SELECT id, role, text, t FROM turns "
                    "ORDER BY id DESC LIMIT ?", (limit + 1,)).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        return ([{"id": r[0], "role": r[1], "text": r[2], "t": r[3]}
                 for r in reversed(rows)], more)
    except Exception:
        return [], False


def counts():
    """(sessions, turns, events) row counts, for diagnostics and the tests."""
    try:
        with _lock:
            if not _ensure():
                return (0, 0, 0)
            return tuple(
                _conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("sessions", "turns", "events"))
    except Exception:
        return (0, 0, 0)


# ------------------------------------------------------------------ shutdown --

def close():
    """Stamp the session as ended and let the connection go.

    Safe to call more than once, and safe to call before anything was ever
    written. If the process is killed instead, `ended_at` simply stays NULL,
    which is the honest record of what happened.
    """
    global _conn, _session_id
    try:
        with _lock:
            if _conn is None:
                return
            try:
                _conn.execute(
                    "UPDATE sessions SET ended_at = ? "
                    "WHERE id = ? AND ended_at IS NULL",
                    (time.time(), _session_id))
                _conn.commit()
            finally:
                _conn.close()
                _conn = None
                _session_id = None
    except Exception:
        pass
