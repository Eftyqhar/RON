"""Internet speed test for RON -- download, upload and ping.

No API key, no signup, no new dependency -- `urllib.request` from the standard
library, hitting Cloudflare's public speed-test endpoints. Two rules, inherited
from `weather.py` and `history.py`:

1. **Nothing here ever raises into the caller.** A dead network, a DNS failure,
   a rate limit, a timeout: all of it comes back as `ok=False` with a plain
   apology. A speed test is a nicety; it must never take RON down.
2. **Nothing happens at import time.** The network is touched only when `run()`
   is called, so the module can be imported lazily and tests can point the
   endpoints at a stub.

Everything outside this file talks to the result dict built by `run()`, never
to the raw endpoints. The single HTTP seam is `_fetch()`, which tests replace.

    python netspeed.py                 # run a test, print the spoken answers
    python netspeed.py --probe         # raw JSON for the default endpoints
"""

import os
import sys
import threading
import time
import urllib.request

# -- configuration -----------------------------------------------------------

# Cloudflare's public speed-test endpoints. They deliberately do not require an
# API key and return known byte counts, which makes them ideal for timing.
DOWNLOAD_URL = "https://speed.cloudflare.com/__down?bytes={n}"
UPLOAD_URL = "https://speed.cloudflare.com/__up"
# A tiny, fast host for the ping measurement.
PING_URL = "https://1.1.1.1"

# How many bytes to transfer per phase. Kept modest so a test finishes inside
# ~10-25 s even on a slow link; the HUD fills the wait.
DOWNLOAD_BYTES = 5_000_000       # 5 MB down
UPLOAD_BYTES = 2_000_000         # 2 MB up
PING_SAMPLES = 4

_HTTP_TIMEOUT = 12.0
_MAX_BODY = 64 * 1024 * 1024     # bounded read for the download phase
_UA = "RON-assistant/1.0 (+https://github.com/Eftyqhar/RON)"

# ponytail: stdlib-only, so the ceiling is one process per test and a single
# thread per phase. Good enough for a home-box assistant; a real speedtest-cli
# belongs in requirements only if the user asks for multi-stream precision.


# -- HTTP seam ---------------------------------------------------------------
# One place to replace in tests. Everything below swallows its own faults so a
# caller that swaps this out for a stub never sees an exception either.

def _fetch(method, url, data=None, size=None):
    """Return the number of bytes read/written, or None on any fault."""
    try:
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("User-Agent", _UA)
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            if data is not None:
                # Upload: the body is already supplied; count what we handed in.
                return len(data)
            # Download: read up to `size` bytes (or the cap) so a slow link is
            # not stuck here forever.
            limit = size if size is not None else _MAX_BODY
            n = 0
            while n < limit:
                chunk = resp.read(min(65536, limit - n))
                if not chunk:
                    break
                n += len(chunk)
            return n if n > 0 else None
    except Exception:
        return None


def _ping_once(url):
    """One ping sample: round-trip seconds, or None."""
    start = time.monotonic()
    try:
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", _UA)
        with urllib.request.urlopen(req, timeout=4.0):
            return time.monotonic() - start
    except Exception:
        return None


# -- the test ----------------------------------------------------------------

