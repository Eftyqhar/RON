"""Headless render check for the HUD (development utility).

    python test_ui_render.py [--port 8791] [--state listening]

Loads the interface in headless Chrome against a running `ui_server.py`,
fails on any JavaScript console error, verifies that the live panels actually
received data, and writes a PNG so the layout can be eyeballed.
"""

import argparse
import json
import os
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.edge.options import Options as EdgeOptions

HERE = os.path.dirname(os.path.abspath(__file__))
DRIVER = os.path.join(HERE, "chromedriver.exe")

COMMON_ARGS = (
    "--headless=new",
    "--hide-scrollbars",
    "--force-device-scale-factor=1",
    # Headless falls back to SwiftShader; without this the canvas layers and
    # backdrop-filter panels can come back blank.
    "--enable-unsafe-swiftshader",
)


def _launch(size):
    """Open whatever Chromium is on this machine.

    The bundled chromedriver.exe only matches the Chrome it shipped with, so
    Edge (driver resolved by Selenium Manager) is tried as well -- both render
    the interface identically, since it is the same engine underneath.
    """
    attempts = []

    def chrome():
        opts = ChromeOptions()
        for a in COMMON_ARGS:
            opts.add_argument(a)
        opts.add_argument(f"--window-size={size[0]},{size[1]}")
        opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
        service = ChromeService(DRIVER) if os.path.isfile(DRIVER) else ChromeService()
        return webdriver.Chrome(service=service, options=opts)

    def edge():
        opts = EdgeOptions()
        for a in COMMON_ARGS:
            opts.add_argument(a)
        opts.add_argument(f"--window-size={size[0]},{size[1]}")
        opts.set_capability("ms:loggingPrefs", {"browser": "ALL"})
        return webdriver.Edge(options=opts)

    for name, factory in (("chrome", chrome), ("edge", edge)):
        try:
            driver = factory()
            print(f"[browser: {name} {driver.capabilities.get('browserVersion')}]")
            return driver
        except Exception as e:
            attempts.append(f"{name}: {str(e).splitlines()[0]}")
    raise SystemExit("Could not start a headless browser.\n  " + "\n  ".join(attempts))


def render(port, size, state, out):
    driver = _launch(size)
    try:
        driver.set_window_size(*size)
        driver.get(f"http://127.0.0.1:{port}/")
        time.sleep(3.0)          # let SSE hydrate, boot overlay clear, fonts load

        if state:
            # Drive the state machine directly so every visual state can be
            # inspected without waiting for RON to reach it.
            driver.execute_script("setState(arguments[0], 'RENDER CHECK');", state)
            time.sleep(1.2)

        report = driver.execute_script("""
          const q = (s) => document.querySelector(s);
          const cs = getComputedStyle(document.documentElement);
          return {
            state: document.documentElement.dataset.state,
            link: document.documentElement.dataset.link,
            coreState: q('#core-state').textContent,
            cpu: q('#meter-cpu b').textContent,
            ram: q('#meter-ram b').textContent,
            gpu: q('#meter-gpu b').textContent,
            disk: q('#meter-disk b').textContent,
            net: q('#net-state').textContent,
            host: q('#host').textContent,
            uptime: q('#uptime').textContent,
            model: q('#model-name').textContent,
            micDevice: q('#mic-device').textContent,
            modules: [...document.querySelectorAll('.modules li')]
                       .map(li => `${li.dataset.key}:${li.dataset.ok}:${li.querySelector('b').textContent}`),
            bootHidden: q('#boot').classList.contains('done'),
            radialBars: q('#radial-bars').children.length,
            amp: cs.getPropertyValue('--amp').trim(),
            spin: cs.getPropertyValue('--spin').trim(),
            accent: cs.getPropertyValue('--accent-rgb').trim(),
            coreBox: (() => { const r = q('.core').getBoundingClientRect();
                              return [Math.round(r.width), Math.round(r.height)]; })(),
            waveBox: (() => { const r = q('#wave-canvas').getBoundingClientRect();
                              return [Math.round(r.width), Math.round(r.height)]; })(),
            overflow: [document.documentElement.scrollWidth - window.innerWidth,
                       document.documentElement.scrollHeight - window.innerHeight],
            wavePainted: (() => {
              const c = q('#wave-canvas');
              const g = c.getContext('2d');
              const d = g.getImageData(0, 0, c.width, c.height).data;
              let lit = 0;
              for (let i = 3; i < d.length; i += 4) if (d[i] > 8) lit++;
              return lit;
            })(),
            bgPainted: (() => {
              const c = q('#bg-canvas');
              const g = c.getContext('2d');
              const d = g.getImageData(0, 0, c.width, c.height).data;
              let lit = 0;
              for (let i = 3; i < d.length; i += 4) if (d[i] > 4) lit++;
              return lit;
            })(),
          };
        """)

        driver.save_screenshot(out)

        errors = [e for e in driver.get_log("browser") if e["level"] == "SEVERE"]
        return report, errors
    finally:
        driver.quit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--state", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    w, h = (int(n) for n in args.size.lower().split("x"))
    out = args.out or f"ui_render_{w}x{h}{('_' + args.state) if args.state else ''}.png"

    report, errors = render(args.port, (w, h), args.state, out)
    print(json.dumps(report, indent=2))
    print(f"\nscreenshot -> {out}")

    if errors:
        print("\nCONSOLE ERRORS:")
        for e in errors:
            print(" ", e["message"][:400])
        return 1

    problems = []
    if report["link"] != "up":
        problems.append("SSE link is not up")
    if not report["bootHidden"]:
        problems.append("boot overlay never cleared")
    if report["radialBars"] != 64:
        problems.append(f"radial collar has {report['radialBars']} bars")
    if report["wavePainted"] < 500:
        problems.append(f"waveform canvas looks blank ({report['wavePainted']} px)")
    if report["bgPainted"] < 500:
        problems.append(f"background canvas looks blank ({report['bgPainted']} px)")
    if any(v > 1 for v in report["overflow"]):
        problems.append(f"page overflows the viewport by {report['overflow']}")
    if "N/A" in (report["cpu"], report["ram"]):
        problems.append("cpu/ram telemetry did not arrive")

    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(" -", p)
        return 1
    print("\nNo console errors; panels are live.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
