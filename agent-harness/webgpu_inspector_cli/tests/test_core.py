"""Unit tests for WebGPU Inspector CLI core modules."""

import os
import sys
import json
import pytest
from pathlib import Path


# --- Session Tests ---

class TestSession:
    def setup_method(self):
        from webgpu_inspector_cli.core.session import Session
        self.session = Session()

    def test_push_pop_shader_edit(self):
        self.session.push_shader_edit(1, "original code")
        result = self.session.pop_shader_edit(1)
        assert result == "original code"

    def test_pop_empty_returns_none(self):
        result = self.session.pop_shader_edit(999)
        assert result is None

    def test_clear_shader_edits(self):
        self.session.push_shader_edit(1, "v1")
        self.session.push_shader_edit(1, "v2")
        self.session.clear_shader_edits(1)
        assert not self.session.has_shader_edits(1)

    def test_has_shader_edits(self):
        assert not self.session.has_shader_edits(1)
        self.session.push_shader_edit(1, "code")
        assert self.session.has_shader_edits(1)

    def test_multiple_edits_stack(self):
        """Edits should pop in LIFO order."""
        self.session.push_shader_edit(1, "v1")
        self.session.push_shader_edit(1, "v2")
        self.session.push_shader_edit(1, "v3")
        assert self.session.pop_shader_edit(1) == "v3"
        assert self.session.pop_shader_edit(1) == "v2"
        assert self.session.pop_shader_edit(1) == "v1"
        assert self.session.pop_shader_edit(1) is None

    def test_separate_shader_histories(self):
        """Different shader IDs should have independent histories."""
        self.session.push_shader_edit(1, "shader1_v1")
        self.session.push_shader_edit(2, "shader2_v1")
        assert self.session.pop_shader_edit(1) == "shader1_v1"
        assert self.session.pop_shader_edit(2) == "shader2_v1"


# --- Bridge Path Resolution Tests ---

