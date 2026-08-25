"""Fast file and folder search across the user's disks, for RON.

A leaf module in the mould of `weather.py` and `history.py`: standard library
only, imports nothing from RON (so there is no cycle with `bus`), and follows the
same two rules --

1. **Nothing here ever raises into the caller.** A permission error mid-walk, a
   vanished directory, a bad root: all of it is swallowed and the search keeps
   going. A search must never take RON down.
2. **Nothing happens at import time.** Drives are enumerated only when a search
   asks, so `RON_SEARCH_ROOTS` can be set after the import and the tests can point
   this at a scratch tree.

The traversal is a *bounded* breadth-first `os.scandir` walk -- not a naive
`Path.rglob("*keyword*")` over the whole drive, which follows junctions into
loops, descends into `C:\\Windows` and `node_modules`, and can hang for minutes.
This one prunes system and cache directories, refuses to cross reparse points,
caps the result count, and stops at a wall-clock time budget. That is the
"most optimal" part: it looks where a person's files actually live and quits
early instead of grinding through the OS.

    python finder.py "python projects"     # keyword search across fixed drives
    python finder.py "*.pdf"                # every PDF (extension mode)
    python finder.py "report.pdf"           # an exact filename
    python finder.py --folder "Documents"   # folders named like this only
"""

import collections
import os
import sys
import time

# -- configuration -----------------------------------------------------------

MAX_RESULTS = 60          # enough to be useful; the HUD shows the first slice
TIME_BUDGET = 8.0         # seconds; a search that runs longer is truncated
MAX_DEPTH = 12            # deep enough for real trees, shallow enough to stay sane
_PROGRESS_EVERY = 400     # entries between progress callbacks (caller throttles too)

# Directories that never hold what a person is looking for and cost a fortune to
# descend. Matched case-insensitively against the bare directory name.
_SKIP_DIRS = frozenset({
    "windows", "$recycle.bin", "system volume information", "$sysreset",
    "recovery", "perflogs", "programdata", "appdata", "application data",
    "node_modules", "__pycache__", ".git", ".svn", ".hg", ".cache",
    "venv", ".venv", "env", "site-packages", ".gradle", ".nuget", ".cargo",
    "temp", "tmp", "cache", "caches", ".npm", ".m2", ".tox", "dist-info",
})

# Extensions people say as bare words map to their real suffix; anything not here
# is taken literally, so "*.rs" or "log" still work.
_EXT_WORDS = {
    "pdf": ".pdf", "word": ".docx", "excel": ".xlsx", "powerpoint": ".pptx",
    "image": ".jpg", "photo": ".jpg", "picture": ".jpg", "video": ".mp4",
    "music": ".mp3", "song": ".mp3", "text": ".txt", "python": ".py",
    "zip": ".zip", "csv": ".csv", "json": ".json",
}


# -- roots -------------------------------------------------------------------

