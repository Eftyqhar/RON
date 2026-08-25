"""RON HUD server -- serves the holographic interface and bridges it to RON.

    python ui_server.py

Runs three things in one process:

  * a small HTTP server for the interface in `ui/`
  * an SSE stream (`/events`) carrying live state from `bus`
  * RON's own voice loop, on a background thread

Deliberately built on the standard library alone. A voice assistant that has to
survive `pip install` drift on a Windows box does not need a web framework to
push a few JSON objects at localhost. `psutil` is the one optional extra: with it
the system monitor shows real telemetry, without it those readouts show N/A and
everything else works.
"""

import argparse
import json
import mimetypes
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bus
import history

try:
    import psutil
except ImportError:
    psutil = None

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")
DEFAULT_PORT = 8765
WEATHER_INTERVAL = 600      # seconds between forecast refreshes
_START = time.time()

# Imported lazily in serve() so `--help` and a missing microphone cannot stop the
# server from at least explaining itself.
ron = None


# ------------------------------------------------------------------ telemetry --

def _drive_root():
    """The drive the project lives on -- what DISK% should actually report."""
    drive = os.path.splitdrive(os.path.abspath(__file__))[0]
    return (drive + os.sep) if drive else os.sep


def _nvidia_gpu():
    """GPU utilisation via nvidia-smi, or None when there is no NVIDIA GPU."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4,
            # Without this a console window flashes over the fullscreen HUD
            # every few seconds.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if out.returncode == 0:
            first = out.stdout.strip().splitlines()[0]
            return float(first.strip())
    except Exception:
        pass
    return None


def _online():
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=1.5):
            return True
    except OSError:
        return False


def telemetry_loop(stop: threading.Event):
    """Publish system telemetry for the left-hand panel."""
    root = _drive_root()
    gpu_supported = True
    gpu_value = None
    net_prev = None
    tick = 0

    if psutil:
        psutil.cpu_percent(interval=None)  # prime the delta
    else:
        bus.activity("psutil not installed - system monitor limited", "info")

    while not stop.is_set():
        payload = {"uptime": int(time.time() - _START)}

        if psutil:
            try:
                payload["cpu"] = psutil.cpu_percent(interval=None)
                payload["ram"] = psutil.virtual_memory().percent
                payload["disk"] = psutil.disk_usage(root).percent
                counters = psutil.net_io_counters()
                now = time.monotonic()
                if net_prev:
                    span = max(0.001, now - net_prev[0])
                    payload["net_down"] = max(0.0, (counters.bytes_recv - net_prev[1]) / span / 1024)
                    payload["net_up"] = max(0.0, (counters.bytes_sent - net_prev[2]) / span / 1024)
                net_prev = (now, counters.bytes_recv, counters.bytes_sent)
            except Exception:
                pass

        # nvidia-smi costs a process spawn, and connectivity costs a socket, so
        # neither runs at the 1.2s cadence of the cheap counters.
        if tick % 4 == 0:
            if gpu_supported:
                gpu_value = _nvidia_gpu()
                if gpu_value is None:
                    gpu_supported = False  # no NVIDIA GPU here; stop paying for it
            payload["gpu"] = gpu_value
            payload["disk_root"] = root

        if tick % 5 == 0:
            online = _online()
            bus.meta(network_ok=online)
            payload["online"] = online

        bus.metrics(**payload)
        tick += 1
        stop.wait(1.2)


def weather_loop(stop: threading.Event):
    """Keep panel 05 current.

    Publishes once immediately so the panel is populated before a browser has
    finished loading, then refreshes on `WEATHER_INTERVAL`. A failed fetch
    publishes `ok=False` rather than leaving last hour's numbers on screen
    pretending to be live -- `weather.py` already serves a stale reading when it
    has one, so reaching this branch means there is genuinely nothing to show.
    """
    import weather                    # local: keeps the module optional at import

    failures = 0
    while not stop.is_set():
        payload = weather.hud_payload()
        bus.weather(**payload)

        if payload.get("ok"):
            if failures:
                bus.activity("Weather service recovered", "ok")
            failures = 0
            bus.meta(weather_ok=True)
        else:
            failures += 1
            bus.meta(weather_ok=False)
            # One line per outage, not one per tick: a machine left offline
            # overnight would otherwise fill the activity feed with the same
            # sentence a hundred times.
            if failures == 1:
                bus.activity(payload.get("error") or "Weather unavailable", "info")

        # Retry sooner after a failure, but back off so a long outage is not a
        # request every thirty seconds for hours.
        stop.wait(WEATHER_INTERVAL if not failures
                  else min(WEATHER_INTERVAL, 30 * 2 ** min(failures, 4)))


def probe_hardware():
    """One-off startup checks so the module panel starts out honest."""
    import voice

    try:
        voices = voice._voice().GetVoices()
        bus.meta(audio_ok=voices.Count > 0,
                 tts_voice=voices.Item(0).GetDescription() if voices.Count else "NONE")
    except Exception as e:
        bus.meta(audio_ok=False, tts_voice=f"ERROR: {e}")

    try:
        import speech_recognition as sr
        names = sr.Microphone.list_microphone_names()
        ok = 0 <= voice.MIC_INDEX < len(names)
        bus.meta(mic_ok=ok,
                 mic_device=voice.mic_name() if ok else "NOT FOUND",
                 mic_index=voice.MIC_INDEX,
                 mic_count=len(names))
    except Exception as e:
        bus.meta(mic_ok=False, mic_device=f"ERROR: {e}")

    bus.meta(tools_ok=True, host=socket.gethostname().upper())


# --------------------------------------------------------------------- server --

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "RON-HUD"

    def log_message(self, fmt, *args):
        pass  # the console belongs to RON, not to an access log

    # -- helpers --
    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 64 * 1024:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:
            return {}

    # -- routes --
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/events":
            return self._events()
        if path == "/api/state":
            return self._json(200, bus.snapshot())
        if path == "/api/history":
            return self._history()
        return self._static(path)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/command":
            return self._command()
        if path == "/api/control":
            return self._control()
        if path == "/api/open":
            return self._open()
        return self._json(404, {"error": "not found"})

    def _static(self, path):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = os.path.normpath(os.path.join(UI_DIR, rel))
        # Refuse anything that escapes UI_DIR. This server binds to loopback, but
        # a path-traversal hole is not worth leaving open regardless.
        if not target.startswith(UI_DIR + os.sep) and target != UI_DIR:
            return self._send(403, b"forbidden")
        if not os.path.isfile(target):
            return self._send(404, b"not found")
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(target, "rb") as fh:
            self._send(200, fh.read(), ctype)

    def _history(self):
        """Past conversation turns, oldest first, for the HUD's scrollback.

        `before` is the oldest id the client already holds, so walking backwards
        is one indexed query with no offset arithmetic. Junk input falls back to
        the defaults, and the SQL is parameterised regardless.
        """
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

        def _int(name, default):
            try:
                return int(qs.get(name, [""])[0])
            except (TypeError, ValueError):
                return default

        limit = max(1, min(500, _int("limit", 100)))
        before = _int("before", 0) or None
        turns, more = history.page_turns(limit, before)
        return self._json(200, {"turns": turns, "more": more})

    def _events(self):
        """Server-sent events: one long-lived response, one JSON object per line.

        SSE rather than WebSockets on purpose -- the traffic here is entirely
        server-to-client, and this needs no handshake code, no framing, and no
        third-party dependency. `Connection: close` puts the response in
        read-until-close mode, which is what an open-ended stream needs.
        """
        q = bus.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            self._write_event(bus.snapshot())

            last_ping = time.monotonic()
            while not self.server.stopping.is_set():
                try:
                    self._write_event(q.get(timeout=1.0))
                    # Drain whatever else piled up in the same wake-up rather than
                    # flushing once per event.
                    for _ in range(64):
                        self._write_event(q.get_nowait())
                except queue.Empty:
                    pass
                now = time.monotonic()
                if now - last_ping > 10:
                    # Comment frame: keeps proxies and the browser from deciding a
                    # quiet stream is a dead one.
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_ping = now
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass  # tab closed or reloaded
        finally:
            bus.unsubscribe(q)

    def _write_event(self, event):
        self.wfile.write(b"data: " + json.dumps(event).encode("utf-8") + b"\n\n")
        self.wfile.flush()

    def _command(self):
        text = str(self._read_json().get("text") or "").strip()
        if not text:
            return self._json(400, {"error": "empty command"})
        if ron is None:
            return self._json(503, {"error": "assistant not loaded"})

        # listen() lowercases what it hears; typed commands must match so the
        # keyword routing in extract_website/extract_folder behaves identically.
        lowered = text.lower()
        if any(w in lowered for w in ("goodbye", "shut down", "exit", "sleep")):
            threading.Thread(target=_farewell, daemon=True).start()
            return self._json(200, {"ok": True, "shutdown": True})

        # Answer immediately: a PDF turn can run for 90 seconds and the browser
        # must not be holding a request open while the HUD animates.
        threading.Thread(target=ron.process_command, args=(lowered,),
                         daemon=True).start()
        return self._json(202, {"ok": True, "queued": text})

    def _open(self):
        """Reveal a search result on disk.

        SECURITY: this opens any path the browser sends. That is the point of
        the feature, and it is only acceptable because the server binds to
        127.0.0.1 by default -- never expose it beyond loopback without adding
        authentication first.
        """
        path = str(self._read_json().get("path") or "").strip()
        if not path:
            return self._json(400, {"error": "empty path"})
        # Absolute + existing: refuse to guess at relative paths or open
        # something that is not there. A directory opens in Explorer; a file is
        # *selected* in its parent folder rather than launched -- startfile on
        # an arbitrary .exe/.bat would be running whatever the disk search dug up.
        if not os.path.isabs(path) or not os.path.exists(path):
            return self._json(404, {"error": "path not found"})
        try:
            if os.path.isdir(path):
                os.startfile(path)
            else:
                subprocess.Popen(["explorer", "/select,", path],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            return self._json(500, {"error": str(e)})
        return self._json(200, {"ok": True})

    def _control(self):
        action = str(self._read_json().get("action") or "")
        if ron is None:
            return self._json(503, {"error": "assistant not loaded"})

        if action == "mic_on":
            ron.voice_enabled.set()
            bus.activity("Microphone enabled", "ok")
            bus.meta(mic_muted=False)
        elif action == "mic_off":
            ron.voice_enabled.clear()
            bus.activity("Microphone muted", "info")
            bus.meta(mic_muted=True)
            bus.set_state(bus.IDLE, "MICROPHONE MUTED")
        elif action == "shutdown":
            threading.Thread(target=_farewell, daemon=True).start()
        else:
            return self._json(400, {"error": f"unknown action {action!r}"})
        return self._json(200, {"ok": True, "action": action})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.stopping = threading.Event()


_server = None


def _farewell():
    """Speak the sign-off, then bring the whole process down."""
    try:
        ron.shutdown_event.set()
        ron.voice_enabled.clear()
        ron.speak("Going offline, Sir. Goodbye.")
    except Exception:
        pass
    bus.set_state(bus.OFFLINE, "SESSION ENDED")
    time.sleep(0.6)  # let the last SSE frame reach the browser
    if _server is not None:
        _server.stopping.set()
        threading.Thread(target=_server.shutdown, daemon=True).start()


# -------------------------------------------------------------------- browser --

def _chromium_exe():
    candidates = []
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if not base:
            continue
        candidates += [
            os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(base, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
        ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def open_ui(url, mode):
    if mode == "none":
        return
    if mode == "app":
        exe = _chromium_exe()
        if exe:
            try:
                subprocess.Popen([exe, f"--app={url}", "--start-fullscreen",
                                  "--new-window"])
                return
            except Exception as e:
                print(f"[Could not launch {os.path.basename(exe)} in app mode: {e}]")
        else:
            print("[No Chromium browser found; opening in the default browser.]")
    import webbrowser
    webbrowser.open(url)


# ----------------------------------------------------------------------- main --

def _bind(host, port):
    """Take `port`, or the next few if something else already has it."""
    last = None
    for candidate in range(port, port + 12):
        try:
            return Server((host, candidate), Handler)
        except OSError as e:
            last = e
    raise SystemExit(f"Could not bind {host}:{port}-{port + 11}: {last}")


def serve(argv=None):
    global ron, _server

    ap = argparse.ArgumentParser(description="RON holographic HUD")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--browser", choices=("app", "tab", "none"), default="app",
                    help="app: frameless fullscreen Chromium (default)")
    ap.add_argument("--no-voice", action="store_true",
                    help="serve the HUD without starting the microphone loop")
    ap.add_argument("--no-greet", action="store_true",
                    help="skip the spoken 'Ron is online' greeting")
    ap.add_argument("--no-replay", action="store_true",
                    help="start with an empty context instead of recalling "
                         "recent turns from history.db")
    ap.add_argument("--no-weather", action="store_true",
                    help="skip the weather poller (panel 05 shows OFFLINE)")
    ap.add_argument("--location", default="",
                    help='city for the weather panel, e.g. --location "Dhaka"')
    args = ap.parse_args(argv)

    if not os.path.isdir(UI_DIR):
        raise SystemExit(f"Interface files are missing: {UI_DIR}")

    if args.no_replay:
        # Must be set before the import below: main.py seeds its context at
        # module level, so by the time this function could call anything on it
        # the decision has already been made.
        os.environ["RON_REPLAY"] = "0"

    if args.location:
        # Same ordering constraint as --no-replay above: main.py imports weather,
        # and this has to be in the environment before it does.
        os.environ["RON_LOCATION"] = args.location

    print("[Loading RON...]")
    import main as ron_module
    ron = ron_module

    bus.set_state(bus.IDLE, "SYSTEM READY")
    bus.activity("HUD server started", "ok")
    probe_hardware()
    bus.meta(voice_loop=not args.no_voice, mic_muted=args.no_voice)

    _server = _bind(args.host, args.port)
    url = f"http://{args.host}:{_server.server_address[1]}/"

    stop = threading.Event()
    threading.Thread(target=telemetry_loop, args=(stop,), daemon=True).start()

    if args.no_weather:
        bus.weather(ok=False, error="weather poller disabled")
        bus.meta(weather_ok=False)
        print("[Weather poller disabled: panel 05 will show OFFLINE.]")
    else:
        threading.Thread(target=weather_loop, args=(stop,), daemon=True).start()

    if args.no_voice:
        ron.voice_enabled.clear()
        print("[Voice loop disabled: type commands in the HUD command line.]")
    else:
        threading.Thread(target=ron.run_voice_loop,
                         kwargs={"greet": not args.no_greet},
                         daemon=True).start()

    print(f"\n  R.O.N. HUD  ->  {url}\n  Ctrl+C to shut down.\n")
    threading.Thread(target=open_ui, args=(url, args.browser), daemon=True).start()

    # Bring the process down when RON is dismissed by voice ("goodbye"), which
    # sets shutdown_event on the voice thread rather than through /api/control.
    def watch_shutdown():
        ron.shutdown_event.wait()
        time.sleep(0.6)
        _server.stopping.set()
        _server.shutdown()

    threading.Thread(target=watch_shutdown, daemon=True).start()

    try:
        _server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("\n[Interrupted - shutting down.]")
    finally:
        stop.set()
        _server.stopping.set()
        ron.shutdown_event.set()
        bus.set_state(bus.OFFLINE)
        _server.server_close()
        # Closed here rather than in run_voice_loop: this process owns the
        # database, and the voice thread is only one of the things writing to it.
        history.close()
        print("[HUD offline.]")


if __name__ == "__main__":
    sys.exit(serve())
