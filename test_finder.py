"""Offline check of the disk-search engine.

    python test_finder.py

Everything runs against a scratch tree via `RON_SEARCH_ROOTS`, so no real drive
is walked and nothing on your desktop is touched. Covers the four match modes,
directory pruning, the result cap, `describe()` phrasing for 0/1/many matches,
the HUD payload shape, fault tolerance on bad roots, and the command
understanding in `main.extract_find` -- including phrases that must NOT route to
a disk search.
"""

import json
import os
import sys
import tempfile

# The scratch tree is built before finder reads anything.
_BASE = tempfile.mkdtemp(prefix="ron-finder-test-")
os.environ["RON_SEARCH_ROOTS"] = _BASE

# A small forest:
#   _BASE/
#     projects/
#       python_projects/          <- folder match for "python"
#         find_me.py              <- keyword + extension hit
#       notes.txt                 <- filename hit below
#       python_notes.txt          <- keyword hit
#     $Recycle.bin/junk.py        <- pruned: never scanned
#     node_modules/pkg/index.js   <- pruned: never scanned
#     deep/a/b/c/d/e/f/g/h/i/j/k/l/much_too_deep.py   <- beyond MAX_DEPTH
os.makedirs(os.path.join(_BASE, "projects", "python_projects"), exist_ok=True)
with open(os.path.join(_BASE, "projects", "python_projects", "find_me.py"), "w") as fh:
    fh.write("print('hi')\n")
with open(os.path.join(_BASE, "projects", "notes.txt"), "w") as fh:
    fh.write("hello\n")
with open(os.path.join(_BASE, "projects", "python_notes.txt"), "w") as fh:
    fh.write("hello\n")

_junk = os.path.join(_BASE, "$Recycle.bin", "node_modules", "pkg")
os.makedirs(_junk, exist_ok=True)
with open(os.path.join(_junk, "junk.py"), "w") as fh:
    fh.write("# should never be seen\n")

_deep = os.path.join(_BASE, "deep", *list("abcdefghijkl"))
os.makedirs(_deep, exist_ok=True)
with open(os.path.join(_deep, "much_too_deep.py"), "w") as fh:
    fh.write("x\n")

import bus            # noqa: E402  (after RON_DB-free env; bus is import-safe)
import finder         # noqa: E402

FAILURES = []


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f"  -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def names(result):
    return sorted(m["name"] for m in result["matches"])


