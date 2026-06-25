# -*- coding: utf-8 -*-
"""
tv_bridge.py — drive a Chrome-hosted TradingView chart over the Chrome DevTools
Protocol (CDP).

Zero heavy dependencies: uses `websocket-client` (already installed) + urllib only.

⚠️ UNOFFICIAL / FRAGILE. This automates the TradingView *web* UI through Chrome.
It relies on undocumented internal interfaces and will break whenever TradingView
ships a UI change. Use it locally, for personal research only. Keep the debug port
bound to localhost — an open CDP port is full control of that browser.

Honest capability map on tradingview.com (web):
  ROBUST   : attach, health check, run JS (evaluate), screenshots, navigate,
             set symbol / timeframe via the symbol-search keystrokes, read title.
  BEST-EFF : replay mode, alerts, switching layouts — done by simulating keystrokes
             / clicks; selectors & shortcuts can change.
  HARD/NO  : programmatic drawings and full Pine "external backtest" — TradingView's
             chart is a private canvas with no public DOM/JS API on tradingview.com
             (only self-hosted charting_library exposes that). We can open the Pine
             editor and type, but reading Strategy-Tester results is screen-scraping.

Typical use:
    from tv_bridge import TV, launch_chrome
    launch_chrome()                 # starts Chrome w/ debug port + opens TV (log in once)
    tv = TV(); tv.connect()
    print(tv.health())
    tv.set_symbol("NASDAQ:NVDA"); tv.set_timeframe("60")
    tv.screenshot("nvda.png")
"""
import os
import json
import time
import base64
import subprocess
import urllib.request

import websocket  # websocket-client

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]
DEBUG_PORT = 9222
USER_DATA = os.path.join(os.environ.get("LOCALAPPDATA", os.getcwd()), "tv_bridge_profile")
TV_CHART_URL = "https://www.tradingview.com/chart/"


def _chrome_path():
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    raise FileNotFoundError("Chrome/Edge not found in the standard locations.")


def launch_chrome(url=TV_CHART_URL, port=DEBUG_PORT, user_data=USER_DATA, headless=False):
    """Start a dedicated Chrome instance with the CDP debug port open.

    Uses its own user-data-dir so it won't clash with your normal Chrome and so the
    TradingView login persists across runs (log in once in this window).
    """
    os.makedirs(user_data, exist_ok=True)
    args = [
        _chrome_path(),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data}",
        "--remote-allow-origins=*",          # required for raw-CDP ws attach on modern Chrome
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if headless:
        args += ["--headless=new", "--disable-gpu", "--window-size=1600,900"]
    args.append(url)
    return subprocess.Popen(args)


def _targets(port=DEBUG_PORT):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5) as r:
        return json.load(r)


