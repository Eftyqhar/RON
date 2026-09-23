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
import coach
import history
import intel
import memory
import researcher
import sfx
import telegram_bridge
import netradar
import docintel
import dhaka_desk

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
_ron_ready = threading.Event()


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
        if path == "/api/memories":
            return self._memories()
        if path == "/api/coach":
            return self._coach_status()
        if path == "/api/intel":
            return self._intel_status()
        if path == "/api/research/status":
            return self._research_status()
        if path == "/api/research/history":
            return self._research_history()
        if path == "/api/research/report":
            return self._research_report()
        if path == "/api/weather/station":
            return self._weather_station()
        if path == "/api/tribune/latest":
            return self._tribune_latest()
        if path == "/api/tribune/generate":
            return self._tribune_generate()
        if path == "/api/tribune/broadcast":
            return self._tribune_broadcast()
        if path == "/api/tribune/pdf":
            return self._tribune_pdf()
        if path == "/api/telegram/status":
            return self._telegram_status()
        if path == "/api/netradar/status":
            return self._netradar_status()
        if path == "/api/docintel/status":
            return self._docintel_status()
        if path == "/api/dhaka/latest":
            return self._dhaka_latest()
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
        if path == "/api/memories/delete":
            return self._delete_memory()
        if path == "/api/memories/add":
            return self._add_memory()
        if path == "/api/coach/goal":
            return self._coach_goal()
        if path == "/api/coach/complete":
            return self._coach_complete()
        if path == "/api/coach/delete":
            return self._coach_delete()
        if path == "/api/coach/blocker":
            return self._coach_blocker()
        if path == "/api/coach/debrief":
            return self._coach_debrief()
        if path == "/api/intel/refresh":
            return self._intel_refresh()
        if path == "/api/research/start":
            return self._research_start()
        if path == "/api/research/open":
            return self._research_open()
        if path == "/api/weather/station":
            return self._weather_station()
        if path == "/api/tribune/latest":
            return self._tribune_latest()
        if path == "/api/tribune/generate":
            return self._tribune_generate()
        if path == "/api/tribune/broadcast":
            return self._tribune_broadcast()
        if path == "/api/tribune/pdf":
            return self._tribune_pdf()
        if path == "/api/netradar/scan":
            return self._netradar_scan()
        if path == "/api/netradar/trust":
            return self._netradar_trust()
        if path == "/api/docintel/upload":
            return self._docintel_upload()
        if path == "/api/docintel/ask":
            return self._docintel_ask()
        if path == "/api/docintel/export":
            return self._docintel_export()
        if path == "/api/diagnostic":
            return self._diagnostic()
        if path == "/api/dhaka/refresh":
            return self._dhaka_refresh()
        if path == "/api/dhaka/broadcast":
            return self._dhaka_broadcast()
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
        sfx.play("hud_hum", debounce_s=8.0)
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
            _ron_ready.wait(timeout=6.0)
        if ron is None:
            return self._json(503, {"error": "assistant still initializing"})

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
            _ron_ready.wait(timeout=6.0)
        if ron is None:
            return self._json(503, {"error": "assistant still initializing"})

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

    def _memories(self):
        """Return all memories, category stats, and knowledge graph for HUD."""
        return self._json(200, {
            "memories": memory.get_all_memories(),
            "stats": memory.get_memory_stats(),
            "graph": memory.get_knowledge_graph()
        })

    def _delete_memory(self):
        """Delete a memory by id."""
        data = self._read_json()
        mem_id = data.get("id")
        if not mem_id:
            return self._json(400, {"error": "Missing memory id"})
        try:
            mem_id = int(mem_id)
        except (ValueError, TypeError):
            return self._json(400, {"error": "Invalid memory id"})
        ok = memory.delete_memory_by_id(mem_id)
        return self._json(200, {"ok": ok})

    def _add_memory(self):
        """Add a memory directly from the HUD UI."""
        data = self._read_json()
        content = str(data.get("content") or "").strip()
        category = str(data.get("category") or "general").strip()
        subject = str(data.get("subject") or "").strip() or None
        if not content:
            return self._json(400, {"error": "Empty memory content"})
        res = memory.remember(content, category=category, subject=subject)
        return self._json(200, res)

    def _coach_status(self):
        """Return current standup goals, metrics, and window focus status."""
        return self._json(200, coach.get_standup_status())

    def _coach_goal(self):
        """Add goals from HUD."""
        data = self._read_json()
        goals = data.get("goals")
        if isinstance(goals, str):
            goals = [goals]
        elif not isinstance(goals, list):
            goals = []
        if not goals:
            text = str(data.get("text") or "").strip()
            if text:
                goals = [text]
        if not goals:
            return self._json(400, {"error": "No goals specified"})
        res = coach.start_standup(goals)
        return self._json(200, res)

    def _coach_complete(self):
        """Toggle goal completion from HUD."""
        data = self._read_json()
        goal_id = str(data.get("id") or "")
        completed = bool(data.get("completed", True))
        if not goal_id:
            return self._json(400, {"error": "Missing goal id"})
        res = coach.mark_goal(goal_id, completed=completed)
        return self._json(200, res)

    def _coach_delete(self):
        """Delete goal from HUD."""
        data = self._read_json()
        try:
            goal_id = int(data.get("id"))
        except (ValueError, TypeError):
            return self._json(400, {"error": "Invalid goal id"})
        ok = coach.delete_goal(goal_id)
        return self._json(200, {"ok": ok})

    def _coach_blocker(self):
        """Toggle distraction blocker from HUD."""
        data = self._read_json()
        enabled = bool(data.get("enabled", True))
        active = coach.set_blocker(enabled)
        return self._json(200, {"ok": True, "blocker_enabled": active})

    def _coach_debrief(self):
        """Trigger evening debrief from HUD."""
        res = coach.evening_debrief()
        return self._json(200, res)

    def _intel_status(self):
        """Return current intel feeds and snapshot."""
        data = intel._last_intel_data or intel.compile_intel_report()
        return self._json(200, data)

    def _intel_refresh(self):
        """Trigger an on-demand intel compilation and broadcast."""
        data = intel.compile_intel_report()
        intel.broadcast_state(event_name="update")
        return self._json(200, {"ok": True, "data": data})

    def _research_status(self):
        """Return current research telemetry snapshot."""
        return self._json(200, researcher.get_current_research_snapshot())

    def _research_history(self):
        """Return list of past research dossiers from history."""
        return self._json(200, {"history": researcher.get_research_history()})

    def _research_report(self):
        """Return full details and markdown for a specific report."""
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        report_id = qs.get("id", [""])[0]
        if not report_id:
            return self._json(400, {"error": "Missing report id"})
        dossier = researcher.get_research_dossier(report_id)
        if not dossier:
            return self._json(404, {"error": "Report not found"})
        return self._json(200, dossier)

    def _research_start(self):
        """Start autonomous deep research from HUD."""
        data = self._read_json()
        topic = str(data.get("topic") or "").strip()
        depth = str(data.get("depth") or "deep").strip()
        lang = str(data.get("lang") or bus.get_language())
        if not topic:
            return self._json(400, {"error": "Missing topic"})
        client = getattr(ron, "client", None)
        model = getattr(ron, "MODEL", "auto")
        res = researcher.start_deep_research(topic, client=client, model=model, lang=lang, depth=depth)
        return self._json(200, res)

    def _research_open(self):
        """Open PDF or research folder from HUD."""
        data = self._read_json()
        action = str(data.get("action") or "pdf").strip().lower()
        target = str(data.get("target") or "").strip()
        if action == "folder":
            res = researcher.open_research_folder()
        else:
            res = researcher.open_research_pdf(target)
        return self._json(200, res)

    def _weather_station(self):
        """Return full structured Weather Station telemetry and 5-day forecast."""
        import weather
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        loc = qs.get("location", [""])[0] or None
        data = weather.get_weather_station_data(loc)
        return self._json(200, data)

    def _tribune_latest(self):
        """Return latest newspaper edition of The RON World Tribune."""
        import tribune
        data = tribune.build_today_tribune(force_refresh=False, generate_pdf_doc=False)
        return self._json(200, data)

    def _tribune_generate(self):
        """Force re-scrape and PDF compilation of today's Tribune."""
        import tribune
        data = tribune.build_today_tribune(force_refresh=True, generate_pdf_doc=True)
        return self._json(200, {"ok": True, "data": data})

    def _tribune_broadcast(self):
        """Trigger 3-minute executive audio broadcast."""
        import tribune
        res = tribune.read_tribune_broadcast(force_refresh=False)
        return self._json(200, res)

    def _tribune_pdf(self):
        """Open or serve the latest newspaper PDF."""
        import tribune
        edition = tribune.build_today_tribune(force_refresh=False, generate_pdf_doc=True)
        pdf_path = edition.get("pdf_path")
        if pdf_path and os.path.exists(pdf_path):
            try:
                if hasattr(os, "startfile"):
                    os.startfile(pdf_path)
                return self._json(200, {"ok": True, "pdf_path": pdf_path})
            except Exception as e:
                return self._json(500, {"error": str(e)})
        return self._json(404, {"error": "PDF not found"})

    def _telegram_status(self):
        """Return live status of Telegram Pocket Uplink."""
        return self._json(200, telegram_bridge.get_status())

    def _netradar_status(self):
        """Return latest Cyber Watchdog network radar snapshot."""
        return self._json(200, netradar.get_latest_radar_snapshot())

    def _netradar_scan(self):
        """Trigger an on-demand network perimeter sweep."""
        data = self._read_json()
        fast = bool(data.get("fast", True))
        threading.Thread(target=netradar.scan_network, args=(fast,), daemon=True).start()
        return self._json(202, {"ok": True, "message": "Perimeter sweep initiated"})

    def _netradar_trust(self):
        """Mark a device MAC as trusted in the whitelist."""
        data = self._read_json()
        mac = str(data.get("mac") or "").strip()
        name = str(data.get("name") or "").strip()
        if not mac:
            return self._json(400, {"error": "Missing MAC address"})
        ok = netradar.mark_device_trusted(mac, name)
        return self._json(200, {"ok": ok, "mac": mac})

    def _docintel_status(self):
        """Return snapshot of active document."""
        doc = docintel.get_active_document()
        return self._json(200, doc or {"status": "idle", "message": "No document loaded"})

    def _docintel_upload(self):
        """Handle document ingestion from base64 upload or local file path."""
        data = self._read_json()
        filename = str(data.get("filename") or "document.pdf").strip()
        file_path = data.get("file_path")
        file_b64 = data.get("file_base64")

        file_bytes = None
        if file_b64:
            try:
                if "," in file_b64:
                    file_b64 = file_b64.split(",", 1)[1]
                file_bytes = base64.b64decode(file_b64)
            except Exception as e:
                return self._json(400, {"error": f"Invalid base64 payload: {e}"})

        if not file_bytes and not file_path:
            return self._json(400, {"error": "Missing file_base64 or file_path"})

        try:
            doc_rec = docintel.parse_document(file_path=file_path, file_bytes=file_bytes, filename=filename)

            def _digest_worker(d_id):
                docintel.generate_executive_digest(d_id)
            threading.Thread(target=_digest_worker, args=(doc_rec["id"],), daemon=True).start()

            return self._json(200, {
                "ok": True,
                "doc_id": doc_rec["id"],
                "filename": doc_rec["filename"],
                "pages": doc_rec["pages"],
                "words": doc_rec["words"],
                "table_count": doc_rec["table_count"],
                "message": "Document ingested. Generating executive digest."
            })
        except Exception as e:
            return self._json(500, {"error": f"Failed to ingest document: {e}"})

    def _docintel_ask(self):
        """Answer question about active document."""
        data = self._read_json()
        query = str(data.get("query") or "").strip()
        doc_id = data.get("doc_id")
        if not query:
            return self._json(400, {"error": "Missing query"})

        res = docintel.ask_document(query, doc_id=doc_id)
        return self._json(200, res)

    def _docintel_export(self):
        """Export document tables to CSV."""
        data = self._read_json()
        doc_id = data.get("doc_id")
        try:
            exported = docintel.export_tables_to_csv(doc_id=doc_id)
            return self._json(200, {"ok": True, "files": exported, "count": len(exported)})
        except Exception as e:
            return self._json(500, {"error": f"Export failed: {e}"})

    def _diagnostic(self):
        """Trigger Stark Laser Diagnostic Sweep on-demand from HUD."""
        sfx.play("diagnostic", debounce_s=1.0)
        metrics = {}
        if psutil:
            try:
                metrics["cpu"] = psutil.cpu_percent(interval=None)
                metrics["ram"] = psutil.virtual_memory().percent
                metrics["disk"] = psutil.disk_usage(_drive_root()).percent
            except Exception:
                pass
        bus.diagnostic(active=True, **metrics)
        bus.activity("Stark Laser Diagnostic Sweep engaged", "accent")
        return self._json(200, {"ok": True, "metrics": metrics})

    def _dhaka_latest(self):
        """Return latest harvested Bangladeshi news wire."""
        data = dhaka_desk.harvest_all(force_refresh=False)
        return self._json(200, data)

    def _dhaka_refresh(self):
        """Force background re-harvest of Bangladeshi feeds."""
        data = dhaka_desk.harvest_all(force_refresh=True)
        bus.dhaka_bulletin(
            open=False,
            timestamp=data.get("timestamp"),
            updated_at=data.get("updated_at"),
            total_count=data.get("total_count", 0),
            articles=data.get("articles", [])[:40]
        )
        return self._json(200, {"ok": True, "count": data.get("total_count", 0)})

    def _dhaka_broadcast(self):
        """Trigger spoken audio broadcast of top stories."""
        data = self._read_json() if self.headers.get("content-length") else {}
        category = str(data.get("category") or "all").strip()
        res = dhaka_desk.broadcast_bulletin(category=category)
        return self._json(200, res)




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
    threading.Thread(target=lambda: sfx.play("hud_hum", debounce_s=8.0), daemon=True).start()
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

    os.environ["RON_SHOW_CONSOLE"] = "1"

    # Step 1: Bind socket immediately (<2ms)
    _server = _bind(args.host, args.port)
    url = f"http://{args.host}:{_server.server_address[1]}/"

    # Step 2: Instant UI Launch (<15ms) - pops up browser window right away!
    print(f"\n  R.O.N. HUD  ->  {url}\n  Launching holographic interface...\n")
    threading.Thread(target=open_ui, args=(url, args.browser), daemon=True).start()

    # Step 3: Start live telemetry loop immediately so HUD displays real metrics right away
    stop = threading.Event()
    threading.Thread(target=telemetry_loop, args=(stop,), daemon=True).start()

    # Step 4: Start weather poller in background
    if args.no_weather:
        bus.weather(ok=False, error="weather poller disabled")
        bus.meta(weather_ok=False)
        print("[Weather poller disabled: panel 05 will show OFFLINE.]")
    else:
        threading.Thread(target=weather_loop, args=(stop,), daemon=True).start()

    # Step 5: Asynchronous parallel bootloader for core engines & voice loop
    def _boot_core():
        global ron
        t0 = time.monotonic()
        print("[Initializing R.O.N. core engines...]")
        try:
            import main as ron_module
            ron = ron_module
            _ron_ready.set()
        except Exception as e:
            print(f"[Error loading RON core: {e}]")
            bus.activity(f"Core loading fault: {e}", "fail")
            bus.set_state(bus.ERROR, f"CORE FAULT: {e}")
            return

        bus.set_state(bus.IDLE, "SYSTEM READY")
        bus.activity("HUD server online", "ok")

        # Non-blocking audio preload & hardware probe in separate threads
        threading.Thread(target=sfx.preload_all, daemon=True).start()
        threading.Thread(target=probe_hardware, daemon=True).start()

        if hasattr(ron, "start_focus_monitor"):
            ron.start_focus_monitor()

        bus.meta(voice_loop=not args.no_voice, mic_muted=args.no_voice)

        if args.no_voice:
            ron.voice_enabled.clear()
            print("[Voice loop disabled: type commands in the HUD command line.]")
            try:
                telegram_bridge.start_telegram_daemon()
            except Exception as e:
                print(f"[telegram_bridge error: {e}]")
            try:
                docintel.start_rag_watcher()
            except Exception as e:
                print(f"[docintel watcher error: {e}]")
        else:
            threading.Thread(target=ron.run_voice_loop,
                             kwargs={"greet": not args.no_greet},
                             daemon=True).start()

        elapsed = round(time.monotonic() - t0, 2)
        print(f"[R.O.N. Core fully online in {elapsed}s]\n  Ctrl+C in console to shut down.\n")

        # Bring the process down when RON is dismissed by voice ("goodbye")
        def watch_shutdown():
            ron.shutdown_event.wait()
            time.sleep(0.6)
            _server.stopping.set()
            _server.shutdown()

        threading.Thread(target=watch_shutdown, daemon=True).start()

    threading.Thread(target=_boot_core, daemon=True).start()

    try:
        _server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("\n[Interrupted - shutting down.]")
    finally:
        stop.set()
        _server.stopping.set()
        if ron is not None and hasattr(ron, "shutdown_event"):
            ron.shutdown_event.set()
        bus.set_state(bus.OFFLINE)
        _server.server_close()
        # Closed here rather than in run_voice_loop: this process owns the
        # database, and the voice thread is only one of the things writing to it.
        history.close()
        print("[HUD offline.]")


if __name__ == "__main__":
    sys.exit(serve())
