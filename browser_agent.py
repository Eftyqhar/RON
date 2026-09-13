"""Small, stateful browser controller for Ron.

The controller deliberately performs one browser operation per ``run`` call.
That makes each voice request observable and prevents an ambiguous instruction
from turning into a long, unverified sequence of clicks.
"""

import csv
import os
import re
import threading
import time
import tempfile
from pathlib import Path


_lock = threading.RLock()
_driver = None
_profile_dir = None
_last_records = []
_DANGEROUS = {"submit", "purchase", "pay", "send", "delete", "publish", "post"}


def _brave_binary():
    """Return Brave's executable when it is installed, otherwise None."""
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) /
        "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) /
        "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
    ]
    return next((str(path) for path in candidates if path.is_file()), None)


def _documents_dir():
    base = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents" / "Ron Browser"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _safe_name(value, default="browser_data"):
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "")).strip("_")
    return value[:80] or default


def _load_selenium():
    """Import Selenium lazily so normal Ron commands still work without it."""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support.ui import Select
        return webdriver, Options, By, Keys, Select
    except ImportError as exc:
        raise RuntimeError("Browser control needs Selenium. Run: pip install selenium") from exc


def _is_driver_alive(driver):
    """Check whether the WebDriver session is actively connected and valid."""
    if driver is None:
        return False
    try:
        _ = driver.window_handles
        return True
    except Exception:
        return False


def _get_driver(force_recreate=False):
    global _driver, _profile_dir
    if not force_recreate and _driver is not None:
        if _is_driver_alive(_driver):
            return _driver
        close()
    webdriver, Options, _By, _Keys, _Select = _load_selenium()
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.selenium_manager import SeleniumManager
    options = Options()
    options.add_experimental_option("excludeSwitches", ["enable-logging"])
    options.add_argument("--disable-notifications")
    # Do not attach to the user's normal Brave profile. Besides protecting the
    # existing session, this avoids Chromium's profile lock / DevTools port
    # startup failure when Brave is already open.
    _profile_dir = tempfile.mkdtemp(prefix="ron_brave_")
    options.add_argument(f"--user-data-dir={_profile_dir}")
    options.add_argument("--remote-debugging-port=0")
    options.add_experimental_option("prefs", {
        "download.default_directory": str(_documents_dir()),
        "download.prompt_for_download": False,
    })
    brave = _brave_binary()
    if brave:
        options.binary_location = brave

    # webdriver.Chrome() searches PATH, which includes this project's legacy
    # chromedriver.exe on Windows. It is v139 and cannot drive current Brave.
    # Calling Selenium Manager ourselves gives us a matching driver and makes
    # the service path explicit, so that stale executable is never considered.
    manager_args = ["--browser", "chrome", "--skip-driver-in-path"]
    if brave:
        manager_args.extend(["--browser-path", brave])
    assets = SeleniumManager().binary_paths(manager_args)
    driver_path = assets.get("driver_path")
    if not driver_path:
        raise RuntimeError("Selenium Manager could not find a compatible browser driver.")
    _driver = webdriver.Chrome(service=Service(executable_path=driver_path), options=options)
    _driver.implicitly_wait(4)
    return _driver


def _page_summary(driver, limit=900):
    title = (driver.title or "untitled page").strip()
    url = driver.current_url
    text = re.sub(r"\s+", " ", driver.find_element("tag name", "body").text or "").strip()
    return {"title": title, "url": url, "text": text[:limit]}


def _element(driver, target):
    """Resolve a CSS selector first, then visible text / accessible labels."""
    _webdriver, _Options, By, _Keys, _Select = _load_selenium()
    target = str(target or "").strip()
    if not target:
        raise ValueError("I need a button, field, or selector to target.")
    try:
        return driver.find_element(By.CSS_SELECTOR, target)
    except Exception:
        pass
    xpath = ("//*[normalize-space(.)=" + _xpath_literal(target) + "]"
             " | //*[@aria-label=" + _xpath_literal(target) + "]"
             " | //*[@name=" + _xpath_literal(target) + "]"
             " | //*[@placeholder=" + _xpath_literal(target) + "]")
    return driver.find_element(By.XPATH, xpath)


def _xpath_literal(value):
    if "'" not in value:
        return "'" + value + "'"
    if '"' not in value:
        return '"' + value + '"'
    return "concat(" + ", \"'\", ".join("'%s'" % part for part in value.split("'")) + ")"


def _requires_confirmation(action, target=""):
    words = (str(action) + " " + str(target)).lower()
    return any(re.search(r"\b" + word + r"\b", words) for word in _DANGEROUS)


