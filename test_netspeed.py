"""Offline check of the internet speed-test engine.

    python test_netspeed.py
    python test_netspeed.py --probe     # live run against Cloudflare

Everything below the first `run()` call replaces the network seam with a stub,
so no bytes leave the machine and the CI box needs no connectivity. Covers:

* a passing test with injectable endpoints -- download, upload and ping all
  come back as numbers and the elapsed wall clock is bounded;
* a total network failure -- every seam returns None and `run()` surfaces
  `ok=False` with a plain apology rather than raising;
* `describe()` phrasing for the happy path, the partial-result path and the
  failed path;
* `hud_payload()` shape and its `ok`/`status` flags for done vs failed;
* the command understanding in `main.extract_speed` -- phrases that must route
  to the speed test and phrases that must NOT.
"""

import json
import sys
import time

FAILURES = []


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f"  -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


import bus            # noqa: E402  (import-safe; no network touched)
import netspeed       # noqa: E402


# -- 1. a stubbed, fully-offline passing test --------------------------------

def _stub_fetch(method, url, data=None, size=None):
    # The real endpoints echo the byte count they were handed; the stub mirrors
    # that contract so the math inside `run()` is exercised for real.
    if data is not None:
        return len(data)
    return size or netspeed.DOWNLOAD_BYTES


def _stub_ping_once(url):
    return 0.025   # 25 ms round trip


def test_passing():
    print("1. stubbed passing test")
    orig_fetch = netspeed._fetch
    orig_ping = netspeed._ping_once
    netspeed._fetch = _stub_fetch
    netspeed._ping_once = _stub_ping_once
    try:
        t0 = time.time()
        r = netspeed.run(
            download_url="https://stub/__down?bytes={n}",
            upload_url="https://stub/__up",
            ping_url="https://stub/ping",
        )
        elapsed = time.time() - t0

        check("ok on a healthy run", r["ok"] is True, r)
        check("ping lands as ms", r["ping_ms"] == 25.0, r)
        check("download is a positive number", isinstance(r["download_mbps"], (int, float)) and r["download_mbps"] > 0, r)
        check("upload is a positive number", isinstance(r["upload_mbps"], (int, float)) and r["upload_mbps"] > 0, r)
        check("error is None when healthy", r["error"] is None, r)
        check("elapsed is real and bounded", 0 < r["elapsed"] < 5.0, r)
        check("wall clock is bounded (<3s for a stub)", elapsed < 3.0, f"{elapsed:.3f}s")
    finally:
        netspeed._fetch = orig_fetch
        netspeed._ping_once = orig_ping


# -- 2. total network failure --------------------------------------------------

def _dead_fetch(method, url, data=None, size=None):
    return None


def _dead_ping_once(url):
    return None


def test_failure():
    print("\n2. total network failure")
    orig_fetch = netspeed._fetch
    orig_ping = netspeed._ping_once
    netspeed._fetch = _dead_fetch
    netspeed._ping_once = _dead_ping_once
    try:
        r = netspeed.run(
            download_url="https://dead/__down?bytes={n}",
            upload_url="https://dead/__up",
            ping_url="https://dead/ping",
        )
        check("ok is False when nothing answers", r["ok"] is False, r)
        check("error is a plain string", isinstance(r["error"], str) and r["error"], r)
        check("ping is None", r["ping_ms"] is None, r)
        check("download is None", r["download_mbps"] is None, r)
        check("upload is None", r["upload_mbps"] is None, r)
    finally:
        netspeed._fetch = orig_fetch
        netspeed._ping_once = orig_ping


# -- 3. describe() phrasing ----------------------------------------------------

def test_describe():
    print("\n3. spoken answers")
    happy = {"ok": True, "ping_ms": 12.4, "download_mbps": 142.5,
             "upload_mbps": 58.3, "elapsed": 14.2}
    partial = {"ok": True, "ping_ms": None, "download_mbps": 8.1,
               "upload_mbps": None, "elapsed": 6.0}
    dead = {"ok": False, "ping_ms": None, "download_mbps": None,
            "upload_mbps": None, "elapsed": 0.0, "error": "no response"}

    check("happy path names all three",
          all(tok in netspeed.describe(happy)
              for tok in ["142.5", "58.3", "12.4"]),
          netspeed.describe(happy))
    check("happy path with fast download is 'excellent'",
          "excellent" in netspeed.describe(happy), netspeed.describe(happy))
    check("partial still returns a number",
          "8.1" in netspeed.describe(partial), netspeed.describe(partial))
    check("failed gets an apology",
          "problem" in netspeed.describe(dead) or "could not" in netspeed.describe(dead),
          netspeed.describe(dead))
    check("None input apologises",
          "problem" in netspeed.describe(None) or "could not" in netspeed.describe(None),
          netspeed.describe(None))

    slow = {"ok": True, "ping_ms": 200.0, "download_mbps": 5.0,
            "upload_mbps": 1.0, "elapsed": 30.0}
    check("slow link is called 'on the slower side'",
          "slower side" in netspeed.describe(slow), netspeed.describe(slow))


