# How to Add a New Feature to RON

Welcome to the RON development guide. This document provides a complete, production-grade guide for adding new capabilities to RON — whether you are adding a quick local utility, a deep generative AI workflow, a background monitor, or a remote Telegram command.

---

## ⚡ 60-Second Quick Start

Every feature in RON follows a standard 4-step pipeline:

```
[User Input] ──► [Extractor / LLM Dispatcher] ──► [your_feature.run()]
                                                          │
                   ┌──────────────────────────────────────┴──────────────────────────────────────┐
                   ▼                                      ▼                                      ▼
        [describe(result)]                     [hud_payload(result)]                  [telegram_bridge / bus]
       Spoken TTS to User                     Live SSE stream to HUD                  Smartphone push / feed
```

1. **Create the module** `your_feature.py` with `run()`, `hud_payload()`, and `describe()`.
2. **Wire into `main.py`**:
   - For fast local tools: Add a regex in `extract_your_feature()` (0 tokens, zero latency).
   - For AI-driven tools: Add JSON schema in `SYSTEM_PROMPT` and handler in `handle_your_feature()`.
3. **Connect the HUD & Telegram** via `bus.py` and `telegram_bridge.py`.
4. **Write an offline test** in `test_your_feature.py` (runs without microphone or API keys).

---

## 🧭 The Decision Tree: Which Route Should You Take?

Before writing code, identify which execution route your feature belongs to:

```
                      Is the command deterministic & predictable?
                                     │
                     ┌───────────────┴───────────────┐
                    YES                              NO
                     │                               │
       Does it need cloud LLM?             Needs LLM reasoning?
             │                               │
        ┌────┴────┐                          ▼
       NO        YES             [Route 2: Conversational Tool Call]
        │         │              • Add schema to SYSTEM_PROMPT
        ▼         ▼              • Emits JSON {"tool": "..."}
[Route 1: Direct] [Route 3: Two-Stage]
• 0 tokens, instant  • Fast classifier turn
• Regex matching    • Terse intent -> Rich writer
• Works offline     • (e.g. PDF, HTML, Email)
```

| Route | Characteristics | Best For | Example Modules |
|---|---|---|---|
| **1. Direct (Zero-Token)** | Instant execution via regex. Bypasses LLM. Works without API keys or internet. | System status, volume, clock, weather, timer, network speed, file opening, RON-Share. | `volume.py`, `clock.py`, `timer.py`, `finder.py` |
| **2. LLM Tool Calling** | Agent reasoning. LLM decides parameters dynamically and emits structured JSON. | Ambiguous intent, web searches, contextual questions, smart multi-argument dispatch. | `tools.py`, `browser_agent.py` |
| **3. Two-Stage Generative** | Stage 1 emits terse intent; Stage 2 invokes specialized writer prompt. Prevents stubs. | Reports, generated webpages, AI emails, deep research synthesis. | `researcher.py`, `email_notify.py`, `tribune.py` |
| **4. Background Daemon** | Non-blocking background threads. Continuously monitors state and pushes events. | Subnet scanning, inbox polling, document watching, radio streams. | `netradar.py`, `docintel.py`, `intel.py` |
| **5. Telegram Remote Uplink** | Mobile-to-desktop bridge. Triggered via smartphone bot commands. | Remote lock, camera capture, file upload/download, remote status. | `telegram_bridge.py` |

---

## 🏛️ The Golden Rules of RON Engineering

Every feature module in RON must strictly obey these four architectural laws:

1. **Zero Import-Time Side Effects**:
   - ❌ Never initiate network requests, spawn background threads, or perform heavy disk I/O at module top-level.
   - ✅ Keep imports lightweight. Defer heavy third-party imports (like `psutil`, `cv2`, `reportlab`) inside functions if they are optional.
2. **Never Raise Into Caller (`main.py`)**:
   - ❌ Never let an unhandled exception crash the conversational voice loop.
   - ✅ Wrap all public API boundaries with `try...except Exception as e:`. Return `{"ok": False, "error": str(e)}`.
3. **Flat, JSON-Serializable Payloads**:
   - ❌ Do not return complex custom class instances or binary buffers in `run()`.
   - ✅ Return plain Python dictionaries and lists. The HUD SSE bridge (`ui_server.py`) and SQLite audit log (`history.py`) must be able to serialize results via `json.dumps()`.
