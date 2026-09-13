"""Executive Coach & Daily Standup Accountability Partner for R.O.N.

Provides:
  * Daily standup non-negotiables tracking with interactive completion.
  * Privacy-preserving active window focus telemetry (no keystrokes, no screen recording).
  * Three-ring productivity scoring (Goals %, Focus Ratio %, Productivity Score).
  * Proactive distraction interception (notifying user when lingering on social media/gaming).
  * Evening retrospective debrief synced to Neural Long-Term Memory (memory.py).
  * Real-time HUD integration via bus.coach().
"""

import ctypes
import ctypes.wintypes
import datetime
import json
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import bus
import memory

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "coach.json")
_lock = threading.RLock()

# ---------------------------------------------------------------------------
# Classification Dictionaries
# ---------------------------------------------------------------------------

PRODUCTIVE_KEYWORDS = [
    "visual studio code", "vs code", "code.exe", "pycharm", "intellij", "devenv",
    "terminal", "powershell", "cmd.exe", "wt.exe", "bash", "git", "sublime",
    "github", "gitlab", "bitbucket", "stackoverflow", "stackexchange", "docs.",
    "python", "javascript", "typescript", "react", "fastapi", "docker",
    "notion", "linear", "jira", "trello", "asana", "confluence",
    "winword", "excel", "powerpnt", "slack", "teams", "onenote",
    "chatgpt", "claude", "gemini", "deepseek", "perplexity", "cursor"
]

DISTRACTING_KEYWORDS = [
    "reddit.com", "reddit", "twitter.com", "x.com", "instagram.com", "facebook.com",
    "tiktok.com", "twitch.tv", "netflix.com", "primevideo", "hulu",
    "steam.exe", "steam", "epicgames", "discord.exe", "valorant", "dota 2",
    "league of legends", "cs2", "counter-strike", "overwatch", "roblox",
    "9gag", "buzzfeed", "youtube.com/shorts", "shorts"
]

NUDGE_DISTRACTION_THRESHOLD = 1200  # 20 minutes of continuous distraction
NUDGE_COOLDOWN = 1800               # 30 minutes between audio nudges


# ---------------------------------------------------------------------------
# State Management
# ---------------------------------------------------------------------------

def _today_str() -> str:
    return datetime.date.today().isoformat()


def _default_state() -> Dict[str, Any]:
    return {
        "date": _today_str(),
        "goals": [],
        "focus_seconds": 0.0,
        "distraction_seconds": 0.0,
        "neutral_seconds": 0.0,
        "distraction_streak": 0.0,
        "current_window": "Desktop",
        "current_category": "neutral",
        "last_nudge_at": 0.0,
        "blocker_enabled": False,
        "history": []
    }