def _save_records(records, file_name, fmt):
    if not records:
        raise ValueError("There is no extracted data to save yet.")
    if not isinstance(records, list):
        records = [records]
    rows = [r if isinstance(r, dict) else {"value": r} for r in records]
    columns = list(dict.fromkeys(key for row in rows for key in row))
    path = _documents_dir() / (_safe_name(file_name) + (".xlsx" if fmt == "xlsx" else ".csv"))
    if fmt == "xlsx":
        try:
            from openpyxl import Workbook
        except ImportError as exc:
            raise RuntimeError("Excel export needs openpyxl. Run: pip install openpyxl") from exc
        book = Workbook()
        sheet = book.active
        sheet.title = "Extracted Data"
        sheet.append(columns)
        for row in rows:
            sheet.append([row.get(column, "") for column in columns])
        book.save(path)
    else:
        with path.open("w", newline="", encoding="utf-8-sig") as output:
            writer = csv.DictWriter(output, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
    return str(path)


def _read_foreground_browser_page():
    """Copy readable text from the user's foreground Chromium page.

    Selenium cannot attach to an ordinary, already-running Brave tab. On
    Windows we can still read the active page using its normal Select All / Copy
    shortcuts. The clipboard's Unicode text is restored afterwards. This is a
    deliberately narrow fallback: it only reads the frontmost Brave/Chrome/Edge
    window and never clicks, types into, or navigates the page.
    """
    try:
        import win32api
        import win32clipboard
        import win32con
        import win32gui
        import win32process
        import psutil
    except ImportError:
        return {"ok": False, "message": "Reading the active browser page needs pywin32 on Windows."}

    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return {"ok": False, "message": "I cannot find an active window to read, Sir."}
    try:
        _thread, pid = win32process.GetWindowThreadProcessId(hwnd)
        process = psutil.Process(pid).name().lower()
    except Exception:
        process = ""
    if process not in {"brave.exe", "chrome.exe", "msedge.exe", "firefox.exe"}:
        return {"ok": False,
                "message": "Please bring the browser page you want read to the front, then try again, Sir."}

    previous_text = None
    had_text = False
    try:
        win32clipboard.OpenClipboard()
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            previous_text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            had_text = True
        win32clipboard.CloseClipboard()

        def shortcut(key):
            win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
            win32api.keybd_event(key, 0, 0, 0)
            win32api.keybd_event(key, 0, win32con.KEYEVENTF_KEYUP, 0)
            win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)

        shortcut(ord("A"))
        shortcut(ord("C"))
        time.sleep(0.2)
        win32clipboard.OpenClipboard()
        text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        win32clipboard.EmptyClipboard()
        if had_text:
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, previous_text)
        win32clipboard.CloseClipboard()
    except Exception as exc:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass
        return {"ok": False, "message": f"I could not read the active browser page: {exc}"}

    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return {"ok": False, "message": "I could not find readable text on the active page, Sir."}
    return {"ok": True,
            "page": {"title": win32gui.GetWindowText(hwnd), "url": "", "text": text[:900]},
            "message": "Read the active browser page."}


def _click_foreground_browser_control(target):
    """Click an accessible control in the foreground browser window.

    Chromium exposes page buttons, links, and inputs through Windows UI
    Automation. Unlike screenshot/coordinate clicking, this follows the
    control's accessible name and remains reliable when the page is resized.
    """
    try:
        # comtypes otherwise tries to generate UIA bindings inside the Python
        # package directory, which is commonly read-only for Store Python.
        import comtypes.client
        generated = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "Ron" / "uiautomation_gen"
        generated.mkdir(parents=True, exist_ok=True)
        comtypes.client.gen_dir = str(generated)
        import uiautomation as auto
        import win32gui
        import win32process
        import psutil
    except ImportError:
        return {"ok": False,
                "message": "Foreground browser control needs uiautomation. Run: pip install uiautomation"}
    needle = str(target or "").strip().casefold()
    needles = {needle, re.sub(r"\s+(?:button|link|field|input)$", "", needle).strip()}
    needles.discard("")
    if not needle:
        return {"ok": False, "message": "Tell me which visible button or link to click, Sir."}
    hwnd = win32gui.GetForegroundWindow()
    try:
        _thread, pid = win32process.GetWindowThreadProcessId(hwnd)
        process = psutil.Process(pid).name().lower()
    except Exception:
        process = ""
    if process not in {"brave.exe", "chrome.exe", "msedge.exe", "firefox.exe"}:
        return {"ok": False,
                "message": "Please bring the browser page containing that control to the front, then try again, Sir."}
    try:
        root = auto.ControlFromHandle(hwnd)
        # Exact accessible-name matches are preferred. A second pass allows
        # common natural-language requests such as "find a repository button".
        def matches(control, partial=False):
            name = str(getattr(control, "Name", "") or "").strip().casefold()
            kind = str(getattr(control, "ControlTypeName", "") or "").lower()
            if kind not in {"buttoncontrol", "hyperlinkcontrol", "editcontrol"}:
                return False
            return any((query in name if partial else query == name) for query in needles)

        def walk(control, partial=False, depth=0, seen=None):
            """Bounded tree walk; uiautomation 2.x has no FindControl API."""
            if depth > 30 or (seen is not None and seen[0] >= 3000):
                return None
            if seen is not None:
                seen[0] += 1
            if matches(control, partial):
                return control
            for child in control.GetChildren():
                found = walk(child, partial, depth + 1, seen)
                if found:
                    return found
            return None

        control = walk(root, seen=[0]) or walk(root, partial=True, seen=[0])
        if not control:
            return {"ok": False, "message": f"I could not find a visible control named {target}, Sir."}
        control.Click(simulateMove=False)
        return {"ok": True, "page": {"title": win32gui.GetWindowText(hwnd), "url": "", "text": ""},
                "message": f"Clicked {getattr(control, 'Name', target)}, Sir."}
    except Exception as exc:
        return {"ok": False, "message": f"I could not click that browser control: {exc}"}