def run(progress=None, download_url=None, upload_url=None, ping_url=None):
    """Run a speed test. Returns a flat, JSON-safe dict; never raises.

    `progress`, if given, is called as ``progress(phase, done, total)`` where
    `phase` is "ping" | "download" | "upload". The caller (not this module)
    decides how to throttle and forward it to `bus.netspeed()`.

    The three endpoints are injectable so a test can run against a stub server
    without touching the network.
    """
    download_url = download_url or DOWNLOAD_URL
    upload_url = upload_url or UPLOAD_URL
    ping_url = ping_url or PING_URL

    result = {
        "ok": True,
        "ping_ms": None,
        "download_mbps": None,
        "upload_mbps": None,
        "elapsed": 0.0,
        "error": None,
    }
    started = time.time()

    # -- ping ----------------------------------------------------------------
    if progress:
        progress("ping", 0, PING_SAMPLES)
    samples = []
    for i in range(PING_SAMPLES):
        s = _ping_once(ping_url)
        if s is not None:
            samples.append(s)
        if progress:
            progress("ping", i + 1, PING_SAMPLES)

    if samples:
        # Median is more honest than mean when one sample hit a GC pause.
        samples.sort()
        result["ping_ms"] = round(samples[len(samples) // 2] * 1000, 1)

    # -- download ------------------------------------------------------------
    url = download_url.format(n=DOWNLOAD_BYTES)
    if progress:
        progress("download", 0, DOWNLOAD_BYTES)
    t0 = time.monotonic()
    got = _fetch("GET", url, size=DOWNLOAD_BYTES)
    dt = time.monotonic() - t0
    if progress:
        progress("download", got or 0, DOWNLOAD_BYTES)

    if got and dt > 0:
        result["download_mbps"] = round(got * 8 / dt / 1_000_000, 2)

    # -- upload --------------------------------------------------------------
    # A repeatable payload -- the endpoints echo the byte count, they do not
    # inspect the body.
    payload = b"0" * UPLOAD_BYTES
    if progress:
        progress("upload", 0, UPLOAD_BYTES)
    t0 = time.monotonic()
    sent = _fetch("POST", upload_url, data=payload)
    ut = time.monotonic() - t0
    if progress:
        progress("upload", sent or 0, UPLOAD_BYTES)

    if sent and ut > 0:
        result["upload_mbps"] = round(sent * 8 / ut / 1_000_000, 2)

    # If nothing came back from any phase, there is no point pretending.
    if result["ping_ms"] is None and result["download_mbps"] is None:
        result["ok"] = False
        result["error"] = "no response from any endpoint -- check your connection"

    result["elapsed"] = round(time.time() - started, 3)
    return result


# -- spoken answers ----------------------------------------------------------

def _mbps(v):
    return v if isinstance(v, (int, float)) and v is not None else None


def describe(result):
    """The spoken sentence for a finished test. Plain, never raises."""
    if not result or not result.get("ok"):
        return "I could not complete the speed test, Sir. Please check your connection and try again."

    d = _mbps(result.get("download_mbps"))
    u = _mbps(result.get("upload_mbps"))
    p = _mbps(result.get("ping_ms"))

    if d is None and u is None:
        return "The test returned no usable numbers, Sir. Your connection may be down."

    parts = []
    if d is not None:
        parts.append(f"download is {d} megabits per second")
    if u is not None:
        parts.append(f"upload is {u} megabits per second")
    if p is not None:
        parts.append(f"ping is {p} milliseconds")

    lead = "Your internet speed, Sir: " if len(parts) > 1 else ""
    body = ", while ".join(parts)
    tag = ""
    if d is not None:
        tag = " -- that is " + ("excellent" if d >= 100 else "decent" if d >= 25 else "on the slower side")
    return f"{lead}{body}.{tag}, Sir."


# -- HUD payload -------------------------------------------------------------

def hud_payload(result):
    """Flat, JSON-serialisable dict for `bus.netspeed(**...)` and the overlay."""
    if not result or not result.get("ok"):
        return {"ok": False, "status": "error",
                "error": (result or {}).get("error") or "speed test failed"}
    return {
        "ok": True,
        "status": "done",
        "ping_ms": result.get("ping_ms"),
        "download_mbps": result.get("download_mbps"),
        "upload_mbps": result.get("upload_mbps"),
        "elapsed": result.get("elapsed", 0.0),
    }


def reset_cache():
    """No persistent cache to clear -- present for parity with the other leaves."""
    return None


# -- command line ------------------------------------------------------------

def main(argv):
    if argv and argv[0] == "--probe":
        # Raw dump: the UI equivalent of `curl`-ing the endpoint.
        import json
        print("[probing endpoints ...]")
        res = run()
        print(json.dumps(res, indent=2))
        return 0 if res.get("ok") else 1

    print("[running speed test ... this usually takes 10-25 seconds]\n")

    def show(phase, done, total):
        pct = int(100 * done / total) if total else 0
        sys.stderr.write(f"\r  {phase:<10} {pct:>3}%")
        sys.stderr.flush()

    result = run(progress=show)
    sys.stderr.write("\r" + " " * 40 + "\r")

    print(describe(result))
    print(f'\nping {result.get("ping_ms")} ms · '
          f'download {result.get("download_mbps")} Mbps · '
          f'upload {result.get("upload_mbps")} Mbps · '
          f'{result.get("elapsed")}s\n')
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
