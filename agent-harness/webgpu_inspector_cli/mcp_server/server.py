"""FastMCP server exposing the webgpu-inspector toolset over stdio.

Every tool is a thin wrapper over the same `Bridge` singleton the CLI uses, so
state persists for the lifetime of this process — the LLM client can launch a
browser, drive the page, capture frames, and read buffers without the browser
ever closing between calls. That is the core property the CLI's per-invocation
process model can't deliver.
"""

from __future__ import annotations

import base64
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from webgpu_inspector_cli.core.bridge import get_bridge
from webgpu_inspector_cli.utils import buffer_decoders


def _bridge_or_error() -> Any:
    """Return the Bridge if connected, else raise a friendly RuntimeError."""
    b = get_bridge()
    if not b.is_connected:
        raise RuntimeError(
            "No active browser session. Call browser_launch first."
        )
    return b


def build_server() -> FastMCP:
    """Construct and register every MCP tool. Separated from `main()` so tests
    can introspect the tool surface without spawning stdio."""
    mcp_app = FastMCP("webgpu-inspector")

    # --- browser ---

    @mcp_app.tool()
    def browser_launch(
        url: str,
        headless: bool = False,
        gpu_backend: str | None = None,
        capture_console_path: str | None = None,
        user_data_dir: str | None = None,
    ) -> dict:
        """Launch Chromium, navigate to `url`, and inject the WebGPU Inspector.

        - capture_console_path: if set, all browser console messages (and
          page errors) are written to this file. The listener is attached
          BEFORE navigation so page-bootstrap logs are captured.
        - user_data_dir: if set, Chromium uses a persistent profile (cookies,
          localStorage, extensions). Use when the target app needs existing
          browser state.
        """
        bridge = get_bridge()
        if bridge.is_connected:
            info = bridge.get_browser_info()
            return {"status": "already_active", **info}
        bridge.launch(
            url,
            headless=headless,
            gpu_backend=gpu_backend,
            capture_console_path=capture_console_path,
            user_data_dir=user_data_dir,
        )
        info = bridge.get_browser_info()
        return {
            "status": "launched",
            "url": info["url"],
            "title": info["title"],
            "gpu": info["gpu"],
            "consoleLog": capture_console_path,
            "userDataDir": user_data_dir,
        }

    @mcp_app.tool()
    def browser_close() -> dict:
        """Close the active browser session."""
        b = get_bridge()
        if not b.is_connected:
            return {"status": "no_session"}
        b.close()
        return {"status": "closed"}

    @mcp_app.tool()
    def browser_navigate(url: str) -> dict:
        """Navigate the active browser to a new URL and re-inject the inspector."""
        b = _bridge_or_error()
        b.navigate(url)
        info = b.get_browser_info()
        return {"status": "navigated", "url": info["url"], "title": info["title"]}

    @mcp_app.tool()
    def browser_screenshot(output_path: str, full_page: bool = False) -> dict:
        """Take a screenshot of the current page and save to `output_path`."""
        b = _bridge_or_error()
        path = b.screenshot(output_path, full_page=full_page)
        return {"status": "saved", "path": path}

    @mcp_app.tool()
    def browser_status() -> dict:
        """Return URL, title, and GPU availability for the active browser."""
        b = _bridge_or_error()
        return b.get_browser_info()

    @mcp_app.tool()
    def browser_eval(js: str, await_promise: bool = False) -> dict:
        """Run a JS expression or async function in the page context.

        Returns the JSON-serializable result. For multi-step or async work,
        pass an arrow function: '() => { ... }'. To wait on a promise, set
        `await_promise=True` and pass a promise-returning expression.
        """
        b = _bridge_or_error()
        result = b.eval(js, await_promise=await_promise)
        return {"result": result}

    @mcp_app.tool()
    def browser_click(selector: str, timeout_seconds: float = 30.0) -> dict:
        """Click a DOM element matching a CSS or Playwright selector."""
        b = _bridge_or_error()
        b.click(selector, timeout=timeout_seconds)
        return {"status": "clicked", "selector": selector}

    @mcp_app.tool()
    def browser_type(selector: str, text: str, timeout_seconds: float = 30.0) -> dict:
        """Type `text` into an input matching `selector` (replaces existing value)."""
        b = _bridge_or_error()
        b.fill(selector, text, timeout=timeout_seconds)
        return {"status": "typed", "selector": selector, "length": len(text)}

    @mcp_app.tool()
    def browser_wait(condition: str, timeout_seconds: float = 30.0) -> dict:
        """Block until a JS expression returns a truthy value, then return that value.

        Common pattern: wait for a global to be defined before driving the app.
        Example: `condition="window._scRenderer !== undefined"`.
        """
        b = _bridge_or_error()
        try:
            value = b.wait_for_condition(condition, timeout=timeout_seconds)
        except Exception as exc:
            return {"status": "timeout", "error": str(exc)}
        return {"status": "ready", "value": value}

    # --- objects ---

    def _enrich_buffer(obj: dict) -> dict:
        if obj.get("type") == "Buffer":
            desc = obj.get("descriptor") or {}
            usage = desc.get("usage") if isinstance(desc, dict) else None
            if not isinstance(usage, int):
                usage = obj.get("usage") if isinstance(obj.get("usage"), int) else None
            if usage is not None:
                obj["usageFlags"] = buffer_decoders.decode_buffer_usage(usage)
        return obj

    @mcp_app.tool()
    def objects_list(type: str | None = None) -> dict:
        """List GPU objects, optionally filtered by `type`.

        Buffer entries are enriched with a decoded `usageFlags` array
        (Storage, Indirect, CopyDst, etc.) so you can spot indirect-draw
        sources, uniform buffers, and writable storage targets at a glance.

        Valid types: Adapter, Device, Buffer, Texture, TextureView, Sampler,
        ShaderModule, BindGroup, BindGroupLayout, PipelineLayout,
        RenderPipeline, ComputePipeline, RenderBundle.
        """
        b = _bridge_or_error()
        result = b.query("getObjects", type)
        for o in result:
            _enrich_buffer(o)
        return {"objects": result, "count": len(result)}

    @mcp_app.tool()
    def objects_inspect(id: int) -> dict:
        """Return the full descriptor + creation stacktrace for a GPU object."""
        b = _bridge_or_error()
        obj = b.query("getObject", id)
        if obj is None:
            return {"error": f"Object {id} not found"}
        return _enrich_buffer(obj)

    @mcp_app.tool()
    def objects_search(label_substring: str) -> dict:
        """Find GPU objects by case-insensitive label substring match."""
        b = _bridge_or_error()
        all_objs = b.query("getObjects", None)
        needle = label_substring.lower()
        matched = [
            o for o in all_objs
            if o.get("label") and needle in o["label"].lower()
        ]
        return {"objects": matched, "count": len(matched)}

    @mcp_app.tool()
    def objects_memory() -> dict:
        """GPU memory breakdown (texture + buffer totals)."""
        b = _bridge_or_error()
        return b.query("getMemoryUsage")

    # --- capture ---

    @mcp_app.tool()
    def capture_frame(
        timeout_seconds: float = 30.0, poll_interval_seconds: float = 0.5
    ) -> dict:
        """Trigger a frame capture and poll until complete.

        Returns the captured frame summary (frame index, command count,
        batches). Buffer/texture data captured during this frame becomes
        available to capture_buffer / capture_texture.
        """
        b = _bridge_or_error()
        b.query("requestCapture", {})
        start = time.time()
        while time.time() - start < timeout_seconds:
            if b.query("getCaptureStatus") == "complete":
                break
            time.sleep(poll_interval_seconds)
        else:
            return {"status": "timeout", "elapsed_seconds": timeout_seconds}
        results = b.query("getCapturedFrameResults")
        return {"status": "complete", "results": results}

    @mcp_app.tool()
    def capture_commands() -> dict:
        """Return the GPU command list from the most recent captured frame."""
        b = _bridge_or_error()
        if b.query("getCaptureStatus") != "complete":
            return {"error": "No captured frame. Call capture_frame first."}
        return b.query("getCapturedFrameResults") or {}

    @mcp_app.tool()
    def capture_texture(
        id: int,
        mip_level: int = 0,
        timeout_seconds: float = 30.0,
        output_path: str | None = None,
    ) -> dict:
        """Read texture pixel data. If `output_path` is set, save as PNG (or
        raw bytes for non-.png extensions).
        """
        b = _bridge_or_error()
        b.query("requestTexture", id, mip_level)
        start = time.time()
        data = None
        while time.time() - start < timeout_seconds:
            data = b.query("getTextureData", id)
            if data and data.get("complete"):
                break
            time.sleep(0.5)
        else:
            return {"status": "timeout"}

        if not data or not data.get("data"):
            return {"status": "no_data"}

        if not output_path:
            return {
                "status": "complete",
                "textureId": id,
                "mipLevel": mip_level,
                "totalChunks": data.get("totalChunks", 0),
                "dataSize": len(data["data"]),
            }

        # Decode + save to disk.
        encoded = data["data"]
        raw = base64.b64decode(
            encoded.split(",")[-1] if "," in encoded else encoded
        )
        if output_path.endswith(".png"):
            obj = b.query("getObject", id)
            desc = obj.get("descriptor", {}) if obj else {}
            size = desc.get("size") or {}
            width = size.get("width") if isinstance(size, dict) else (
                size[0] if isinstance(size, list) and len(size) > 0 else 0
            )
            height = size.get("height") if isinstance(size, dict) else (
                size[1] if isinstance(size, list) and len(size) > 1 else 0
            )
            try:
                from PIL import Image  # type: ignore

                Image.frombytes("RGBA", (int(width), int(height)), raw).save(
                    output_path
                )
            except Exception:
                with open(output_path, "wb") as f:
                    f.write(raw)
        else:
            with open(output_path, "wb") as f:
                f.write(raw)
        return {"status": "saved", "path": output_path, "size": len(raw)}

    @mcp_app.tool()
    def capture_buffer(
        id: int,
        format: str = "hex",
        offset: int = 0,
        size: int | None = None,
        struct_spec: str | None = None,
        max_records: int | None = None,
    ) -> dict:
        """Read buffer contents from the most recent captured frame.

        Requires a prior capture_frame — buffer data is only populated during
        a frame capture (via mapAsync), not on demand.

        format: 'hex' (default), 'hex-dump' (xxd-style), 'u32-list',
            'i32-list', 'f32-list', 'f32-mat4', 'raw' (base64).
        struct_spec: if set, decode buffer as repeating records of this struct.
            Overrides `format`. Example:
            'mat4x4 anchorToWorld; u32 chunkIdDebug; pad12'.
            Supports u8/i8/u16/i16/u32/i32/u64/i64/f32/f64/bool, vec2/vec3/vec4
            (f32), mat2x2/mat3x3/mat4x4 (f32, column-major), and padN.
        """
        b = _bridge_or_error()
        data = b.query("getBufferData", id)
        if not data:
            return {
                "error": (
                    f"No buffer data for #{id}. Call capture_frame first — "
                    "buffer data is only populated during a frame capture."
                )
            }

        try:
            all_bytes = buffer_decoders.to_bytes(data.get("data"))
        except ValueError as exc:
            return {"error": f"Could not decode buffer payload: {exc}"}
        sliced = buffer_decoders.slice_bytes(all_bytes, offset=offset, size=size)
        try:
            if struct_spec:
                decoded = buffer_decoders.format_struct(
                    sliced, struct_spec, max_records=max_records
                )
            else:
                decoded = buffer_decoders.dispatch_format(
                    sliced, format.lower(),
                    base_offset=data.get("offset", 0) + offset,
                )
        except ValueError as exc:
            return {"error": f"Decode error: {exc}"}

        return {
            "bufferId": id,
            "offset": data.get("offset", 0) + offset,
            "size": len(sliced),
            "totalSize": data.get("size", 0),
            "format": "struct" if struct_spec else format.lower(),
            "structSpec": struct_spec,
            "decoded": decoded,
        }

    # --- shaders ---

    @mcp_app.tool()
    def shaders_list() -> dict:
        """List all shader modules with id, label, and code length."""
        b = _bridge_or_error()
        result = b.query("getObjects", "ShaderModule")
        return {"shaders": result, "count": len(result)}

    @mcp_app.tool()
    def shaders_view(id: int) -> dict:
        """Return the WGSL source code for a shader module."""
        b = _bridge_or_error()
        code = b.query("getShaderCode", id)
        if code is None:
            return {"error": f"Shader {id} not found or has no code"}
        return {"shaderId": id, "code": code}

    @mcp_app.tool()
    def shaders_replace(id: int, code: str) -> dict:
        """Hot-replace a shader module's WGSL source. Returns immediately."""
        b = _bridge_or_error()
        b.query("compileShader", id, code)
        return {"status": "compiled", "shaderId": id, "codeLength": len(code)}

    @mcp_app.tool()
    def shaders_revert(id: int) -> dict:
        """Revert a shader module to its original code."""
        b = _bridge_or_error()
        b.query("revertShader", id)
        return {"status": "reverted", "shaderId": id}

    # --- errors ---

    @mcp_app.tool()
    def errors_list() -> dict:
        """All validation errors with messages, object ids, and stacktraces."""
        b = _bridge_or_error()
        result = b.query("getErrors")
        return {"errors": result, "count": len(result)}

    @mcp_app.tool()
    def errors_clear() -> dict:
        """Reset the error history."""
        b = _bridge_or_error()
        b.query("clearErrors")
        return {"status": "cleared"}

    # --- status ---

    @mcp_app.tool()
    def status_summary() -> dict:
        """Object counts by type, memory, FPS, and error count."""
        b = _bridge_or_error()
        return b.query("getSummary")

    return mcp_app


def main() -> None:
    """Console-script entry point. Runs the FastMCP app over stdio."""
    app = build_server()
    app.run()


if __name__ == "__main__":
    main()
