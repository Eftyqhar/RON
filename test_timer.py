"""Offline check of the timer engine.

    python test_timer.py

Everything below replaces the bus publisher with a stub, so no events leave the
machine. Covers:

* a short countdown runs to completion and publishes `done`;
* `cancel()` stops a running timer and publishes `cancelled`;
* `describe()` phrasing for set, running, done, cancelled and error;
* `hud_payload()` shape and its `status`/`ok` flags for each terminal state;
* the command understanding in `main.extract_timer` -- phrases that must route
  to the timer and phrases that must NOT (music, speed test, etc.).
"""

import sys
import time

FAILURES = []


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f"  -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


# Stub the bus publisher before importing the module under test, so the
# background thread's ticks are captured instead of fanned out over SSE.
_seen = []


def _stub_bus_timer(**values):
    _seen.append(dict(values))


import bus            # noqa: E402
bus.timer = _stub_bus_timer

import timer as T    # noqa: E402


# -- 1. a short countdown runs to completion ---------------------------------

def test_passing():
    print("1. short countdown reaches done")
    _seen.clear()
    spec = T.run(3)
    check("initial status is 'set'", spec["status"] == "set", spec)
    check("duration recorded", spec["duration_sec"] == 3, spec)
    check("not done yet", not spec["done"], spec)

    # Wait for the thread to tick through and publish the terminal frame.
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if spec["done"] or spec["cancelled"]:
            break
        time.sleep(0.2)

    check("terminal status is 'done'", spec["status"] == "done", spec)
    check("remaining reached 0", spec["remaining_sec"] == 0, spec)
    check("done flag set", spec["done"] is True, spec)

    statuses = {s.get("status") for s in _seen}
    check("progression covers set, running, done",
          {"set", "running", "done"}.issubset(statuses), statuses)


# -- 2. cancellation ---------------------------------------------------------

def test_cancel():
    print("\n2. cancellation")
    _seen.clear()
    spec = T.run(60)
    time.sleep(0.3)
    T.cancel(spec)
    time.sleep(0.3)

    check("status becomes 'cancelled'", spec["status"] == "cancelled", spec)
    check("done cleared", spec["done"] is False, spec)
    check("cancelled flag set", spec["cancelled"] is True, spec)
    check("a cancelled frame was published",
          any(s.get("status") == "cancelled" for s in _seen), _seen)


# -- 3. describe() phrasing --------------------------------------------------

def test_describe():
    print("\n3. spoken answers")
    set_spec = {"status": "set", "duration_sec": 120}
    check("set -> names the duration",
          "2m" in T.describe(set_spec) or "2 minutes" in T.describe(set_spec),
          T.describe(set_spec))

    run_spec = {"status": "running", "duration_sec": 45}
    check("running -> names the duration",
          "45s" in T.describe(run_spec) or "45 seconds" in T.describe(run_spec),
          T.describe(run_spec))

    done_spec = {"status": "done", "duration_sec": 30}
    check("done -> says it is up",
          "up" in T.describe(done_spec).lower() or "timer" in T.describe(done_spec).lower(),
          T.describe(done_spec))

    cancel_spec = {"status": "cancelled", "duration_sec": 300}
    check("cancelled -> says cancelled",
          "cancelled" in T.describe(cancel_spec).lower(),
          T.describe(cancel_spec))

    check("None input apologises",
          "could not" in T.describe(None).lower(),
          T.describe(None))

    err_spec = {"status": "error", "duration_sec": 0}
    check("error -> problem",
          "problem" in T.describe(err_spec).lower(),
          T.describe(err_spec))


# -- 4. hud_payload() --------------------------------------------------------

def test_hud_payload():
    print("\n4. HUD payload")
    running = {"status": "running", "ok": True, "duration_sec": 120,
               "remaining_sec": 90, "elapsed_sec": 30,
               "cancelled": False, "done": False}
    p = T.hud_payload(running)
    check("running -> status running, ok True",
          p["status"] == "running" and p["ok"] is True, p)
    check("remaining_fmt present", p["remaining_fmt"] == "1m 30s", p)
    check("duration_fmt present", p["duration_fmt"] == "2m", p)
    check("keys are flat and present",
          all(k in p for k in ("duration_sec", "remaining_sec", "elapsed_sec",
                               "duration_fmt", "remaining_fmt", "cancelled", "done")),
          p)

    done = {"status": "done", "ok": True, "duration_sec": 30,
            "remaining_sec": 0, "elapsed_sec": 30, "cancelled": False, "done": True}
    pd = T.hud_payload(done)
    check("done -> status done", pd["status"] == "done" and pd["done"] is True, pd)

    cancelled = {"status": "cancelled", "ok": True, "duration_sec": 60,
                 "remaining_sec": 0, "elapsed_sec": 5, "cancelled": True, "done": False}
    pc = T.hud_payload(cancelled)
    check("cancelled -> status cancelled", pc["status"] == "cancelled", pc)

    pnone = T.hud_payload(None)
    check("None -> status error, ok False",
          pnone["status"] == "error" and not pnone["ok"], pnone)


# -- 5. extract_timer truth table --------------------------------------------

def test_extract_timer():
    print("\n5. command understanding")
    import main as ron   # heavy import; kept late so the earlier checks run first

    positives = [
        ("set a timer for 2 min", 120),
        ("set a timer for 2 minutes", 120),
        ("set a timer for 30 seconds", 30),
        ("set a timer for 1 hour", 3600),
        ("countdown 5 minutes", 300),
        ("start a 45 second timer", 45),
        ("set a 3 hour countdown", 10800),
        ("remind me in 10 minutes", 600),
        ("alarm for 2 hrs", 7200),
        ("set timer 15m", 900),
        ("ron set a timer for 2 min", 120),
    ]
    for cmd, expected in positives:
        got = ron.extract_timer(cmd)
        check(f"routes: {cmd!r}",
              got == {"duration_sec": expected},
              f"got {got}, expected {{'duration_sec': {expected}}}")

    negatives = [
        "play believer",               # music verb
        "check my internet speed",     # speed test
        "what's the weather",          # weather
        "find my python projects",     # disk search
        "open google",                 # website
        "set the table",               # timer word absent, no duration
        "speed of light",              # physics
        "hello ron",                   # conversation
        "set a timer for",             # no duration
        "how fast can you run",        # no timer
    ]
    for cmd in negatives:
        check(f"ignores: {cmd!r}", ron.extract_timer(cmd) is None,
              f"got {ron.extract_timer(cmd)}")


# -- 6. zero / edge durations ------------------------------------------------

def test_zero_duration():
    print("\n6. edge durations")
    _seen.clear()
    spec = T.run(0)
    # The thread is started as a daemon; give it a moment to execute the loop.
    deadline = time.time() + 5.0
    while time.time() < deadline and spec["status"] != "done":
        time.sleep(0.1)
    check("zero-duration goes straight to done", spec["status"] == "done", spec)
    check("remaining is 0", spec["remaining_sec"] == 0, spec)


def test_cancel_none_safe():
    print("\n7. None-safe cancel")
    try:
        T.cancel(None)
        check("cancel(None) does not raise", True)
    except Exception as e:
        check("cancel(None) does not raise", False, str(e))


def main(argv):
    del argv
    print("RON timer engine\n")
    test_passing()
    test_cancel()
    test_describe()
    test_hud_payload()
    test_extract_timer()
    test_zero_duration()
    test_cancel_none_safe()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