4. **Terse, Sharp Spoken Replies**:
   - ❌ Never speak 5 paragraphs of text over TTS.
   - ✅ Keep spoken voice lines under 2-3 sentences. End them courteously with *", Sir."* or *", Ifteqhar."*.

---

## 📦 Copy-Paste Boilerplate: `your_feature.py`

Start new features with this clean, production-ready template:

```python
"""your_feature.py — Brief description of what this module does.

Follows RON standard:
1. Safe execution — zero unhandled exceptions raised to callers.
2. Zero import-time side effects — network and I/O occur only inside run().
3. JSON-serializable payloads for HUD event bus and history logging.
"""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Optional third-party dependencies with graceful fallback flags
try:
    import requests  # example external package
    _HAS_DEPENDENCY = True
except ImportError:
    _HAS_DEPENDENCY = False


# ── Public API ───────────────────────────────────────────────────────────────

def run(**kwargs: Any) -> Dict[str, Any]:
    """Execute the feature logic.
    
    Returns:
        dict: Flat, JSON-serializable dictionary with at least 'ok': bool.
    """
    try:
        data = _fetch_or_compute(kwargs)
        return {
            "ok": True,
            "data": data,
            "count": len(data) if isinstance(data, list) else 1,
            "error": None,
        }
    except Exception as e:
        logger.warning("your_feature.run failed: %s", e)
        return {
            "ok": False,
            "data": None,
            "count": 0,
            "error": str(e),
        }


def hud_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    """Format the raw result for HUD display via bus.py.
    
    Keep keys clean and formatted (percentages, short strings) for the UI.
    """
    if not result.get("ok"):
        return {"status": "error", "message": result.get("error", "Unknown error")}
    
    return {
        "status": "online",
        "metric": result.get("count", 0),
        "details": result.get("data"),
    }


def describe(result: Dict[str, Any]) -> str:
    """Generate the short spoken TTS confirmation for the user."""
    if not result.get("ok"):
        return "I encountered an issue processing your request, Sir."
    
    count = result.get("count", 0)
    return f"Your feature operation completed successfully with {count} items processed, Sir."


# ── Internal Helpers ─────────────────────────────────────────────────────────

def _fetch_or_compute(params: dict) -> Any:
    """Internal worker function isolated from caller."""
    # Implement core logic here
    return {"sample_key": "sample_value"}
```

---

## 🛠️ Step-by-Step Practical Blueprint: Adding `sysinfo.py`

Let us walk through building an actual feature: **System Info** (reporting CPU, RAM, GPU, and Disk telemetry).

### Step 1 — Create `sysinfo.py`

Save this file in the project root:

