"""Offline check of the durable chat history in `history.py`.

    python test_history.py

Points `RON_DB` at a scratch file, then drives the real `bus` publish paths and
reads the database back. Needs no microphone, no API key and no network, and
never touches the real `history.db`.
"""

import os
import sqlite3
import sys
import tempfile
import threading

# Set before anything imports `history` transitively -- `bus` does, at import
# time. The database itself is opened lazily on first write, so this would work
# either way, but the ordering is the guarantee and not an accident.
_TMP = tempfile.mkdtemp(prefix="ron-history-test-")
os.environ["RON_DB"] = os.path.join(_TMP, "history.db")

import bus
import history

FAILURES = []


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f"  -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def rows(sql, *params):
    """Read through a separate connection, the way an outside tool would."""
    con = sqlite3.connect(history.db_path(), timeout=5.0)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def main():
    print("RON chat history check\n")

    # -- 1. lazy initialisation ----------------------------------------------
    print("1. initialisation")
    check("path honours RON_DB", history.db_path() == os.environ["RON_DB"],
          history.db_path())
    check("nothing on disk before the first write",
          not os.path.exists(history.db_path()))

    q = bus.subscribe()
    bus.transcript("user", "open notepad")
    check("file created on first write", os.path.exists(history.db_path()))
    check("schema version stamped",
          rows("PRAGMA user_version")[0][0] == history.SCHEMA_VERSION)
    check("WAL enabled", rows("PRAGMA journal_mode")[0][0].lower() == "wal",
          rows("PRAGMA journal_mode"))
    sessions, turns, events = history.counts()
    check("one session row", sessions == 1, sessions)
    check("session has a start time",
          rows("SELECT started_at FROM sessions")[0][0] > 0)

    # -- 2. the bus hooks -----------------------------------------------------
    print("\n2. bus hooks")
    check("turn stored", rows("SELECT role, text FROM turns") == [("user", "open notepad")],
          rows("SELECT role, text FROM turns"))

    ev = [e for e in _drain(q) if e["kind"] == "transcript"]
    check("emitted entry carries its row id",
          len(ev) == 1 and isinstance(ev[0]["entry"].get("id"), int), ev)
    check("id matches the stored row",
          ev and ev[0]["entry"].get("id") == rows("SELECT id FROM turns")[0][0])

    bus.activity("Tool call: open_app", "pending")
    bus.meta(model="gpt-4o-mini-test")
    stored = rows("SELECT text, status FROM events")
    check("activity stored", ("Tool call: open_app", "pending") in stored, stored)
    check("model stamped on the session",
          rows("SELECT model FROM sessions")[0][0] == "gpt-4o-mini-test",
          rows("SELECT model FROM sessions"))
    check("activity and turns are counted separately",
          history.counts()[1:] == (1, 1), history.counts())

    # -- 3. reading back ------------------------------------------------------
    print("\n3. reading back")
    bus.transcript("ron", "Opening Notepad, Sir.")
    for i in range(6):
        bus.transcript("user", f"question {i}")
        bus.transcript("ron", f"answer {i}")

    recent = history.recent_turns(4)
    check("recent_turns is capped", len(recent) == 4, recent)
    check("recent_turns is chronological, newest last",
          recent[-1] == ("ron", "answer 5"), recent)
    check("recent_turns takes the newest window",
          recent[0] == ("user", "question 4"), recent)
    check("recent_turns of an over-large limit returns everything",
          len(history.recent_turns(500)) == 14, len(history.recent_turns(500)))

    # -- 4. paging ------------------------------------------------------------
    print("\n4. paging")
    page, more = history.page_turns(5)
    ids = [t["id"] for t in page]
    check("page is the newest 5", len(page) == 5, len(page))
    check("page is chronological", ids == sorted(ids), ids)
    check("more reported when older turns exist", more is True, more)
    check("page rows are complete",
          all(set(t) == {"id", "role", "text", "t"} for t in page), page[:1])

    older, more2 = history.page_turns(5, before=ids[0])
    older_ids = [t["id"] for t in older]
    check("second page does not overlap the first",
          not set(older_ids) & set(ids), (older_ids, ids))
    check("second page is immediately older",
          max(older_ids) == ids[0] - 1, (older_ids, ids))
    check("more still reported", more2 is True, more2)

    # Walk to the very start and confirm `more` turns off exactly once.
    seen, cursor, guard = [], None, 0
    while guard < 20:
        guard += 1
        page, more = history.page_turns(4, cursor)
        seen = [t["id"] for t in page] + seen
        if not more:
            break
        cursor = page[0]["id"]
    check("paging reaches the beginning", more is False, more)
    check("paging visits every turn exactly once",
          seen == sorted(set(seen)) and len(seen) == 14, len(seen))
    check("empty page beyond the start",
          history.page_turns(5, before=1) == ([], False),
          history.page_turns(5, before=1))

    # -- 5. concurrency -------------------------------------------------------
    # The connection is shared across threads (check_same_thread=False), which is
    # only safe because of the module lock. This is the check that catches a
    # regression there: sqlite3 would raise ProgrammingError, which history
    # swallows into a None return, so the row count is what gives it away.
    print("\n5. concurrent writers")
    before = history.counts()[1]
    got = []

    def hammer(n):
        for i in range(20):
            got.append(history.record_turn("user", f"t{n}-{i}"))

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    check("every concurrent write returned an id",
          len(got) == 120 and all(isinstance(r, int) for r in got),
          f"{sum(1 for r in got if r is None)} of {len(got)} failed")
    check("every concurrent row landed", history.counts()[1] == before + 120,
          history.counts()[1])
    check("no ids were reused", len(set(got)) == 120, len(set(got)))

    # -- 6. shutdown ----------------------------------------------------------
    print("\n6. shutdown")
    history.close()
    check("session stamped as ended",
          rows("SELECT ended_at FROM sessions")[0][0] is not None)
    history.close()          # must be safe twice
    check("closing twice is harmless", True)
    bus.transcript("user", "after close")
    check("a write after close reopens rather than failing",
          rows("SELECT COUNT(*) FROM turns")[0][0] == before + 121,
          rows("SELECT COUNT(*) FROM turns"))
    check("the reopen started a new session",
          rows("SELECT COUNT(*) FROM sessions")[0][0] == 2,
          rows("SELECT COUNT(*) FROM sessions"))
    bus.unsubscribe(q)

    # -- 7. replay into the LLM context ---------------------------------------
    # Imported here, not at the top: main.py seeds its context at import time, so
    # it has to see the database already populated above.
    print("\n7. context replay")
    import voice

    class DummyVoice:
        def Speak(self, text):
            pass

    voice._tls.voice = DummyVoice()      # keep the synthesiser silent
    # A 'ron' turn immediately before the import, so the replay window really
    # contains one -- everything above it is a 'user' row from the concurrency
    # block, and the role mapping would otherwise go untested.
    bus.transcript("ron", "Very good, Sir.")
    import main as ron

    seeded = ron.conversation_history
    check("system prompt kept at index 0",
          seeded[0]["role"] == "system" and "Ron" in seeded[0]["content"],
          seeded[0]["role"])
    check("recent turns replayed", len(seeded) == ron.REPLAY_TURNS + 1,
          len(seeded))
    check("'ron' is mapped to 'assistant'",
          seeded[-1] == {"role": "assistant", "content": "Very good, Sir."},
          seeded[-1])
    check("no unmapped role reaches the API",
          all(m["role"] in ("system", "user", "assistant") for m in seeded),
          sorted({m["role"] for m in seeded}))
    check("'user' is passed through",
          any(m["role"] == "user" for m in seeded[1:]),
          sorted({m["role"] for m in seeded[1:]}))
    check("replay is in chronological order",
          [m["content"] for m in seeded[-3:]] ==
          [t[1] for t in history.recent_turns(3)],
          [m["content"] for m in seeded[-3:]])

    # -- 8. context trim ------------------------------------------------------
    print("\n8. context trim")
    ron.conversation_history[:] = (
        [{"role": "system", "content": "keep me"}]
        + [{"role": "user", "content": str(i)} for i in range(200)])
    ron._trim_context()
    check("context is capped",
          len(ron.conversation_history) == ron.MAX_CONTEXT_TURNS + 1,
          len(ron.conversation_history))
    check("system prompt survives the trim",
          ron.conversation_history[0]["content"] == "keep me",
          ron.conversation_history[0])
    check("the oldest turns are the ones dropped",
          ron.conversation_history[1]["content"] == "160",
          ron.conversation_history[1])
    ron._trim_context()
    check("trimming an already-short context is a no-op",
          len(ron.conversation_history) == ron.MAX_CONTEXT_TURNS + 1,
          len(ron.conversation_history))

    # -- 9. resilience --------------------------------------------------------
    # The invariant that matters most: a storage fault must not take RON down.
    print("\n9. storage fault")
    history.close()
    _reset()
    os.environ["RON_DB"] = os.path.join(_TMP, "history.db", "nope", "x.db")
    q = bus.subscribe()
    bus.transcript("user", "still works")     # must not raise
    ev = [e for e in _drain(q) if e["kind"] == "transcript"]
    check("the bus still emits with storage broken",
          len(ev) == 1 and ev[0]["entry"]["text"] == "still works", ev)
    check("the entry simply carries no id", ev and "id" not in ev[0]["entry"], ev)
    check("history reports itself disabled", history._disabled is True)
    check("record_turn returns None", history.record_turn("user", "x") is None)
    check("reads degrade to empty", history.recent_turns(5) == []
          and history.page_turns(5) == ([], False))
    bus.activity("tool ran anyway", "ok")     # must not raise either
    bus.meta(model="whatever")
    check("activity and meta survive it too", True)
    bus.unsubscribe(q)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("All checks passed.")
    print(f"(scratch database left in {_TMP})")
    return 0


def _drain(q):
    import queue
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def _reset():
    """Forget the open database so the next call re-resolves RON_DB."""
    history._conn = None
    history._session_id = None
    history._disabled = False
    history._path = None


if __name__ == "__main__":
    sys.exit(main())
