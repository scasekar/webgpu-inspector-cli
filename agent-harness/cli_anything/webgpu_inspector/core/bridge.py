"""Playwright CDP bridge for WebGPU Inspector injection and communication."""

import json
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, Browser, Page, BrowserContext


def _find_inspector_js():
    """Locate the built webgpu_inspector_loader.js from the submodule."""
    # Walk up from this file to find the repo root
    pkg_dir = Path(__file__).resolve().parent.parent  # cli_anything/webgpu_inspector/
    repo_root = pkg_dir.parent.parent.parent.parent  # webgpu-inspector-cli/
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

    @property
    def page(self) -> Page | None:
        return self._page

    @property
    def is_connected(self) -> bool:
        return self._page is not None and not self._page.is_closed()

    def launch(self, url: str, headless: bool = False, gpu_backend: str | None = None):
        """Launch browser, navigate to URL, and inject the inspector."""
        self._playwright = sync_playwright().start()

        args = [
            "--enable-unsafe-webgpu",
            "--enable-features=Vulkan",
        ]
        if gpu_backend:
            args.append(f"--use-gl={gpu_backend}")

        self._browser = self._playwright.chromium.launch(
            headless=headless,
            args=args,
        )
        self._context = self._browser.new_context()
        self._page = self._context.new_page()
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
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None
        self._page = None
        self._context = None
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
        """Wait for a JS expression to return a truthy value."""
        if not self.is_connected:
            raise RuntimeError("No browser session.")
        self._page.wait_for_function(js_expression, timeout=timeout * 1000)
        return self._page.evaluate(js_expression)

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


def require_bridge() -> Bridge:
    """Get the bridge, raising an error if no session is active."""
    bridge = get_bridge()
    if not bridge.is_connected:
        raise RuntimeError(
            "No active browser session. Run 'browser launch --url <URL>' first."
        )
    return bridge