```python
"""sysinfo.py — Local system resource telemetry (CPU, RAM, GPU, Disk)."""

import os
from typing import Any, Dict

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

try:
    import wmi
    _HAS_WMI = True
except ImportError:
    _HAS_WMI = False

_SAMPLE_INTERVAL = 0.5
_TARGET_DISK = os.environ.get("SystemDrive", "C:") + "\\"


def run() -> Dict[str, Any]:
    """Gather system metrics safely without raising exceptions."""
    result: Dict[str, Any] = {
        "ok": True,
        "cpu_percent": None,
        "ram_percent": None,
        "ram_used_gb": None,
        "ram_total_gb": None,
        "gpu_name": "N/A",
        "disk_percent": None,
        "disk_used_gb": None,
        "disk_total_gb": None,
    }
    
    if _HAS_PSUTIL:
        try:
            result["cpu_percent"] = psutil.cpu_percent(interval=_SAMPLE_INTERVAL)
            vm = psutil.virtual_memory()
            result["ram_percent"] = vm.percent
            result["ram_used_gb"] = round(vm.used / (1024 ** 3), 1)
            result["ram_total_gb"] = round(vm.total / (1024 ** 3), 1)
            
            du = psutil.disk_usage(_TARGET_DISK)
            result["disk_percent"] = du.percent
            result["disk_used_gb"] = round(du.used / (1024 ** 3), 1)
            result["disk_total_gb"] = round(du.total / (1024 ** 3), 1)
        except Exception:
            pass
            
    if _HAS_WMI:
        try:
            c = wmi.WMI()
            cards = c.Win32_VideoController()
            if cards and cards[0].Name:
                result["gpu_name"] = cards[0].Name
        except Exception:
            pass

    return result


def hud_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    """Shape telemetry for the HUD SYSTEM monitor panel."""
    cpu = result.get("cpu_percent")
    ram = result.get("ram_percent")
    disk = result.get("disk_percent")
    gpu = result.get("gpu_name") or "N/A"
    
    return {
        "metrics": {
            "cpu": f"{cpu:.0f}%" if cpu is not None else "N/A",
            "ram": f"{ram:.0f}%" if ram is not None else "N/A",
            "gpu": gpu,
            "disk": f"{disk:.0f}%" if disk is not None else "N/A",
        },
        "details": {
            "ram_str": f"{result.get('ram_used_gb')} / {result.get('ram_total_gb')} GB",
            "disk_str": f"{result.get('disk_used_gb')} / {result.get('disk_total_gb')} GB",
        }
    }


def describe(result: Dict[str, Any]) -> str:
    """Produce a clean spoken confirmation."""
    cpu = result.get("cpu_percent")
    ram = result.get("ram_percent")
    gpu = result.get("gpu_name")
    disk = result.get("disk_percent")
    
    parts = []
    if cpu is not None:
        parts.append(f"CPU is at {cpu:.0f} percent")
    if ram is not None:
        parts.append(f"RAM at {ram:.0f} percent")
    if disk is not None:
        parts.append(f"Disk at {disk:.0f} percent")
    if gpu and gpu != "N/A":
        parts.append(f"GPU {gpu}")
        
    if not parts:
        return "System metrics are currently unavailable, Sir."
        
    return ", ".join(parts) + ", Sir."
```

---

### Step 2 — Register Bus Snapshot & Event Dispatcher in `bus.py`

In `bus.py`, add the state key so the HUD has persistent access to the latest data on page reload:

```python
# Inside bus.py: _snapshot dictionary
_snapshot = {
    ...
    "sysinfo": {},  # <-- Add your snapshot key
}

# Add the public publisher function
def sysinfo(metrics: dict):
    """Publish system info telemetry to connected HUD clients."""
    with _lock:
        _snapshot["sysinfo"] = dict(metrics or {})
    _emit("sysinfo", {"sysinfo": metrics})
```

---

### Step 3 — Direct Route Extractor & Runner in `main.py`

Fast, predictable voice phrases should bypass the LLM entirely:

```python
# In main.py: Extractor regex
_SYSINFO_TRIGGER = re.compile(
    r"(system\s*(?:info|status|specs?)|cpu|ram|memory|gpu|disk\s*usage"
    r"|how\s+(?:is|are)\s+(?:my\s+)?(?:cpu|ram|memory|gpu|disk)"
    r"|what(?:'s| is) my (?:cpu|ram|memory|gpu|disk))",
    re.IGNORECASE
)

def extract_sysinfo(command: str):
    text = (command or "").strip()
    if _SYSINFO_TRIGGER.search(text):
        return {"kind": "sysinfo"}
    return None

def run_sysinfo(_spec: dict) -> str:
    """Shared runner: drives the HUD bus and returns spoken speech."""
    try:
        bus.set_state(bus.EXECUTING, "SYSTEM TELEMETRY")
        bus.activity("Querying system metrics", "pending")
        
        import sysinfo
        result = sysinfo.run()
        bus.sysinfo(sysinfo.hud_payload(result))
        
        bus.activity("System metrics retrieved", "ok")
        return sysinfo.describe(result)
    except Exception as e:
        bus.activity(f"Sysinfo error: {e}", "error")
        return "I encountered an error reading your system metrics, Sir."
```

Then in `_process_command()`, add the check before the LLM call:

```python
# Direct route execution (zero tokens, instant response)
if spec := extract_sysinfo(user_input):
    reply = run_sysinfo(spec)
    bus.transcript("user", user_input)
    bus.transcript("ron", reply)
    speak(reply)
    return
```

---

### Step 4 — LLM Tool Schema & Tool Handler in `main.py`

For natural language fallback when the user asks in an unexpected way (e.g. *"RON, is my PC breaking a sweat right now?"*):