class TestBridgePaths:
    def test_find_inspector_js(self):
        from webgpu_inspector_cli.core.bridge import _find_inspector_js
        path = _find_inspector_js()
        assert path.exists()
        assert path.name == "webgpu_inspector_loader.js"

    def test_find_collector_js(self):
        from webgpu_inspector_cli.core.bridge import _find_collector_js
        path = _find_collector_js()
        assert path.exists()
        assert path.name == "collector.js"

    def test_bridge_not_connected(self):
        from webgpu_inspector_cli.core.bridge import Bridge
        bridge = Bridge()
        assert not bridge.is_connected
        with pytest.raises(RuntimeError, match="No browser session"):
            bridge.query("getSummary")

    def test_bridge_require_not_connected(self):
        from webgpu_inspector_cli.core.bridge import require_bridge, Bridge
        import webgpu_inspector_cli.core.bridge as bridge_mod
        # Reset global singleton
        bridge_mod._bridge = Bridge()
        with pytest.raises(RuntimeError, match="No active browser session"):
            require_bridge()
        bridge_mod._bridge = None

    def test_loader_bootstrap_sets_session_storage_flag(self):
        """Regression for v0.2.3: the loader bootstrap MUST set
        WEBGPU_INSPECTOR_LOADED='true' before injecting the loader. Without
        this, the loader's auto-init is skipped (it just registers a
        start_inspection event listener) and the inspector's prototype hooks
        never install in time to catch emscripten's first GPUQueue.submit."""
        from webgpu_inspector_cli.core.bridge import Bridge
        bs = Bridge()._build_loader_bootstrap()
        assert "WEBGPU_INSPECTOR_LOADED" in bs
        assert "'true'" in bs

    def test_loader_bootstrap_defers_until_documentElement(self):
        """Regression for v0.2.3: the loader bootstrap MUST wait for
        document.documentElement before injecting the loader. Playwright's
        add_init_script runs BEFORE <html> is parsed, so an immediate loader
        execution crashes inside `MutationObserver.observe(null, ...)`.
        Yielding via setTimeout(0) (NOT queueMicrotask, which would starve
        the parser) gives the parser a chance to create documentElement."""
        from webgpu_inspector_cli.core.bridge import Bridge
        bs = Bridge()._build_loader_bootstrap()
        assert "documentElement" in bs
        # Must yield to the parser via setTimeout, not queueMicrotask (which
        # would starve the parser and produce an infinite loop).
        assert "setTimeout" in bs
        assert "queueMicrotask" not in bs

    def test_find_guard_js(self):
        from webgpu_inspector_cli.core.bridge import _find_guard_js
        path = _find_guard_js()
        assert path.exists()
        assert path.name == "wgi_guard.js"

    def test_guard_bootstrap_shape(self):
        """v0.3.0: the guard bootstrap must reference __wgiOptions, expose
        __wgi.stats, install Object.defineProperty traps, and run as an
        IIFE so it can be safely concatenated with any other init scripts."""
        from webgpu_inspector_cli.core.bridge import Bridge
        guard = Bridge()._build_guard_bootstrap()
        assert "__wgiOptions" in guard
        assert "__wgi" in guard
        assert "Object.defineProperty" in guard
        assert "maxHooksPerWindow" in guard
        assert "droppedDueToBurst" in guard

    def test_guard_js_has_burst_guard_contract(self):
        """The guard JS must expose the documented contract: it reads
        window.__wgiOptions, traps the GPUDevice/GPUQueue methods that the
        crash report identified, exposes window.__wgi.stats(), and tracks
        droppedDueToBurst counts. Pin this so accidental refactors don't
        break the page-side API documented in README."""
        from webgpu_inspector_cli.core.bridge import _find_guard_js
        guard = _find_guard_js().read_text()
        for needle in [
            "__wgiOptions",
            "__wgi.stats",
            "Object.defineProperty",
            "maxHooksPerWindow",
            "windowMs",
            "droppedDueToBurst",
            "createBuffer",
            "createBindGroup",
            "writeBuffer",
        ]:
            assert needle in guard, f"guard contract missing: {needle}"

    def test_inspector_opt_out_skips_injection(self):
        """v0.3.0: Bridge.launch(inspector=False) must NOT call
        add_init_script for the guard or loader, and must NOT call _inject().
        We stub the Playwright pieces so the test can run without a real
        browser."""
        from webgpu_inspector_cli.core.bridge import Bridge

        init_script_calls = []
        inject_calls = []

        class _StubContext:
            def add_init_script(self, payload):
                init_script_calls.append(payload)
            def new_page(self):
                return _StubPage()
            def close(self):
                pass

        class _StubPage:
            def is_closed(self):
                return False
            def goto(self, url, wait_until=None):
                pass
            def evaluate(self, *args, **kwargs):
                return None
            def close(self):
                pass
            def on(self, *args, **kwargs):
                pass

        class _StubBrowser:
            def new_context(self):
                return _StubContext()
            def close(self):
                pass

        class _StubChromium:
            def launch(self, **kwargs):
                return _StubBrowser()
            def launch_persistent_context(self, **kwargs):
                return _StubContext()

        class _StubPlaywright:
            chromium = _StubChromium()
            def stop(self):
                pass

        b = Bridge()
        # Replace the playwright start so launch() doesn't spawn a real browser.
        import webgpu_inspector_cli.core.bridge as bridge_mod
        original_start = bridge_mod.sync_playwright

        def fake_start():
            class _CM:
                def start(self):
                    return _StubPlaywright()
            return _CM()

        bridge_mod.sync_playwright = fake_start
        # Also stub _inject so we can confirm it's never called.
        original_inject = b._inject
        b._inject = lambda: inject_calls.append(True)
        try:
            b.launch("about:blank", inspector=False)
        finally:
            bridge_mod.sync_playwright = original_start
            b._inject = original_inject

        assert init_script_calls == [], (
            "inspector=False must skip add_init_script calls; got "
            f"{len(init_script_calls)} call(s)"
        )
        assert inject_calls == [], (
            "inspector=False must skip _inject(); got "
            f"{len(inject_calls)} call(s)"
        )
        assert b._inspector_enabled is False

    def test_inspector_default_logs_warning(self, capsys):
        """v0.3.0: when the inspector is active (default), the launch path
        must emit a one-line stderr warning so users know WGI is injected
        and how to disable it. The warning is what makes opt-out
        discoverable; if it's silent, users won't find the flag until they
        crash their machine."""
        from webgpu_inspector_cli.core.bridge import Bridge
        b = Bridge()
        b._inspector_enabled = True
        b._emit_active_warning()
        captured = capsys.readouterr()
        assert "WGI hooks active" in captured.err
        assert "inspector=False" in captured.err