def load_state() -> Dict[str, Any]:
    """Load standup state from coach.json, resetting if new day."""
    with _lock:
        if os.path.isfile(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except Exception:
                state = _default_state()
        else:
            state = _default_state()

        today = _today_str()
        if state.get("date") != today:
            # Archive previous day's results into history
            if state.get("goals") or state.get("focus_seconds", 0) > 0:
                metrics = _compute_metrics_from_state(state)
                archive_entry = {
                    "date": state.get("date"),
                    "goals_total": len(state.get("goals", [])),
                    "goals_completed": sum(1 for g in state.get("goals", []) if g.get("completed")),
                    "focus_seconds": state.get("focus_seconds", 0),
                    "distraction_seconds": state.get("distraction_seconds", 0),
                    "productivity_score": metrics["productivity_score"]
                }
                history = state.get("history", [])
                history.append(archive_entry)
                state["history"] = history[-30:]  # keep last 30 days

            # Reset daily fields for today
            state["date"] = today
            state["goals"] = []
            state["focus_seconds"] = 0.0
            state["distraction_seconds"] = 0.0
            state["neutral_seconds"] = 0.0
            state["distraction_streak"] = 0.0
            state["current_window"] = "Desktop"
            state["current_category"] = "neutral"
            state["last_nudge_at"] = 0.0
            save_state(state)

        return state


def save_state(state: Dict[str, Any]):
    """Save coach state atomically to coach.json."""
    with _lock:
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[coach] Could not save state: {e}")


# ---------------------------------------------------------------------------
# Active Window Telemetry & Classification
# ---------------------------------------------------------------------------

def get_active_window() -> Tuple[str, str]:
    """Return active foreground window title and process name using Windows user32."""
    if os.name != "nt":
        return "Unknown", "unknown"

    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return "Desktop", "explorer.exe"

        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
        else:
            title = "Desktop"

        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc_name = ""

        try:
            import psutil
            if pid.value:
                proc = psutil.Process(pid.value)
                proc_name = proc.name()
        except Exception:
            pass

        return title, proc_name
    except Exception:
        return "Desktop", "unknown"


def classify_window(title: str, proc_name: str) -> str:
    """Classify window as 'productive', 'distracting', or 'neutral'."""
    combined = f"{title} {proc_name}".lower()

    # Check for YouTube specifically: allow educational/music
    if "youtube" in combined:
        if any(w in combined for w in ("tutorial", "course", "lecture", "lofi", "ambient", "coding", "music", "learn")):
            return "neutral"
        return "distracting"

    # Distracting check
    for kw in DISTRACTING_KEYWORDS:
        if kw in combined:
            return "distracting"

    # Productive check
    for kw in PRODUCTIVE_KEYWORDS:
        if kw in combined:
            return "productive"

    return "neutral"


# ---------------------------------------------------------------------------
# Goal Lifecycle & Standup Operations
# ---------------------------------------------------------------------------

def start_standup(goal_texts: List[str]) -> Dict[str, Any]:
    """Initialize daily non-negotiable goals."""
    clean_goals = [g.strip() for g in goal_texts if g.strip()]
    if not clean_goals:
        return {"ok": False, "message": "No goals specified"}

    state = load_state()
    existing_goals = state.get("goals", [])
    start_id = len(existing_goals) + 1

    new_items = []
    now = time.time()
    for idx, g in enumerate(clean_goals):
        new_items.append({
            "id": start_id + idx,
            "text": g,
            "completed": False,
            "created_at": now,
            "completed_at": None
        })

    state["goals"] = existing_goals + new_items
    save_state(state)

    # Sync to memory.py for long-term intelligence
    for g in new_items:
        try:
            memory.remember(f"Today's goal: {g['text']}", category="project")
        except Exception:
            pass

    broadcast_state(event_name="standup_started")
    return {
        "ok": True,
        "goals": state["goals"],
        "count": len(new_items),
        "message": f"Recorded {len(new_items)} non-negotiable goal(s) for today, Sir."
    }


def mark_goal(identifier: str, completed: bool = True) -> Dict[str, Any]:
    """Toggle or complete a goal by index (1, 2, 3) or by text snippet."""
    state = load_state()
    goals = state.get("goals", [])
    if not goals:
        return {"ok": False, "message": "No goals currently tracked for today"}

    target_goal = None
    clean_id = identifier.strip().lower()

    # Try numeric match (1-indexed)
    num_match = re.search(r"\b(\d+)\b", clean_id)
    if num_match:
        idx = int(num_match.group(1))
        for g in goals:
            if g["id"] == idx:
                target_goal = g
                break

    # Try text match
    if not target_goal:
        for g in goals:
            if clean_id in g["text"].lower() or g["text"].lower() in clean_id:
                target_goal = g
                break

    if not target_goal:
        return {"ok": False, "message": f"Could not find any goal matching '{identifier}'"}

    target_goal["completed"] = completed
    target_goal["completed_at"] = time.time() if completed else None
    save_state(state)

    broadcast_state(event_name="goal_updated")
    metrics = _compute_metrics_from_state(state)

    status_str = "completed" if completed else "reopened"
    return {
        "ok": True,
        "goal": target_goal,
        "status": status_str,
        "metrics": metrics,
        "message": f"Goal marked {status_str}, Sir: {target_goal['text']}."
    }


def delete_goal(goal_id: int) -> bool:
    """Delete a goal by id."""
    state = load_state()
    goals = state.get("goals", [])
    new_goals = [g for g in goals if g["id"] != goal_id]
    if len(new_goals) != len(goals):
        state["goals"] = new_goals
        save_state(state)
        broadcast_state(event_name="goal_deleted")
        return True
    return False


def set_blocker(enabled: bool) -> bool:
    """Enable or disable proactive distraction interception."""
    state = load_state()
    state["blocker_enabled"] = bool(enabled)
    save_state(state)
    broadcast_state(event_name="blocker_toggled")
    return state["blocker_enabled"]


# ---------------------------------------------------------------------------
# Metrics & Debrief Calculations
# ---------------------------------------------------------------------------

def _compute_metrics_from_state(state: Dict[str, Any]) -> Dict[str, Any]:
    goals = state.get("goals", [])
    total_goals = len(goals)
    completed_goals = sum(1 for g in goals if g.get("completed"))

    goal_pct = round((completed_goals / total_goals * 100)) if total_goals > 0 else 0

    focus_sec = state.get("focus_seconds", 0.0)
    distract_sec = state.get("distraction_seconds", 0.0)
    neutral_sec = state.get("neutral_seconds", 0.0)
    work_total = focus_sec + distract_sec

    if work_total > 0:
        focus_ratio_pct = round((focus_sec / work_total) * 100)
    else:
        focus_ratio_pct = 100 if focus_sec > 0 else 50

    # Productivity score: 60% goal achievement, 40% focus ratio
    if total_goals > 0:
        productivity_score = round((goal_pct * 0.6) + (focus_ratio_pct * 0.4))
    else:
        productivity_score = focus_ratio_pct

    return {
        "total_goals": total_goals,
        "completed_goals": completed_goals,
        "pending_goals": total_goals - completed_goals,
        "goal_pct": goal_pct,
        "focus_ratio_pct": focus_ratio_pct,
        "productivity_score": max(0, min(100, productivity_score)),
        "focus_hours": round(focus_sec / 3600, 1),
        "distraction_minutes": round(distract_sec / 60),
        "neutral_minutes": round(neutral_sec / 60)
    }


def get_standup_status() -> Dict[str, Any]:
    """Get current standup goals, metrics, and window focus status."""
    state = load_state()
    metrics = _compute_metrics_from_state(state)
    return {
        "date": state.get("date"),
        "goals": state.get("goals", []),
        "metrics": metrics,
        "current_window": state.get("current_window"),
        "current_category": state.get("current_category"),
        "distraction_streak_min": round(state.get("distraction_streak", 0.0) / 60),
        "blocker_enabled": state.get("blocker_enabled", False)
    }


def evening_debrief() -> Dict[str, Any]:
    """Generate evening debrief summary and save to long-term memory."""
    state = load_state()
    metrics = _compute_metrics_from_state(state)
    date_str = state.get("date", _today_str())

    completed = metrics["completed_goals"]
    total = metrics["total_goals"]
    score = metrics["productivity_score"]
    focus_hrs = metrics["focus_hours"]

    # Formulate spoken debrief
    if total == 0:
        spoken = f"Time for your daily debrief, Sir. You logged {focus_hrs} hours of deep focus with a focus ratio of {metrics['focus_ratio_pct']}%. No non-negotiable goals were registered for today."
    elif completed == total:
        spoken = f"Flawless execution today, Sir. You completed all {total} of your non-negotiables with a productivity rating of {score}% across {focus_hrs} hours of focused work."
    else:
        pending = total - completed
        spoken = f"Time for your daily debrief, Sir. You completed {completed} of {total} goals today with {pending} pending. Your daily productivity rating is {score}%."

    # Commit to neural long-term memory
    try:
        memory.remember(
            f"Daily Standup Retrospective for {date_str}: Completed {completed}/{total} goals. Productivity score: {score}%. Deep focus: {focus_hrs} hours.",
            category="project"
        )
    except Exception:
        pass

    broadcast_state(event_name="debrief_completed")
    return {
        "ok": True,
        "spoken": spoken,
        "metrics": metrics,
        "goals": state.get("goals", [])
    }


# ---------------------------------------------------------------------------
# Background Tick & Proactive Distraction Interception
# ---------------------------------------------------------------------------

def track_tick(interval_seconds: float = 5.0) -> Optional[Dict[str, Any]]:
    """Inspect active window, update seconds, and evaluate distraction nudge."""
    title, proc = get_active_window()
    category = classify_window(title, proc)

    with _lock:
        state = load_state()
        state["current_window"] = title[:80]
        state["current_category"] = category

        if category == "productive":
            state["focus_seconds"] += interval_seconds
            state["distraction_streak"] = 0.0
        elif category == "distracting":
            state["distraction_seconds"] += interval_seconds
            state["distraction_streak"] += interval_seconds
        else:
            state["neutral_seconds"] += interval_seconds
            # Decay distraction streak slowly
            state["distraction_streak"] = max(0.0, state["distraction_streak"] - (interval_seconds * 0.5))

        # Check for proactive distraction nudge
        nudge_payload = None
        streak = state.get("distraction_streak", 0.0)
        now = time.time()
        last_nudge = state.get("last_nudge_at", 0.0)

        # Only nudge if distraction streak > threshold, goals are pending, and cooldown elapsed
        metrics = _compute_metrics_from_state(state)
        has_pending_goals = metrics["pending_goals"] > 0

        if streak >= NUDGE_DISTRACTION_THRESHOLD and has_pending_goals and (now - last_nudge >= NUDGE_COOLDOWN):
            state["last_nudge_at"] = now
            streak_min = round(streak / 60)

            # Find first pending goal text
            first_pending = next((g["text"] for g in state.get("goals", []) if not g.get("completed")), "your daily goals")

            # Extract site/app name
            app_name = "social media"
            for kw in ("reddit", "twitter", "instagram", "facebook", "youtube", "steam", "discord"):
                if kw in title.lower() or kw in proc.lower():
                    app_name = kw.capitalize()
                    break

            spoken = f"Sir, you have spent {streak_min} minutes browsing {app_name} while your task '{first_pending}' is pending. Shall I close this window?"
            nudge_payload = {
                "type": "distraction_nudge",
                "app": app_name,
                "streak_min": streak_min,
                "pending_task": first_pending,
                "spoken": spoken
            }

        save_state(state)

    broadcast_state(event_name="tick")
    return nudge_payload


def broadcast_state(event_name: str = "update"):
    """Publish current coach snapshot and progress rings to bus.coach()."""
    try:
        state = load_state()
        metrics = _compute_metrics_from_state(state)
        payload = {
            "event": event_name,
            "date": state.get("date"),
            "goals": state.get("goals", []),
            "metrics": metrics,
            "current_window": state.get("current_window"),
            "current_category": state.get("current_category"),
            "distraction_streak_min": round(state.get("distraction_streak", 0.0) / 60),
            "blocker_enabled": state.get("blocker_enabled", False),
            "updated_at": time.time()
        }
        bus.coach(**payload)
    except Exception:
        pass
