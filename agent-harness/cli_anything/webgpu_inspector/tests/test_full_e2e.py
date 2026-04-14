"""E2E tests for WebGPU Inspector CLI.

These tests launch a real browser, navigate to test pages from the
webgpu_inspector submodule, and verify the inspector works end-to-end.
"""

import json
import os
import subprocess
import sys
import time
import pytest
from pathlib import Path

from cli_anything.webgpu_inspector.core.bridge import Bridge


def _get_test_page_url(page_name="triangle.html"):
    """Get file:// URL for a test page from the submodule."""
    pkg_dir = Path(__file__).resolve().parent.parent
    repo_root = pkg_dir.parent.parent.parent.parent
    test_dir = repo_root / "webgpu_inspector" / "test"
    page = test_dir / page_name
    if not page.exists():
        pytest.skip(f"Test page not found: {page}")
    return f"file://{page}"


def _resolve_cli(name):
    """Resolve installed CLI command; falls back to python -m for dev."""
    import shutil
    force = os.environ.get("CLI_ANYTHING_FORCE_INSTALLED", "").strip() == "1"
    path = shutil.which(name)
    if path:
        print(f"[_resolve_cli] Using installed command: {path}")
        return [path]
    if force:
        raise RuntimeError(f"{name} not found in PATH. Install with: pip install -e .")
    module = "cli_anything.webgpu_inspector.webgpu_inspector_cli"
    print(f"[_resolve_cli] Falling back to: {sys.executable} -m {module}")
    return [sys.executable, "-m", "cli_anything.webgpu_inspector"]


@pytest.fixture(scope="module")
def bridge():
    """Shared browser bridge for all E2E tests."""
    b = Bridge()
    url = _get_test_page_url("triangle.html")
    b.launch(url, headless=False)
    # Wait for WebGPU to initialize and a few frames to render
    time.sleep(3)
    yield b
    b.close()


# --- Browser Lifecycle ---

class TestBrowserLifecycle:
    def test_launch_and_close(self):
        """Test that we can launch and close a browser."""
        b = Bridge()
        url = _get_test_page_url("triangle.html")
        b.launch(url, headless=False)
        assert b.is_connected
        info = b.get_browser_info()
        assert info["gpu"] == "available"
        b.close()
        assert not b.is_connected


# --- Object Detection ---

class TestObjectDetection:
    def test_triangle_objects(self, bridge):
        """Triangle.html should create standard GPU objects."""
        objs = bridge.query("getObjects", None)
        assert len(objs) > 0
        types = {o["type"] for o in objs}
        # At minimum, triangle needs an adapter, device, shader, and pipeline
        assert "Adapter" in types
        assert "Device" in types
        assert "ShaderModule" in types
        assert "RenderPipeline" in types

    def test_texture_objects(self, bridge):
        """Triangle.html should have textures (canvas + depth)."""
        textures = bridge.query("getObjects", "Texture")
        assert len(textures) >= 1

    def test_object_has_descriptor(self, bridge):
        """Objects should include their descriptors."""
        objs = bridge.query("getObjects", None)
        device = next((o for o in objs if o["type"] == "Device"), None)
        assert device is not None
        assert device["descriptor"] is not None
        # Device descriptor should have limits
        assert "limits" in device["descriptor"]


# --- Shader Inspection ---

class TestShaderInspection:
    def test_shader_code(self, bridge):
        """Should retrieve WGSL shader source code."""
        shaders = bridge.query("getObjects", "ShaderModule")
        assert len(shaders) > 0
        shader_id = shaders[0]["id"]
        code = bridge.query("getShaderCode", shader_id)
        assert code is not None
        assert "@vertex" in code
        assert "@fragment" in code
        assert "vertexMain" in code


# --- Runtime Monitoring ---

class TestRuntimeMonitoring:
    def test_frame_rate(self, bridge):
        """FPS should be positive after a few frames."""
        fr = bridge.query("getFrameRate")
        assert fr["fps"] > 0
        assert fr["deltaTime"] > 0

    def test_memory_usage(self, bridge):
        """Texture memory should be tracked."""
        mem = bridge.query("getMemoryUsage")
        assert mem["totalTextureMemory"] > 0
        assert mem["totalMemory"] > 0

    def test_summary(self, bridge):
        """Summary should include all expected fields."""
        summary = bridge.query("getSummary")
        assert "objectCount" in summary
        assert "typeCounts" in summary
        assert "errorCount" in summary
        assert "fps" in summary
        assert "totalTextureMemory" in summary
        assert "totalBufferMemory" in summary
        assert summary["objectCount"] > 0

    def test_no_errors(self, bridge):
        """Triangle.html should produce no validation errors."""
        errors = bridge.query("getErrors")
        assert len(errors) == 0


# --- CLI Subprocess Tests ---

class TestCLISubprocess:
    CLI_BASE = _resolve_cli("cli-anything-webgpu-inspector")

    def _run(self, args, check=True):
        return subprocess.run(
            self.CLI_BASE + args,
            capture_output=True, text=True,
            check=check,
        )

    def test_help(self):
        result = self._run(["--help"])
        assert result.returncode == 0
        assert "WebGPU Inspector CLI" in result.stdout
        assert "browser" in result.stdout
        assert "objects" in result.stdout

    def test_browser_help(self):
        result = self._run(["browser", "--help"])
        assert result.returncode == 0
        assert "launch" in result.stdout
        assert "close" in result.stdout

    def test_objects_help(self):
        result = self._run(["objects", "--help"])
        assert result.returncode == 0
        assert "list" in result.stdout
        assert "inspect" in result.stdout

    def test_capture_help(self):
        result = self._run(["capture", "--help"])
        assert result.returncode == 0
        assert "frame" in result.stdout
        assert "texture" in result.stdout

    def test_shaders_help(self):
        result = self._run(["shaders", "--help"])
        assert result.returncode == 0
        assert "list" in result.stdout
        assert "view" in result.stdout

    def test_errors_help(self):
        result = self._run(["errors", "--help"])
        assert result.returncode == 0
        assert "list" in result.stdout
        assert "watch" in result.stdout

    def test_status_help(self):
        result = self._run(["status", "--help"])
        assert result.returncode == 0
        assert "summary" in result.stdout
        assert "fps" in result.stdout