# -- 4. hud_payload() ----------------------------------------------------------

def test_hud_payload():
    print("\n4. HUD payload")
    happy = {"ok": True, "ping_ms": 12.4, "download_mbps": 142.5,
             "upload_mbps": 58.3, "elapsed": 14.2}
    p = netspeed.hud_payload(happy)
    check("status done for a healthy test", p["status"] == "done", p)
    check("ok flag set", p["ok"] is True, p)
    check("keys are flat and present",
          all(k in p for k in ("ping_ms", "download_mbps", "upload_mbps", "elapsed")), p)
    check("payload is JSON-serialisable", bool(json.dumps(p)))

    dead = {"ok": False, "error": "no response"}
    pf = netspeed.hud_payload(dead)
    check("failure -> status error", pf["status"] == "error" and not pf["ok"], pf)

    pnone = netspeed.hud_payload(None)
    check("None input -> status error", pnone["status"] == "error" and not pnone["ok"], pnone)


# -- 5. extract_speed truth table ----------------------------------------------

def test_extract_speed():
    print("\n5. command understanding")
    import main as ron   # heavy import; kept late so the earlier checks run first

    positives = [
        "check my internet speed",
        "what about my internet speed",
        "how fast is my connection",
        "speed test",
        "ping my internet",
        "check my network speed",
        "test my bandwidth",
        "what is my network speed",
        "ron check my internet speed",
        "how fast is my internet",
        "check my connection performance",
        "network connectivity test",
    ]
    for cmd in positives:
        got = ron.extract_speed(cmd)
        check(f"routes: {cmd!r}", got == {"kind": "speed"}, f"got {got}")

    negatives = [
        "play believer",               # music verb
        "open documents",              # folder opener
        "visit facebook",              # website opener
        "what's the weather",          # weather
        "find my python projects",     # disk search
        "search youtube for lofi",     # online veto
        "speed of light",              # physics, not a link
        "hello ron",                   # conversation
        "how fast can you type",       # no internet/connection token
    ]
    for cmd in negatives:
        check(f"ignores: {cmd!r}", ron.extract_speed(cmd) is None,
              f"got {ron.extract_speed(cmd)}")


# -- 6. progress callback (the HUD-driving seam) ------------------------------

def test_progress():
    print("\n6. progress callback")
    seen = []
    orig_fetch = netspeed._fetch
    orig_ping = netspeed._ping_once
    netspeed._fetch = _stub_fetch
    netspeed._ping_once = _stub_ping_once
    try:
        r = netspeed.run(progress=lambda ph, done, total: seen.append((ph, int(done), int(total))),
                         download_url="https://stub/__down?bytes={n}",
                         upload_url="https://stub/__up",
                         ping_url="https://stub/ping")
        check("run still succeeds with a progress hook", r["ok"] is True)
        phases = {s[0] for s in seen}
        check("progress covers all three phases",
              phases == {"ping", "download", "upload"}, phases)
        check("ping progress counts to PING_SAMPLES",
              any(s[0] == "ping" and s[2] == netspeed.PING_SAMPLES for s in seen), seen)
    finally:
        netspeed._fetch = orig_fetch
        netspeed._ping_once = orig_ping


def main(argv):
    if argv and argv[0] == "--probe":
        print("[live probe against Cloudflare -- this may take ~15s]\n")
        import json as _j
        res = netspeed.run()
        print(_j.dumps(res, indent=2))
        print(netspeed.describe(res))
        return 0 if res.get("ok") else 1

    print("RON internet speed check\n")
    test_passing()
    test_failure()
    test_describe()
    test_hud_payload()
    test_extract_speed()
    test_progress()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