class TV:
    """A thin CDP client attached to one Chrome page (the TradingView tab)."""

    def __init__(self, port=DEBUG_PORT, match="tradingview.com"):
        self.port = port
        self.match = match
        self.ws = None
        self._id = 0
        self.target = None

    # ── attach / lifecycle ────────────────────────────────────────────
    def connect(self, timeout=20):
        """Find the TradingView page (or any page) and open a CDP websocket to it."""
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                pages = [t for t in _targets(self.port) if t.get("type") == "page"]
                tv = [t for t in pages if self.match in (t.get("url") or "")]
                self.target = (tv or pages or [None])[0]
                if self.target and self.target.get("webSocketDebuggerUrl"):
                    self.ws = websocket.create_connection(
                        self.target["webSocketDebuggerUrl"],
                        max_size=None, suppress_origin=True, timeout=15)
                    for dom in ("Page", "Runtime", "DOM"):
                        try:
                            self._cmd(f"{dom}.enable")
                        except Exception:
                            pass
                    return True
            except Exception as e:
                last = e
            time.sleep(0.5)
        raise RuntimeError(
            f"Could not attach to Chrome on :{self.port} ({last}). "
            "Start it with launch_chrome() (or chrome --remote-debugging-port).")

    def close(self):
        try:
            if self.ws:
                self.ws.close()
        finally:
            self.ws = None

    # ── raw CDP ───────────────────────────────────────────────────────
    def _cmd(self, method, **params):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        # drain until our reply; ignore async events
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    # ── workhorses ────────────────────────────────────────────────────
    def evaluate(self, expression, await_promise=False):
        """Run JS in the page and return the value (must be JSON-serialisable)."""
        r = self._cmd("Runtime.evaluate", expression=expression,
                       returnByValue=True, awaitPromise=await_promise)
        if r.get("exceptionDetails"):
            raise RuntimeError(r["exceptionDetails"].get("text", "JS error"))
        return r.get("result", {}).get("value")

    def screenshot(self, path, full_page=False):
        params = {"format": "png"}
        if full_page:
            params["captureBeyondViewport"] = True
        r = self._cmd("Page.captureScreenshot", **params)
        data = base64.b64decode(r["data"])
        with open(path, "wb") as f:
            f.write(data)
        return path

    def screenshot_bytes(self):
        r = self._cmd("Page.captureScreenshot", format="png")
        return base64.b64decode(r["data"])

    def navigate(self, url, wait=2.0):
        self._cmd("Page.navigate", url=url)
        time.sleep(wait)

    # ── input simulation ──────────────────────────────────────────────
    def _key(self, etype, key=None, code=None, vk=None, text=None):
        p = {"type": etype}
        if key:
            p["key"] = key
        if code:
            p["code"] = code
        if vk is not None:
            p["windowsVirtualKeyCode"] = vk
        if text is not None:
            p["text"] = text
        self._cmd("Input.dispatchKeyEvent", **p)

    def type_text(self, text, delay=0.03):
        for ch in str(text):
            self._key("keyDown", text=ch)
            self._key("keyUp", text=ch)
            time.sleep(delay)

    def press_enter(self):
        self._key("keyDown", key="Enter", code="Enter", vk=13)
        self._key("keyUp", key="Enter", code="Enter", vk=13)

    def press_escape(self):
        self._key("keyDown", key="Escape", code="Escape", vk=27)
        self._key("keyUp", key="Escape", code="Escape", vk=27)

    # ── TradingView-specific (best effort) ────────────────────────────
    def get_symbol(self):
        """Read the current symbol from the document title (e.g. 'NVDA …')."""
        try:
            return (self.evaluate("document.title") or "").split(" ")[0]
        except Exception:
            return None

    def set_symbol(self, symbol):
        """Open TV's symbol search (typing focuses it) and switch symbol."""
        self.type_text(symbol)
        time.sleep(0.8)
        self.press_enter()
        time.sleep(1.0)

    def set_timeframe(self, tf):
        """Switch interval: on TV, typing the resolution then Enter changes it
        (e.g. '60' = 1h, 'D' = daily, '5' = 5m)."""
        self.type_text(str(tf))
        time.sleep(0.4)
        self.press_enter()
        time.sleep(0.6)

    def open_chart(self, symbol, tf=None):
        """Load a symbol directly via URL (reliable; reloads the chart)."""
        self.navigate(f"https://www.tradingview.com/chart/?symbol={symbol}", wait=3.0)
        if tf:
            self.set_timeframe(tf)

    def health(self):
        """Connection + target health report."""
        out = {"chrome_up": False, "attached": bool(self.ws), "port": self.port}
        try:
            ts = _targets(self.port)
            pages = [x for x in ts if x.get("type") == "page"]
            tv = [x for x in pages if self.match in (x.get("url") or "")]
            out.update(chrome_up=True, pages=len(pages), tv_pages=len(tv),
                       url=(self.target or {}).get("url"))
            if self.ws:
                try:
                    out["js_ok"] = (self.evaluate("1+1") == 2)
                    out["title"] = self.evaluate("document.title")
                except Exception as e:
                    out["js_ok"] = False
                    out["js_error"] = str(e)
        except Exception as e:
            out["error"] = str(e)
        return out


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "health"
    if cmd == "launch":
        launch_chrome()
        print("Launched Chrome with debug port", DEBUG_PORT, "→ log in to TradingView once.")
    else:
        tv = TV()
        tv.connect()
        print(json.dumps(tv.health(), indent=2))