1. **Add to `SYSTEM_PROMPT` Tools list**:
   ```python
   Tools:
   ...
   15. System Info:      {"tool": "get_system_info"}
   ```

2. **Add Handler**:
   ```python
   def handle_get_system_info(data: dict) -> str:
       """LLM tool-call handler for system telemetry."""
       return run_sysinfo(data or {})
   ```

3. **Add to Tool Dispatcher Block**:
   ```python
   elif tool == "get_system_info":
       result = handle_get_system_info(data)
   ```

---

### Step 5 — Telegram Pocket Uplink Integration in `telegram_bridge.py`

Allow remote monitoring from your smartphone! In `telegram_bridge.py`:

1. Add the command to the bot handler:
   ```python
   elif cmd == "/sysinfo" or cmd == "/specs":
       import sysinfo
       res = sysinfo.run()
       payload = sysinfo.hud_payload(res)
       metrics = payload.get("metrics", {})
       
       msg = (
           "💻 *WORKSTATION HARDWARE TELEMETRY*\n"
           f"• *CPU*: `{metrics.get('cpu', 'N/A')}`\n"
           f"• *RAM*: `{metrics.get('ram', 'N/A')}` ({payload['details']['ram_str']})\n"
           f"• *GPU*: `{metrics.get('gpu', 'N/A')}`\n"
           f"• *Disk*: `{metrics.get('disk', 'N/A')}` ({payload['details']['disk_str']})\n"
           f"• *Status*: `NOMINAL`"
       )
       _send_tg_message(chat_id, msg)
       bus.activity("Telegram: remote sysinfo requested", "ok")
   ```

Now texting `/sysinfo` from anywhere in the world gives an instant workstation status report!

---

### Step 6 — Visual HUD Panel Integration (`ui_server.py` & `ui/app.js`)

1. **In `ui_server.py`**:
   The SSE server automatically streams any `bus.emit("sysinfo", ...)` events to all open browsers.
2. **In `ui/app.js`**:
   Subscribe to the event:
   ```javascript
   // Listen for real-time telemetry updates
   eventSource.addEventListener("sysinfo", (e) => {
       const data = JSON.parse(e.data);
       updateSysinfoPanel(data.sysinfo);
   });

   function updateSysinfoPanel(sysinfo) {
       const cpuEl = document.getElementById("system-cpu-val");
       if (cpuEl && sysinfo.metrics) {
           cpuEl.textContent = sysinfo.metrics.cpu;
       }
   }
   ```

---

### Step 7 — Offline Unit Testing: `test_sysinfo.py`

Every module **must** have an offline test file that runs without microphone, internet, or LLM API keys:

```python
"""test_sysinfo.py — Offline unit test for sysinfo module."""

import unittest
from unittest.mock import patch
import sysinfo


class TestSysinfo(unittest.TestCase):
    def test_run_returns_valid_structure(self):
        result = sysinfo.run()
        self.assertIsInstance(result, dict)
        self.assertIn("ok", result)
        self.assertTrue(result["ok"])
        self.assertIn("cpu_percent", result)
        self.assertIn("ram_percent", result)

    def test_hud_payload_formatting(self):
        mock_result = {
            "ok": True,
            "cpu_percent": 24.5,
            "ram_percent": 60.0,
            "ram_used_gb": 9.6,
            "ram_total_gb": 16.0,
            "gpu_name": "NVIDIA RTX 4070",
            "disk_percent": 45.0,
            "disk_used_gb": 225.0,
            "disk_total_gb": 500.0,
        }
        payload = sysinfo.hud_payload(mock_result)
        self.assertEqual(payload["metrics"]["cpu"], "25%")
        self.assertEqual(payload["metrics"]["gpu"], "NVIDIA RTX 4070")

    def test_describe_spoken_string(self):
        mock_result = {
            "ok": True,
            "cpu_percent": 15.0,
            "ram_percent": 40.0,
            "disk_percent": 50.0,
            "gpu_name": "N/A",
        }
        speech = sysinfo.describe(mock_result)
        self.assertIn("CPU is at 15 percent", speech)
        self.assertTrue(speech.endswith(", Sir."))

    def test_graceful_failure_when_deps_missing(self):
        with patch.object(sysinfo, "_HAS_PSUTIL", False):
            result = sysinfo.run()
            self.assertTrue(result["ok"])
            self.assertIsNone(result["cpu_percent"])
            speech = sysinfo.describe(result)
            self.assertIn("Sir", speech)


if __name__ == "__main__":
    unittest.main()
```