def _switch_foreground_browser_tab(direction="next", index=None):
    """Switch tabs in the frontmost supported browser using standard shortcuts."""
    try:
        import win32api
        import win32con
        import win32gui
        import win32process
        import psutil
    except ImportError:
        return {"ok": False, "message": "Browser tab switching needs pywin32 on Windows."}
    hwnd = win32gui.GetForegroundWindow()
    try:
        _thread, pid = win32process.GetWindowThreadProcessId(hwnd)
        process = psutil.Process(pid).name().lower()
    except Exception:
        process = ""
    if process not in {"brave.exe", "chrome.exe", "msedge.exe", "firefox.exe"}:
        return {"ok": False,
                "message": "Please bring the browser whose tab you want to switch to the front, Sir."}

    try:
        if index is not None:
            number = int(index)
            # Chromium/Firefox reserve Ctrl+1 through Ctrl+8 for those tab
            # positions, but Ctrl+9 means *last* rather than ninth. For tab 10+
            # start at tab 1, then advance in browser order.
            if number < 1 or number > 50:
                return {"ok": False, "message": "Please choose a tab number from 1 through 50, Sir."}
            key = ord("1")
            win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
            win32api.keybd_event(key, 0, 0, 0)
            win32api.keybd_event(key, 0, win32con.KEYEVENTF_KEYUP, 0)
            win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
            for _ in range(number - 1):
                win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
                win32api.keybd_event(win32con.VK_TAB, 0, 0, 0)
                win32api.keybd_event(win32con.VK_TAB, 0, win32con.KEYEVENTF_KEYUP, 0)
                win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
            return {"ok": True, "message": f"Switched to tab {number}, Sir."}

        previous = str(direction or "next").lower() in {"previous", "prev", "back"}
        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
        if previous:
            win32api.keybd_event(win32con.VK_SHIFT, 0, 0, 0)
        win32api.keybd_event(win32con.VK_TAB, 0, 0, 0)
        win32api.keybd_event(win32con.VK_TAB, 0, win32con.KEYEVENTF_KEYUP, 0)
        if previous:
            win32api.keybd_event(win32con.VK_SHIFT, 0, win32con.KEYEVENTF_KEYUP, 0)
        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
        return {"ok": True, "message": f"Switched to the {'previous' if previous else 'next'} tab, Sir."}
    except Exception as exc:
        return {"ok": False, "message": f"I could not switch the browser tab: {exc}"}