def _drive_is_fixed(root):
    """True for an internal disk. On non-Windows every root counts as fixed."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        # 3 == DRIVE_FIXED; skip removable, network and CD-ROM drives.
        return ctypes.windll.kernel32.GetDriveTypeW(root) == 3
    except Exception:
        return True


def search_roots():
    """Where to look. `RON_SEARCH_ROOTS` overrides; otherwise fixed drives.

    Read lazily so a test can set the environment variable after import. The
    override is split on the OS path separator and on commas, so both
    ``F:\\;D:\\`` and ``F:\\,D:\\`` work.
    """
    override = (os.environ.get("RON_SEARCH_ROOTS") or "").strip()
    if override:
        raw = override.replace(",", os.pathsep).split(os.pathsep)
        return [r.strip() for r in raw if r.strip() and os.path.isdir(r.strip())]

    if os.name == "nt":
        roots = []
        for code in range(ord("A"), ord("Z") + 1):
            root = f"{chr(code)}:\\"
            if os.path.isdir(root) and _drive_is_fixed(root):
                roots.append(root)
        return roots or ["C:\\"]

    home = os.path.expanduser("~")
    return [home] if os.path.isdir(home) else ["/"]


# -- query parsing -----------------------------------------------------------

def _classify(query):
    """(mode, needle) from a raw query when the caller did not pin a mode.

    ``*.pdf`` / ``.pdf`` / ``pdf`` -> extension; ``report.pdf`` -> filename;
    anything else -> keyword (substring, files and folders alike).
    """
    q = (query or "").strip().strip('"').strip()
    low = q.lower()

    if low in _EXT_WORDS:
        return "extension", _EXT_WORDS[low]
    if low.startswith("*."):
        return "extension", low[1:]
    if low.startswith(".") and " " not in low and low.count(".") == 1:
        return "extension", low
    # A single dotted token with a short trailing suffix reads as a filename.
    if " " not in q and "." in q and not q.endswith("."):
        ext = q.rsplit(".", 1)[1]
        if 1 <= len(ext) <= 5 and ext.isalnum():
            return "filename", q
    return "keyword", q


# -- matching ----------------------------------------------------------------

def _matcher(mode, needle):
    """Return (predicate(name, is_dir) -> bool). Everything is case-folded once."""
    n = needle.lower()
    if mode == "extension":
        ext = n if n.startswith(".") else "." + n
        return lambda name, is_dir: (not is_dir) and name.lower().endswith(ext)
    if mode == "filename":
        return lambda name, is_dir: name.lower() == n
    if mode == "folder":
        return lambda name, is_dir: is_dir and n in name.lower()
    # keyword: substring against files and folders alike
    return lambda name, is_dir: n in name.lower()


def _is_reparse(entry):
    """True for a junction/symlink, so the walk never chases a loop into itself."""
    try:
        if entry.is_symlink():
            return True
        if os.name == "nt":
            # FILE_ATTRIBUTE_REPARSE_POINT (0x400) catches junctions too.
            return bool(entry.stat(follow_symlinks=False).st_file_attributes & 0x400)
    except OSError:
        return True   # unreadable -> treat as something to leave alone
    return False


# -- the search --------------------------------------------------------------

def find(query, mode="auto", roots=None, progress=None):
    """Search `roots` for `query`. Returns a result dict; never raises.

    `mode` is auto | keyword | filename | extension | folder. `progress`, if
    given, is called as ``progress(scanned, current_dir)`` every few hundred
    entries -- the caller (not this module) decides how to throttle and forward
    it, which keeps this file free of any dependency on `bus`.
    """
    started = time.time()
    q = (query or "").strip().strip('"').strip()

    if mode == "auto":
        mode, needle = _classify(q)
    else:
        _, auto_needle = _classify(q)
        needle = auto_needle if mode == "extension" else q

    result = {
        "ok": True, "query": q, "mode": mode, "matches": [],
        "count": 0, "scanned": 0, "truncated": False,
        "elapsed": 0.0, "roots": [], "error": None,
    }
    if not needle:
        result["error"] = "empty query"
        return result

    match = _matcher(mode, needle)
    roots = roots if roots is not None else search_roots()
    result["roots"] = list(roots)
    matches = result["matches"]
    scanned = 0
    truncated = False

    for root in roots:
        if truncated:
            break
        queue = collections.deque([(root, 0)])
        while queue:
            if time.time() - started > TIME_BUDGET or len(matches) >= MAX_RESULTS:
                truncated = True
                break
            current, depth = queue.popleft()
            try:
                it = os.scandir(current)
            except OSError:
                continue
            with it:
                for entry in it:
                    scanned += 1
                    if progress and scanned % _PROGRESS_EVERY == 0:
                        progress(scanned, current)
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    if match(entry.name, is_dir):
                        matches.append(_row(entry, is_dir))
                        if len(matches) >= MAX_RESULTS:
                            truncated = True
                            break
                    if is_dir and depth < MAX_DEPTH \
                            and entry.name.lower() not in _SKIP_DIRS \
                            and not entry.name.startswith("$") \
                            and not _is_reparse(entry):
                        queue.append((entry.path, depth + 1))

    result["scanned"] = scanned
    result["count"] = len(matches)
    result["truncated"] = truncated
    result["elapsed"] = round(time.time() - started, 3)
    return result


def _row(entry, is_dir):
    """One match, as a flat, JSON-safe dict."""
    size = None
    if not is_dir:
        try:
            size = entry.stat(follow_symlinks=False).st_size
        except OSError:
            size = None
    return {
        "path": entry.path,
        "name": entry.name,
        "kind": "dir" if is_dir else "file",
        "dir": os.path.dirname(entry.path),
        "size": size,
    }


# -- spoken answer -----------------------------------------------------------

def _phrase_mode(mode, query):
    """How the query reads inside a sentence."""
    if mode == "extension":
        ext = query.lower().lstrip("*").lstrip(".")
        return f"{ext.upper()} files"
    if mode == "folder":
        return f'folders named "{query}"'
    return f'"{query}"'


def describe(result):
    """The spoken sentence for a finished search. Always plain, never raises."""
    if not result or not result.get("ok"):
        return "I ran into a problem searching your disk, Sir."

    subject = _phrase_mode(result["mode"], result["query"])
    count = result["count"]
    if count == 0:
        return f"I could not find any {subject} on your drives, Sir."

    first = result["matches"][0]
    where = first["dir"] or first["path"]
    if count == 1:
        return f'I found one match for {subject}: {first["name"]}, in {where}.'

    more = " There may be more beyond that." if result["truncated"] else ""
    shown = min(count, MAX_RESULTS)
    lead = f"I found {shown} matches for {subject}, Sir."
    return f'{lead} The first is {first["name"]}, in {where}.{more}'


# -- HUD payload -------------------------------------------------------------

def _human_size(n):
    if not isinstance(n, (int, float)):
        return ""
    step = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if step < 1024 or unit == "TB":
            return f"{step:.0f} {unit}" if unit == "B" else f"{step:.1f} {unit}"
        step /= 1024
    return ""


def hud_payload(result):
    """Flat, JSON-serialisable dict for `bus.search(**...)` and the overlay."""
    if not result or not result.get("ok"):
        return {"ok": False, "status": "error",
                "error": (result or {}).get("error") or "search failed"}
    return {
        "ok": True,
        "status": "empty" if result["count"] == 0 else "done",
        "query": result["query"],
        "mode": result["mode"],
        "count": result["count"],
        "scanned": result["scanned"],
        "elapsed_ms": int(result["elapsed"] * 1000),
        "truncated": result["truncated"],
        "roots": result["roots"],
        "results": [
            {"path": m["path"], "name": m["name"], "kind": m["kind"],
             "dir": m["dir"], "size": _human_size(m["size"])}
            for m in result["matches"]
        ],
    }


def reset_cache():
    """No persistent cache to clear -- present for parity with the other leaves."""
    return None


# -- command line ------------------------------------------------------------

def main(argv):
    mode = "auto"
    if argv and argv[0] in ("--folder", "-d"):
        mode, argv = "folder", argv[1:]
    if not argv:
        print('usage: python finder.py [--folder] "query"')
        print(f"roots: {search_roots()}")
        return 1

    query = " ".join(argv)

    def show(scanned, current):
        sys.stderr.write(f"\r  scanned {scanned:>7}  {current[:60]:<60}")
        sys.stderr.flush()

    print(f'[searching {search_roots()} for "{query}" ...]\n')
    result = find(query, mode=mode, progress=show)
    sys.stderr.write("\r" + " " * 78 + "\r")

    print(describe(result))
    print(f'\nmode {result["mode"]} · scanned {result["scanned"]} · '
          f'{result["count"]} match(es) · {result["elapsed"]}s'
          f'{" · truncated" if result["truncated"] else ""}\n')
    for m in result["matches"][:20]:
        tag = "DIR " if m["kind"] == "dir" else "FILE"
        print(f"  [{tag}] {m['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
