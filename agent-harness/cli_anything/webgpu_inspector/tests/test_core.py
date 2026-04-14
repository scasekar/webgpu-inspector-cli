"""Unit tests for WebGPU Inspector CLI core modules."""

import os
import sys
import json
import pytest
from pathlib import Path


# --- Session Tests ---

class TestSession:
    def setup_method(self):
        from cli_anything.webgpu_inspector.core.session import Session
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
        from cli_anything.webgpu_inspector.core.bridge import _find_inspector_js
        path = _find_inspector_js()
        assert path.exists()
        assert path.name == "webgpu_inspector_loader.js"

    def test_find_collector_js(self):
        from cli_anything.webgpu_inspector.core.bridge import _find_collector_js
        path = _find_collector_js()
        assert path.exists()
        assert path.name == "collector.js"

    def test_bridge_not_connected(self):
        from cli_anything.webgpu_inspector.core.bridge import Bridge
        bridge = Bridge()
        assert not bridge.is_connected
        with pytest.raises(RuntimeError, match="No browser session"):
            bridge.query("getSummary")

    def test_bridge_require_not_connected(self):
        from cli_anything.webgpu_inspector.core.bridge import require_bridge, Bridge
        import cli_anything.webgpu_inspector.core.bridge as bridge_mod
        # Reset global singleton
        bridge_mod._bridge = Bridge()
        with pytest.raises(RuntimeError, match="No active browser session"):
            require_bridge()
        bridge_mod._bridge = None


# --- Format Helpers Tests ---

class TestFormatHelpers:
    def test_format_bytes_zero(self):
        from cli_anything.webgpu_inspector.commands.objects import _format_bytes
        assert _format_bytes(0) == "0 B"

    def test_format_bytes_small(self):
        from cli_anything.webgpu_inspector.commands.objects import _format_bytes
        assert _format_bytes(512) == "512 B"

    def test_format_bytes_kb(self):
        from cli_anything.webgpu_inspector.commands.objects import _format_bytes
        result = _format_bytes(2048)
        assert "KB" in result

    def test_format_bytes_mb(self):
        from cli_anything.webgpu_inspector.commands.objects import _format_bytes
        result = _format_bytes(4608000)
        assert "MB" in result

    def test_format_bytes_none(self):
        from cli_anything.webgpu_inspector.commands.objects import _format_bytes
        assert _format_bytes(None) == "0 B"


# --- Collector JS Content Tests ---

class TestCollectorJS:
    def test_collector_js_exists_and_valid(self):
        from cli_anything.webgpu_inspector.core.bridge import _find_collector_js
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
        from cli_anything.webgpu_inspector.core.bridge import _find_collector_js
        content = _find_collector_js().read_text()
        # Check all query functions exist
        for fn in ["getObjects", "getObject", "getErrors", "getFrameRate",
                    "getMemoryUsage", "getSummary", "requestCapture",
                    "getCaptureStatus", "getShaderCode", "compileShader",
                    "revertShader"]:
            assert fn in content, f"Missing query function: {fn}"


# --- CLI Entry Point Tests ---

class TestCLIStructure:
    def test_cli_import(self):
        from cli_anything.webgpu_inspector.webgpu_inspector_cli import cli
        assert cli is not None

    def test_cli_has_commands(self):
        from cli_anything.webgpu_inspector.webgpu_inspector_cli import cli
        command_names = set(cli.commands.keys())
        expected = {"browser", "objects", "capture", "shaders", "errors", "status", "repl"}
        assert expected.issubset(command_names)