def run(action, **data):
    """Execute one verified browser action and return a serialisable result."""
    global _last_records
    action = str(action or "").lower().strip()
    target = data.get("target") or data.get("selector") or data.get("text") or ""
    supported = {"open", "navigate", "search", "back", "forward", "reload", "click",
                 "fill", "type", "select", "scroll", "read", "inspect", "extract", "switch_tab",
                 "screenshot", "download", "upload", "save_csv", "save_excel"}
    if action not in supported:
        return {"ok": False, "message": f"Unsupported browser action: {action}."}
    if action in {"click", "upload"} and _requires_confirmation(action, target) and not data.get("confirmed"):
        return {"ok": False, "needs_confirmation": True,
                "message": "This action may have an external effect. Please explicitly confirm it first."}
    if action == "upload" and not data.get("confirmed"):
        return {"ok": False, "needs_confirmation": True,
                "message": "Uploading shares a local file. Please explicitly confirm the upload."}
    try:
        with _lock:
            if action in {"save_csv", "save_excel"}:
                path = _save_records(data.get("records") or _last_records, data.get("file_name"),
                                     "xlsx" if action == "save_excel" else "csv")
                return {"ok": True, "file": path, "message": f"Saved extracted data to {path}."}

            # Clean up stale/closed browser session if window was closed by user
            if _driver is not None and not _is_driver_alive(_driver):
                close()

            # Reading or changing a page must never surprise the user by opening
            # a fresh browser window. Browser automation can only operate on a
            # page Ron has already opened in this process; ``open``, ``navigate``
            # and ``search`` are the deliberate session-starting actions.
            if _driver is None and action == "read":
                return _read_foreground_browser_page()
            if _driver is None and action == "click":
                return _click_foreground_browser_control(target)
            if action == "switch_tab":
                return _switch_foreground_browser_tab(data.get("direction"), data.get("index"))
            if _driver is None and action not in {"open", "navigate", "search"}:
                return {
                    "ok": False,
                    "message": ("I do not have a Ron-controlled browser page yet, Sir. "
                                "Ask me to open the site first, then I can read or control it."),
                }
            driver = _get_driver()
            _webdriver, _Options, By, Keys, Select = _load_selenium()
            if action in {"open", "navigate", "search"}:
                value = data.get("url") or data.get("query") or ""
                if action == "search":
                    from urllib.parse import quote_plus
                    value = "https://www.google.com/search?q=" + quote_plus(str(value))
                elif not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", str(value)):
                    value = "https://" + str(value)
                try:
                    driver.get(value)
                except Exception as exc:
                    err = str(exc).lower()
                    if any(term in err for term in ["invalid session id", "disconnected", "closed the connection", "no such window"]):
                        close()
                        driver = _get_driver(force_recreate=True)
                        driver.get(value)
                    else:
                        raise
            elif action == "back":
                driver.back()
            elif action == "forward":
                driver.forward()
            elif action == "reload":
                driver.refresh()
            elif action == "click":
                _element(driver, target).click()
            elif action in {"fill", "type"}:
                field = _element(driver, target)
                if action == "fill":
                    field.clear()
                field.send_keys(str(data.get("value") or data.get("text") or ""))
            elif action == "select":
                Select(_element(driver, target)).select_by_visible_text(str(data.get("value") or ""))
            elif action == "scroll":
                direction = str(data.get("direction") or "down").lower()
                amount = int(data.get("amount") or 700)
                driver.execute_script("window.scrollBy(0, arguments[0]);", -amount if direction == "up" else amount)
            elif action == "read":
                return {"ok": True, "page": _page_summary(driver), "message": "Read the visible page content."}
            elif action == "inspect":
                source = driver.page_source
                return {"ok": True, "page": _page_summary(driver), "html": source[:12000],
                        "message": "Inspected the current page structure."}
            elif action == "extract":
                elements = driver.find_elements(By.CSS_SELECTOR, str(target or "body"))
                records = [{"text": re.sub(r"\s+", " ", item.text).strip(),
                            "href": item.get_attribute("href") or None}
                           for item in elements if item.text.strip() or item.get_attribute("href")]
                _last_records = records
                return {"ok": True, "records": records[:100], "count": len(records),
                        "message": f"Extracted {len(records)} records."}
            elif action == "screenshot":
                path = _documents_dir() / (_safe_name(data.get("file_name"), "screenshot") + ".png")
                driver.save_screenshot(str(path))
                return {"ok": True, "file": str(path), "message": f"Saved screenshot to {path}."}
            elif action == "download":
                _element(driver, target).click()
            elif action == "upload":
                path = Path(str(data.get("file_path") or ""))
                if not path.is_file():
                    raise ValueError("The requested upload file does not exist.")
                _element(driver, target).send_keys(str(path.resolve()))
            time.sleep(0.25)
            return {"ok": True, "page": _page_summary(driver), "message": f"Browser action '{action}' completed."}
    except Exception as exc:
        err_msg = str(exc)
        low_err = err_msg.lower()
        if any(term in low_err for term in [
            "invalid session id",
            "disconnected",
            "no such window",
            "closed the connection",
            "devtools",
            "session deleted",
        ]):
            close()
            return {
                "ok": False,
                "message": "The browser window was closed or disconnected. I have reset the session, Sir. Please repeat your request to reopen it.",
            }
        return {"ok": False, "message": f"Browser action failed: {exc}"}


def close():
    """Close Ron's automation browser, if it was started."""
    global _driver, _profile_dir
    with _lock:
        if _driver is not None:
            try:
                _driver.quit()
            except Exception:
                pass
            _driver = None
        if _profile_dir:
            try:
                import shutil
                shutil.rmtree(_profile_dir, ignore_errors=True)
            except Exception:
                pass
            _profile_dir = None