def main():
    print("RON file-search check\n")

    # -- 1. modes --------------------------------------------------------------
    print("1. match modes")
    r = finder.find("python")
    check("keyword mode chosen", r["mode"] == "keyword", r)
    check("keyword finds files and folders",
          set(names(r)) == {"find_me.py", "python_projects", "python_notes.txt"},
          names(r))

    r = finder.find("*.py")
    check("star pattern -> extension", (r["mode"], r["count"]) == ("extension", 1), r)
    check("extension search returns only find_me.py", names(r) == ["find_me.py"], names(r))

    r = finder.find("notes.txt", mode="filename")
    check("filename mode is exact",
          [m["path"] for m in r["matches"]] == [os.path.join(_BASE, "projects", "notes.txt")],
          [m["path"] for m in r["matches"]])

    r = finder.find("python", mode="folder")
    check("folder mode is dirs only", names(r) == ["python_projects"], names(r))

    r = finder.find("pdf")
    check("bare ext word -> extension", r["mode"] == "extension" and r["count"] == 0, r)

    # -- 2. pruning and depth --------------------------------------------------
    print("\n2. pruning")
    r = finder.find("*.py")
    # junk.py hides behind "$Recycle.bin" (skipped by the $ rule) and
    # node_modules (skipped by name); much_too_deep.py sits past MAX_DEPTH.
    check("pruned and out-of-depth py files never surface",
          names(r) == ["find_me.py"], names(r))
    r = finder.find("much_too_deep", roots=[os.path.join(_BASE, "deep")])
    check("beyond MAX_DEPTH there is nothing", r["count"] == 0, r)

    # -- 3. caps ----------------------------------------------------------------
    print("\n3. caps")
    many = os.path.join(_BASE, "many")
    os.makedirs(many, exist_ok=True)
    for i in range(finder.MAX_RESULTS + 10):
        with open(os.path.join(many, f"cap_{i}.log"), "w") as fh:
            fh.write("x\n")
    r = finder.find("cap_", roots=[many])
    check("result count is capped at MAX_RESULTS",
          r["count"] == finder.MAX_RESULTS and r["truncated"], (r["count"], r["truncated"]))

    # -- 4. faults ---------------------------------------------------------------
    print("\n4. faults")
    r = finder.find("", roots=[_BASE])
    check("empty query errors cleanly", not r["ok"] or r.get("error") == "empty query", r)
    r = finder.find("x", roots=["Q:\\definitely-not-here\\"])
    check("bad root never raises", r["count"] == 0 and r["error"] is None, r)

    # -- 5. describe() -----------------------------------------------------------
    print("\n5. spoken answers")
    empty = {"ok": True, "query": "ghost", "mode": "keyword", "matches": [],
             "count": 0, "truncated": False}
    one = dict(empty, count=1, matches=[
        {"name": "notes.txt", "dir": _BASE + "\\projects"}])
    manyr = dict(empty, count=finder.MAX_RESULTS, truncated=True, matches=[
        {"name": "first.log", "dir": many}] * finder.MAX_RESULTS)
    check("zero matches apologises",
          finder.describe(empty) == 'I could not find any "ghost" on your drives, Sir.',
          finder.describe(empty))
    d1 = finder.describe(one)
    check("one match names it", "one match" in d1 and "notes.txt" in d1, d1)
    dm = finder.describe(manyr)
    check("many matches leads with a count", str(finder.MAX_RESULTS) in dm, dm)
    check("truncation is confessed", "more beyond that" in dm, dm)
    check("failed result gets an apology",
          "problem" in finder.describe({"ok": False}), finder.describe({"ok": False}))

    # -- 6. hud payload ----------------------------------------------------------
    print("\n6. HUD payload")
    p = finder.hud_payload(one)
    check("status done for hits", p["status"] == "done", p)
    check("payload keys are flat and present",
          all(k in p for k in ("query", "mode", "count", "scanned",
                               "elapsed_ms", "roots", "results")), p)
    check("results carry human sizes", all("size" in m and isinstance(m["size"], str)
                                           for m in p["results"]), p["results"])
    check("payload is JSON-serialisable", bool(json.dumps(p)))
    pe = finder.hud_payload(empty)
    check("zero hits -> status empty", pe["status"] == "empty", pe)
    pf = finder.hud_payload(None)
    check("failure -> status error", pf["status"] == "error" and not pf["ok"], pf)

    # -- 7. extract_find truth table ---------------------------------------------
    print("\n7. command understanding")
    import main as ron   # heavy import; kept late so the earlier checks run first

    positives = {
        "find my python projects": ("python projects", "keyword"),
        "ron find my python projects": ("python projects", "keyword"),
        "search for report.pdf": ("report.pdf", "filename"),
        "locate report.pdf": ("report.pdf", "filename"),
        "find all pdf files": ("pdf", "extension"),
        "search *.docx": ("docx", "extension"),
        "find the downloads folder": ("downloads", "folder"),
        "where is my resume": ("resume", "keyword"),
        "find a file called notes.txt": ("notes.txt", "filename"),
        "look for budget spreadsheet": ("budget spreadsheet", "keyword"),
    }
    for cmd, want in positives.items():
        got = ron.extract_find(cmd)
        check(f"routes: {cmd!r}", got == {"query": want[0], "mode": want[1]},
              f"got {got}")

    negatives = [
        "play believer",               # music verb, not a hunt
        "open documents",              # folder opener owns this
        "visit facebook",              # website opener owns this
        "what's the weather",          # weather owns this
        "search youtube for lofi",     # online veto
        "find out",                    # pure filler
        "hello ron",                   # conversation
    ]
    for cmd in negatives:
        check(f"ignores: {cmd!r}", ron.extract_find(cmd) is None,
              f"got {ron.extract_find(cmd)}")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
