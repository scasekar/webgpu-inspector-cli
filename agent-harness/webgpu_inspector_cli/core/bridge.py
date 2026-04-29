"""Playwright CDP bridge for WebGPU Inspector injection and communication."""

import json
import os
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, Browser, Page, BrowserContext


def _find_inspector_js():
    """Locate the built webgpu_inspector_loader.js from the submodule."""
    # Walk up from this file to find the repo root
    pkg_dir = Path(__file__).resolve().parent.parent  # webgpu_inspector_cli/
    repo_root = pkg_dir.parent.parent  # webgpu-inspector-cli/
    loader_path = repo_root / "webgpu_inspector" / "extensions" / "chrome" / "webgpu_inspector_loader.js"
    if loader_path.exists():
        return loader_path
    raise FileNotFoundError(
        f"Could not find webgpu_inspector_loader.js at {loader_path}. "
        "Make sure the webgpu_inspector submodule is initialized: "
        "git submodule update --init"
    )


def _find_collector_js():
    """Locate the collector.js script bundled with this package."""
    js_dir = Path(__file__).resolve().parent.parent / "js"
    collector_path = js_dir / "collector.js"
    if collector_path.exists():
        return collector_path
    raise FileNotFoundError(f"Could not find collector.js at {collector_path}")


class Bridge:
    """Manages browser lifecycle, inspector injection, and communication."""

    def __init__(self):
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._inspector_injected = False
        self._console_log_file = None

    @property
    def page(self) -> Page | None:
        return self._page

    @property
    def is_connected(self) -> bool:
        return self._page is not None and not self._page.is_closed()

    def launch(
        self,
        url: str,
        headless: bool = False,
        gpu_backend: str | None = None,
        capture_console_path: str | None = None,
        user_data_dir: str | None = None,
    ):
        """Launch browser, navigate to URL, and inject the inspector.

        capture_console_path: if set, console messages are written to this file
            line-by-line. The listener is attached BEFORE navigation so page
            bootstrap logs are captured.
        user_data_dir: if set, Chrome runs with a persistent profile directory
            (cookies, localStorage, extensions). Useful when the target app
            depends on existing browser state.
        """
        self._playwright = sync_playwright().start()

        args = [
            "--enable-unsafe-webgpu",
            "--enable-features=Vulkan",
        ]
        if gpu_backend:
            args.append(f"--use-gl={gpu_backend}")

        if user_data_dir:
            # Persistent context: a single object that owns the browser lifetime.
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=headless,
                args=args,
            )
            self._browser = None  # No separate Browser object in this mode.
            # launch_persistent_context starts with one default page.
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        else:
            self._browser = self._playwright.chromium.launch(
                headless=headless,
                args=args,
            )
            self._context = self._browser.new_context()
            self._page = self._context.new_page()

        # Attach console capture BEFORE navigation so page-bootstrap logs are recorded.
        if capture_console_path:
            self._attach_console_capture(capture_console_path)

        self._page.goto(url, wait_until="domcontentloaded")
        self._inject()

    def navigate(self, url: str):
        """Navigate to a new URL and re-inject the inspector."""
        if not self.is_connected:
            raise RuntimeError("No browser session. Call launch() first.")
        self._inspector_injected = False
        self._page.goto(url, wait_until="domcontentloaded")
        self._inject()

    def close(self):
        """Shut down the browser and clean up resources."""
        if self._console_log_file:
            try:
                self._console_log_file.close()
            except Exception:
                pass
            self._console_log_file = None

        # `launch_persistent_context` returns only a context (no Browser).
        # `launch` + `new_context` returns both — closing the browser closes
        # the context too. Be defensive in either direction.
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None
        self._page = None
        self._inspector_injected = False

    def screenshot(self, output_path: str, full_page: bool = False) -> str:
        """Take a screenshot of the current page."""
        if not self.is_connected:
            raise RuntimeError("No browser session.")
        self._page.screenshot(path=output_path, full_page=full_page)
        return output_path

    def _inject(self):
        """Inject the inspector and collector scripts into the page."""
        if self._inspector_injected:
            return

        # 1. Inject the built webgpu_inspector_loader.js
        loader_js = _find_inspector_js().read_text()
        self._page.evaluate(loader_js)

        # 2. Inject our collector.js
        collector_js = _find_collector_js().read_text()
        self._page.evaluate(collector_js)

        # 3. Dispatch the start_inspection event to activate the inspector
        self._page.evaluate("""() => {
            window.dispatchEvent(new CustomEvent("__WebGPUInspector", {
                detail: {
                    __webgpuInspector: true,
                    action: "webgpu_inspector_start_inspection"
                }
            }));
        }""")

        self._inspector_injected = True

    def _attach_console_capture(self, path: str) -> None:
        """Open `path` for line-buffered writes and forward console events to it.

        Each line is `[<timestamp>] <JSON object>` so the file is grep-friendly
        but each entry is a single self-contained record. The previous format
        split pageerror events across multiple lines (one for `ErrorEvent`,
        one for the underlying exception); this one keeps name + message +
        stack together.
        """
        # Resolve and create parent dirs so users can pass relative paths.
        log_path = Path(path).expanduser().resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "w", buffering=1, encoding="utf-8")
        self._console_log_file = log_file

        def _ts() -> str:
            return datetime.utcnow().isoformat(timespec="milliseconds") + "Z"

        def _emit(record: dict) -> None:
            try:
                # Single JSON line per event so consumers can parse with
                # `jq -c '.'` or grep on a single field.
                log_file.write(f"[{record['timestamp']}] {json.dumps(record)}\n")
            except Exception:
                # Never let logging crash the page session.
                pass

        def on_console(msg) -> None:
            try:
                rec = {
                    "timestamp": _ts(),
                    "kind": "console",
                    "level": msg.type,
                    "text": msg.text,
                }
                try:
                    loc = msg.location
                    if loc and loc.get("url"):
                        rec["url"] = loc.get("url")
                        rec["line"] = loc.get("line_number")
                        rec["column"] = loc.get("column_number")
                except Exception:
                    pass
                _emit(rec)
            except Exception:
                pass

        def on_pageerror(exc) -> None:
            # Playwright passes the page error as the JS Error itself; common
            # attrs: message, name, stack. Coerce robustly.
            try:
                rec = {
                    "timestamp": _ts(),
                    "kind": "pageerror",
                    "message": getattr(exc, "message", None) or str(exc),
                    "name": getattr(exc, "name", None) or "Error",
                    "stack": getattr(exc, "stack", None),
                }
                _emit(rec)
            except Exception:
                pass

        self._page.on("console", on_console)
        self._page.on("pageerror", on_pageerror)

    def query(self, fn_name: str, *args) -> object:
        """Call a collector query function and return the result."""
        if not self.is_connected:
            raise RuntimeError("No browser session.")
        args_json = json.dumps(args)
        result = self._page.evaluate(f"() => window.__wgi.{fn_name}(...{args_json})")
        return result

    def send_action(self, action: str, data: dict | None = None):
        """Dispatch a PanelAction to the inspector running in the page."""
        if not self.is_connected:
            raise RuntimeError("No browser session.")
        message = {
            "__webgpuInspector": True,
            "action": action,
        }
        if data:
            message.update(data)
        self._page.evaluate("""(msg) => {
            window.dispatchEvent(new CustomEvent("__WebGPUInspector", {
                detail: msg
            }));
        }""", message)

    def wait_for_condition(self, js_expression: str, timeout: float = 30.0) -> object:
        """Wait for a JS expression to return a truthy value, then return it."""
        if not self.is_connected:
            raise RuntimeError("No browser session.")
        self._page.wait_for_function(js_expression, timeout=timeout * 1000)
        return self._page.evaluate(js_expression)

    def eval(self, js: str, *, await_promise: bool = False) -> object:
        """Run an arbitrary JS expression in the page context.

        If `js` is a bare expression, it is wrapped so its value is returned.
        If `await_promise` is true, the expression's resolved promise value is
        returned. Equivalent to Playwright's page.evaluate.
        """
        if not self.is_connected:
            raise RuntimeError("No browser session.")
        # Wrap the expression so users can pass either an expression
        # ("document.title") or a function body ("() => document.title").
        # Playwright accepts function-form strings directly; for bare
        # expressions, wrap.
        s = js.strip()
        is_function = s.startswith("(") or s.startswith("function") or s.startswith("async ")
        wrapped = js if is_function else f"() => ({js})"
        if await_promise:
            wrapped = (
                js
                if is_function
                else f"async () => (await ({js}))"
            )
        return self._page.evaluate(wrapped)

    def click(self, selector: str, timeout: float = 30.0) -> None:
        """Click an element matched by `selector` (CSS or Playwright locator)."""
        if not self.is_connected:
            raise RuntimeError("No browser session.")
        self._page.click(selector, timeout=timeout * 1000)

    def fill(self, selector: str, text: str, timeout: float = 30.0) -> None:
        """Type `text` into an input matched by `selector`."""
        if not self.is_connected:
            raise RuntimeError("No browser session.")
        self._page.fill(selector, text, timeout=timeout * 1000)

    def get_browser_info(self) -> dict:
        """Get browser and GPU information."""
        if not self.is_connected:
            raise RuntimeError("No browser session.")
        return self._page.evaluate("""() => {
            return {
                url: window.location.href,
                title: document.title,
                userAgent: navigator.userAgent,
                gpu: navigator.gpu ? 'available' : 'unavailable',
            };
        }""")


# Singleton bridge instance shared across CLI commands
_bridge: Bridge | None = None


def get_bridge() -> Bridge:
    """Get or create the global bridge instance."""
    global _bridge
    if _bridge is None:
        _bridge = Bridge()
    return _bridge


# Friendly multi-line message used by `require_bridge`. Tests assert that the
# string starts with "No active browser session" (substring match).
NO_SESSION_MESSAGE = (
    "No active browser session. Either:\n"
    "  • Run 'webgpu-inspector-cli browser launch --url <URL>' first, then your follow-up command in the SAME process (use REPL).\n"
    "  • Run 'webgpu-inspector-cli repl' for an interactive multi-step session.\n"
    "  • Run 'webgpu-inspector-mcp' to start the MCP server (Claude Code, Claude Desktop, Cursor) — recommended for agent use.\n"
    "Each bare CLI invocation starts a fresh process and a fresh browser, so 'browser launch' followed by a separate 'capture frame' will never share state."
)


def require_bridge() -> Bridge:
    """Get the bridge, raising a friendly error if no session is active."""
    bridge = get_bridge()
    if not bridge.is_connected:
        raise RuntimeError(NO_SESSION_MESSAGE)
    return bridge