Run tests in the terminal:
```bash
python -m unittest test_sysinfo.py
```

---

### Step 8 — Terminal & Voice Smoke Testing

Test your new feature immediately via CLI arguments without starting the voice loop:

```bash
# Direct Route (Zero tokens)
python main.py "what is my cpu usage"

# LLM Fallback Route
python main.py "give me a full system status report"
```

---

## ⚡ Advanced Production Patterns

### 1. Two-Stage Creative Generation (Documents, Webpages, Emails)
For tasks requiring lengthy, high-quality generated artifacts, use the **Two-Stage Pattern**:
1. **Stage 1 (Classifier/Turn 1)**: LLM produces terse JSON intent:
   `{"tool": "send_email", "recipient": "thanos", "context": "birthday wish"}`
2. **Stage 2 (Generator/Turn 2)**: Specialized isolated prompt (`EMAIL_COMPOSE_PROMPT` or `WRITER_PROMPT`) generates the high-density output without chat clutter.
*Reference implementations*: `email_notify.py` (`generate_composed_email`), `tools.py` (`generate_pdf`, `create_webpage`).

### 2. Async Daemon Background Scanners
For long-running tasks (network sweeps, inbox polling, file sync):
- Spawn an explicit daemon thread (`threading.Thread(target=..., daemon=True).start()`).
- Use thread locks (`threading.Lock`) when modifying shared state.
- Post incremental updates via `bus.activity("Scanning subnet...", "pending")`.
*Reference implementations*: `netradar.py`, `docintel.py`, `telegram_bridge.py`.

### 3. Desktop File Repositories (`RON-Share`)
If your feature handles files from phone or internet:
- Store files under user's standard Windows folders (e.g. `os.path.join(os.path.expanduser("~"), "Documents", "RON-Share")`).
- Sanitize file names to prevent directory traversal attacks (`os.path.basename()`).
- Provide an automatic explorer opening helper:
  ```python
  import subprocess
  subprocess.Popen(f'explorer "{folder_path}"')
  ```

---

## 🗺️ File Reference Matrix

| File | Purpose | What to Add |
|---|---|---|
| `your_feature.py` | Core feature implementation | `run()`, `hud_payload()`, `describe()` |
| `bus.py` | Central SSE event pub/sub | State key in `_snapshot`, publisher function |
| `main.py` | Dispatcher & Speech loop | Extractor regex, runner function, tool schema, dispatch case |
| `telegram_bridge.py` | Mobile Telegram bot | New `/command` handler in bot message loop |
| `ui_server.py` | HUD HTTP & SSE backend | Panel data mapping |
| `ui/app.js` | Holographic HUD UI | EventSource listener & DOM element updates |
| `test_your_feature.py`| Offline unit test suite | Unittest with mocks (zero mic/network needed) |
| `README.md` | User documentation | Feature table row, voice trigger examples |

---

## ✅ Pre-Merge Quality Checklist

Before submitting a PR or merging your new feature, verify all 8 items:

- [ ] **1. Clean Isolation**: Does `your_feature.py` import without performing I/O or network calls?
- [ ] **2. Exception Safety**: Is every public function wrapped so it never raises unhandled exceptions?
- [ ] **3. Fast Direct Route**: Are obvious command phrases caught in `main.py` before hitting the LLM?
- [ ] **4. LLM Fallback**: Is the tool schema in `SYSTEM_PROMPT` and dispatched in `_process_command()`?
- [ ] **5. HUD Pub/Sub**: Does the feature emit `bus.set_state()`, `bus.activity()`, and updates to the bus?
- [ ] **6. Telegram Uplink**: Is there a corresponding `/command` in `telegram_bridge.py` if relevant?
- [ ] **7. Offline Test**: Does `python -m unittest test_your_feature.py` pass without credentials?
- [ ] **8. Documentation**: Are example phrases and configuration options added to `README.md`?

---

*RON Architecture Team — Engineered for speed, privacy, and ironclad stability.*
