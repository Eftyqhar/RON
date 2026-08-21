"""Offline check of the HUD event pipeline.

    python test_hud.py

Runs RON's dispatcher with the speech synthesiser and the LLM stubbed out, then
asserts that the HUD gets the state machine, transcript and activity feed it
expects. Needs no microphone, no API key, and no network -- and deliberately
never reaches a real tool, so nothing is launched on your desktop.
"""

import json
import queue
import sys

import bus
import voice


class DummyVoice:
    """Stands in for SAPI.SpVoice so the test stays silent."""
    def __init__(self):
        self.said = []

    def Speak(self, text):
        self.said.append(text)


class StubMessage:
    def __init__(self, content):
        self.content = content


class StubChoice:
    def __init__(self, content):
        self.message = StubMessage(content)
        self.finish_reason = "stop"


class StubResponse:
    def __init__(self, content, model="stub-model"):
        self.choices = [StubChoice(content)]
        self.model = model


class StubClient:
    """Replaces the OpenAI client. `script` is what the model 'replies'."""
    def __init__(self):
        self.script = ""
        self.raises = None

        outer = self

        class Completions:
            def create(self, **kw):
                if outer.raises:
                    raise outer.raises
                return StubResponse(outer.script)

        class Chat:
            completions = Completions()

        self.chat = Chat()


def drain(q):
    events = []
    while True:
        try:
            events.append(q.get_nowait())
        except queue.Empty:
            return events


def states(events):
    return [e["state"] for e in events if e["kind"] == "state"]


def activities(events):
    return [(e["entry"]["status"], e["entry"]["text"]) for e in events if e["kind"] == "activity"]


def turns(events):
    return [(e["entry"]["role"], e["entry"]["text"]) for e in events if e["kind"] == "transcript"]


FAILURES = []


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f"  -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def main():
    print("RON HUD pipeline check\n")

    # Silence the synthesiser before main.py binds its reference to speak().
    dummy = DummyVoice()
    voice._tls.voice = dummy

    import main as ron
    stub = StubClient()
    ron.client = stub

    q = bus.subscribe()

    # -- 1. tool call ---------------------------------------------------------
    print("1. tool-call turn")
    drain(q)
    # An unrecognised tool exercises the full EXECUTING path without launching
    # anything real.
    stub.script = json.dumps({"tool": "diagnostic_probe", "arg": "x"})
    ron.process_command("run a diagnostic probe")
    ev = drain(q)

    check("user turn recorded", ("user", "run a diagnostic probe") in turns(ev))
    check("spoken reply recorded", any(r == "ron" for r, _ in turns(ev)))
    check("thinking then executing", states(ev)[:2] == ["thinking", "executing"],
          states(ev))
    check("settles back to idle", states(ev)[-1] == "idle", states(ev))
    acts = [t for _, t in activities(ev)]
    check("command logged", "Command received" in acts, acts)
    check("response logged", "AI response generated" in acts, acts)
    check("tool call logged", any(a.startswith("Tool call:") for a in acts), acts)
    check("nothing audible escaped", dummy.said and "Unknown tool" in dummy.said[-1],
          dummy.said)

    # -- 2. conversational turn ----------------------------------------------
    print("\n2. conversational turn")
    drain(q)
    stub.script = "Good evening, Sir."
    ron.process_command("how are you")
    ev = drain(q)
    check("no executing state", "executing" not in states(ev), states(ev))
    check("reply reaches transcript", ("ron", "Good evening, Sir.") in turns(ev), turns(ev))
    check("ends idle", states(ev)[-1] == "idle", states(ev))

    # -- 3. API failure -------------------------------------------------------
    print("\n3. API failure")
    drain(q)
    stub.raises = RuntimeError("Invalid token: check your key")
    ron.process_command("say something")
    ev = drain(q)
    stub.raises = None
    check("enters error state", "error" in states(ev), states(ev))
    check("error survives the spoken explanation", states(ev)[-1] == "error", states(ev))
    check("failure logged", any(s == "fail" for s, _ in activities(ev)), activities(ev))
    check("api marked down",
          any(e["kind"] == "meta" and e["meta"].get("api_ok") is False for e in ev))

    # -- 4. audio level maths -------------------------------------------------
    print("\n4. audio level")
    bus.set_state(bus.IDLE)
    check("silence reads zero", voice._rms(b"\x00\x00" * 512) == 0.0)
    loud = voice._rms(b"\x00\x40" * 512)          # 16384 == half scale
    check("loud audio clamps to 1.0", loud == 1.0, loud)
    quiet = voice._rms(b"\x10\x01" * 512)         # 272
    check("quiet audio is a small fraction", 0 < quiet < 0.2, quiet)
    check("odd-length buffer is safe", voice._rms(b"\x01") == 0.0)

    class FakeStream:
        def __init__(self):
            self.closed = False

        def read(self, n, **kw):
            return b"\x00\x20" * n

        def close(self):
            self.closed = True

    inner = FakeStream()
    tap = voice._LevelTap(inner)
    check("tap returns audio untouched", tap.read(64) == inner.read(64))
    tap.close()
    check("tap proxies attributes", inner.closed)

    # -- 5. snapshot shape ----------------------------------------------------
    print("\n5. snapshot")
    snap = bus.snapshot()
    for key in ("state", "detail", "transcript", "activity", "metrics", "meta"):
        check(f"snapshot has {key}", key in snap)
    check("snapshot is JSON-serialisable", bool(json.dumps(snap)))

    bus.unsubscribe(q)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