# --- Format Helpers Tests ---

class TestFormatHelpers:
    def test_format_bytes_zero(self):
        from webgpu_inspector_cli.commands.objects import _format_bytes
        assert _format_bytes(0) == "0 B"

    def test_format_bytes_small(self):
        from webgpu_inspector_cli.commands.objects import _format_bytes
        assert _format_bytes(512) == "512 B"

    def test_format_bytes_kb(self):
        from webgpu_inspector_cli.commands.objects import _format_bytes
        result = _format_bytes(2048)
        assert "KB" in result

    def test_format_bytes_mb(self):
        from webgpu_inspector_cli.commands.objects import _format_bytes
        result = _format_bytes(4608000)
        assert "MB" in result

    def test_format_bytes_none(self):
        from webgpu_inspector_cli.commands.objects import _format_bytes
        assert _format_bytes(None) == "0 B"


# --- Collector JS Content Tests ---

class TestCollectorJS:
    def test_collector_js_exists_and_valid(self):
        from webgpu_inspector_cli.core.bridge import _find_collector_js
        path = _find_collector_js()
        content = path.read_text()
        # Check it defines window.__wgi
        assert "window.__wgi" in content
        # Check it handles key actions
        assert "AddObject" in content
        assert "DeleteObject" in content
        assert "ValidationError" in content
        assert "DeltaTime" in content
        assert "CaptureFrameResults" in content

    def test_collector_has_query_api(self):
        from webgpu_inspector_cli.core.bridge import _find_collector_js
        content = _find_collector_js().read_text()
        # Check all query functions exist
        for fn in ["getObjects", "getObject", "getErrors", "getFrameRate",
                    "getMemoryUsage", "getSummary", "requestCapture",
                    "getCaptureStatus", "getShaderCode", "compileShader",
                    "revertShader", "getCapturedBufferStatus"]:
            assert fn in content, f"Missing query function: {fn}"

    def test_collector_writes_capture_session_storage(self):
        """The capture-frame fix: collector must write WEBGPU_INSPECTOR_CAPTURE_FRAME
        sessionStorage so the main-thread inspector picks it up at the next rAF
        (webgpu_inspector.js:1382 polls this every frame)."""
        from webgpu_inspector_cli.core.bridge import _find_collector_js
        content = _find_collector_js().read_text()
        assert "WEBGPU_INSPECTOR_CAPTURE_FRAME" in content, (
            "Collector must write the capture key to sessionStorage "
            "so main-thread captures actually start"
        )

    def test_collector_resolves_buffer_data_by_bind_group(self):
        """Buffer data arrives keyed by (commandId, entryIndex). The collector
        must walk captured commands' setBindGroup args to resolve back to a
        buffer id."""
        from webgpu_inspector_cli.core.bridge import _find_collector_js
        content = _find_collector_js().read_text()
        for marker in ["capturedBufferChunks", "capturedAllCommands", "setBindGroup"]:
            assert marker in content, f"Missing collector marker: {marker}"


# --- CLI Entry Point Tests ---

class TestCLIStructure:
    def test_cli_import(self):
        from webgpu_inspector_cli.webgpu_inspector_cli import cli
        assert cli is not None

    def test_cli_has_commands(self):
        from webgpu_inspector_cli.webgpu_inspector_cli import cli
        command_names = set(cli.commands.keys())
        expected = {"browser", "objects", "capture", "shaders", "errors", "status", "repl"}
        assert expected.issubset(command_names)
