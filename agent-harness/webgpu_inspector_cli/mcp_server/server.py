"""FastMCP server exposing the webgpu-inspector toolset over stdio.

Architecture: every tool is async at the MCP layer but dispatches its work to
a dedicated single-worker thread that owns the Playwright `Bridge`. Reasons:

- Sync Playwright (`playwright.sync_api`) refuses to run inside a thread that
  has a live asyncio loop ("Playwright Sync API inside the asyncio loop").
  FastMCP runs tool callbacks on its asyncio loop, so a naive sync tool would
  crash on the very first `browser_launch` — exactly what shipped in v0.2.0.
- Sync Playwright is also thread-affine: once `start()` runs in a thread,
  every subsequent call must come from the same thread. A `max_workers=1`
  executor enforces that with no extra bookkeeping.

The Bridge singleton lives wherever `get_bridge()` returns it (module-level
in `core.bridge`); pinning all tool calls to one worker thread is what keeps
its Playwright state coherent across tool invocations.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

from webgpu_inspector_cli.core.bridge import get_bridge
from webgpu_inspector_cli.utils import buffer_decoders


# Single-worker executor pinned for the lifetime of the server. Created lazily
# on first tool call so test harnesses can build the server without spawning
# a thread.
_BRIDGE_EXECUTOR: ThreadPoolExecutor | None = None


def _get_executor() -> ThreadPoolExecutor:
    global _BRIDGE_EXECUTOR
    if _BRIDGE_EXECUTOR is None:
        _BRIDGE_EXECUTOR = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="wgi-bridge"
        )
    return _BRIDGE_EXECUTOR


async def _in_bridge_thread(fn: Callable[[], Any]) -> Any:
    """Run a sync callable on the dedicated bridge thread. Inside `fn`, the
    `Bridge` singleton's Playwright session is safe to drive synchronously."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_get_executor(), fn)


def _bridge_or_error() -> Any:
    """Return the Bridge if connected, else raise a friendly RuntimeError.

    Always called from inside the bridge thread (i.e. inside a callable
    passed to `_in_bridge_thread`)."""
    b = get_bridge()
    if not b.is_connected:
        raise RuntimeError(
            "No active browser session. Call browser_launch first."
        )
    return b


# JS wrapper that captures synchronous + Promise-chain errors so they can
# travel back as structured data instead of as a Python exception.
_EVAL_TRY_CATCH = r"""
async () => {
  try {
    const __wgi_value = await (USER_EXPR);
    return { ok: true, value: __wgi_value };
  } catch (e) {
    return { ok: false, error: {
      name: (e && e.name) || "Error",
      message: (e && e.message) || String(e),
      stack: (e && e.stack) || null,
    } };
  }
}
"""


def build_server() -> FastMCP:
    """Construct and register every MCP tool. Separated from `main()` so tests
    can introspect the tool surface without spawning stdio."""
    mcp_app = FastMCP("webgpu-inspector")

    # --- browser ---

    @mcp_app.tool()
    async def browser_launch(
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
        def _impl():
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
            # Probe the GPU adapter so the response reflects whether WebGPU
            # actually works on this device, not just whether `navigator.gpu`
            # exists. (Findings doc #7.)
            adapter = bridge.eval(
                "() => navigator.gpu && navigator.gpu.requestAdapter()"
                ".then(a => a ? { name: a.name || null, "
                "isFallbackAdapter: !!a.isFallbackAdapter } : null)"
                ".catch(() => null)",
                await_promise=True,
            )
            return {
                "status": "launched",
                "url": info["url"],
                "title": info["title"],
                "gpu": info["gpu"],
                "gpuAdapter": adapter,
                "consoleLog": capture_console_path,
                "userDataDir": user_data_dir,
            }
        return await _in_bridge_thread(_impl)

    @mcp_app.tool()
    async def browser_close() -> dict:
        """Close the active browser session."""
        def _impl():
            b = get_bridge()
            if not b.is_connected:
                return {"status": "no_session"}
            b.close()
            return {"status": "closed"}
        return await _in_bridge_thread(_impl)

    @mcp_app.tool()
    async def browser_navigate(url: str) -> dict:
        """Navigate the active browser to a new URL and re-inject the inspector."""
        def _impl():
            b = _bridge_or_error()
            b.navigate(url)
            info = b.get_browser_info()
            return {"status": "navigated", "url": info["url"], "title": info["title"]}
        return await _in_bridge_thread(_impl)

    @mcp_app.tool()
    async def browser_screenshot(output_path: str, full_page: bool = False) -> dict:
        """Take a screenshot of the current page and save to `output_path`."""
        def _impl():
            b = _bridge_or_error()
            path = b.screenshot(output_path, full_page=full_page)
            return {"status": "saved", "path": path}
        return await _in_bridge_thread(_impl)

    @mcp_app.tool()
    async def browser_status() -> dict:
        """Return URL, title, and a real GPU adapter probe.

        Unlike v0.2.1, `gpu` here actually calls `navigator.gpu.requestAdapter()`
        instead of just checking that `navigator.gpu` exists, so a "no adapter"
        result is detectable.
        """
        def _impl():
            b = _bridge_or_error()
            info = b.get_browser_info()
            adapter = b.eval(
                "() => navigator.gpu && navigator.gpu.requestAdapter()"
                ".then(a => a ? { name: a.name || null, "
                "isFallbackAdapter: !!a.isFallbackAdapter } : null)"
                ".catch(() => null)",
                await_promise=True,
            )
            return {**info, "gpuAdapter": adapter}
        return await _in_bridge_thread(_impl)

    @mcp_app.tool()
    async def browser_eval(js: str, await_promise: bool = False) -> dict:
        """Run a JS expression or async function in the page context.

        The expression is wrapped in try/catch so synchronous and
        Promise-chain errors come back as structured `error` data instead of
        crashing the tool call. Returns either:
            { ok: true, value: <result> }
        or
            { ok: false, error: { name, message, stack } }

        For multi-step or async work, pass an arrow function:
        '() => { ... }'. To wait on a promise, set `await_promise=True` and
        pass a promise-returning expression.

        Note: errors that fire AFTER the eval returns (e.g. from setTimeout
        callbacks, fetch chains the eval kicked off) won't be caught here —
        they'll surface in the `pageerror` lines if `capture_console_path`
        was set on launch.
        """
        def _impl():
            b = _bridge_or_error()
            # Decide whether to inline `js` as an expression or as a function.
            stripped = js.strip()
            is_function = (
                stripped.startswith("(")
                or stripped.startswith("function")
                or stripped.startswith("async ")
            )
            user_expr = f"({js})()" if is_function else f"({js})"
            wrapped = _EVAL_TRY_CATCH.replace("USER_EXPR", user_expr)
            # Always await — the wrapper is async, so its return is a promise.
            result = b.eval(wrapped, await_promise=True)
            return result if isinstance(result, dict) else {"ok": True, "value": result}
        return await _in_bridge_thread(_impl)

    @mcp_app.tool()
    async def browser_click(selector: str, timeout_seconds: float = 30.0) -> dict:
        """Click a DOM element matching a CSS or Playwright selector."""
        def _impl():
            b = _bridge_or_error()
            b.click(selector, timeout=timeout_seconds)
            return {"status": "clicked", "selector": selector}
        return await _in_bridge_thread(_impl)

    @mcp_app.tool()
    async def browser_type(selector: str, text: str, timeout_seconds: float = 30.0) -> dict:
        """Type `text` into an input matching `selector` (replaces existing value)."""
        def _impl():
            b = _bridge_or_error()
            b.fill(selector, text, timeout=timeout_seconds)
            return {"status": "typed", "selector": selector, "length": len(text)}
        return await _in_bridge_thread(_impl)

    @mcp_app.tool()
    async def browser_wait(condition: str, timeout_seconds: float = 30.0) -> dict:
        """Block until a JS expression returns a truthy value, then return that value.

        Common pattern: wait for a global to be defined before driving the app.
        Example: `condition="window._scRenderer !== undefined"`.
        """
        def _impl():
            b = _bridge_or_error()
            try:
                value = b.wait_for_condition(condition, timeout=timeout_seconds)
            except Exception as exc:
                return {"status": "timeout", "error": str(exc)}
            return {"status": "ready", "value": value}
        return await _in_bridge_thread(_impl)

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
    async def objects_list(
        type: str | None = None, label_substring: str | None = None
    ) -> dict:
        """List GPU objects, optionally filtered by `type` and/or label substring.

        - `type`: Adapter, Device, Buffer, Texture, TextureView, Sampler,
          ShaderModule, BindGroup, BindGroupLayout, PipelineLayout,
          RenderPipeline, ComputePipeline, RenderBundle.
        - `label_substring`: case-insensitive label match. Combine with
          `type` to find e.g. all gsplat-related buffers without grepping
          a 60kB JSON dump on the client side.

        Buffer entries are enriched with a decoded `usageFlags` array
        (Storage, Indirect, CopyDst, etc.).
        """
        def _impl():
            b = _bridge_or_error()
            result = b.query("getObjects", type)
            if label_substring:
                needle = label_substring.lower()
                result = [
                    o for o in result
                    if o.get("label") and needle in o["label"].lower()
                ]
            for o in result:
                _enrich_buffer(o)
            return {"objects": result, "count": len(result)}
        return await _in_bridge_thread(_impl)

    @mcp_app.tool()
    async def objects_inspect(id: int) -> dict:
        """Return the full descriptor + creation stacktrace for a GPU object."""
        def _impl():
            b = _bridge_or_error()
            obj = b.query("getObject", id)
            if obj is None:
                return {"error": f"Object {id} not found"}
            return _enrich_buffer(obj)
        return await _in_bridge_thread(_impl)

    @mcp_app.tool()
    async def objects_search(label_substring: str, type: str | None = None) -> dict:
        """Find GPU objects by case-insensitive label substring, optionally
        narrowed to a single GPU object type. Identical to
        objects_list(label_substring, type) — kept for API stability."""
        def _impl():
            b = _bridge_or_error()
            all_objs = b.query("getObjects", type)
            needle = label_substring.lower()
            matched = [
                o for o in all_objs
                if o.get("label") and needle in o["label"].lower()
            ]
            for o in matched:
                _enrich_buffer(o)
            return {"objects": matched, "count": len(matched)}
        return await _in_bridge_thread(_impl)

    @mcp_app.tool()
    async def objects_memory() -> dict:
        """GPU memory breakdown (texture + buffer totals)."""
        def _impl():
            b = _bridge_or_error()
            return b.query("getMemoryUsage")
        return await _in_bridge_thread(_impl)

    # --- capture ---

    @mcp_app.tool()
    async def capture_frame(
        timeout_seconds: float = 30.0, poll_interval_seconds: float = 0.5
    ) -> dict:
        """Trigger a frame capture and poll until complete.

        Returns the captured frame summary (frame index, command count,
        batches). Buffer/texture data captured during this frame becomes
        available to capture_buffer / capture_texture.
        """
        def _impl():
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
        return await _in_bridge_thread(_impl)

    @mcp_app.tool()
    async def capture_commands(output_path: str | None = None) -> dict:
        """Return the GPU command list from the most recent captured frame.

        For large captures (thousands of commands), pass `output_path` to
        write the full payload as JSON to disk; the response then contains
        only a summary so the LLM context isn't blown up.
        """
        def _impl():
            b = _bridge_or_error()
            if b.query("getCaptureStatus") != "complete":
                return {"error": "No captured frame. Call capture_frame first."}
            payload = b.query("getCapturedCommands") or {}
            commands = payload.get("commands") if isinstance(payload, dict) else None
            if output_path:
                with open(output_path, "w") as f:
                    json.dump(payload, f, indent=2, default=str)
                return {
                    "status": "saved",
                    "path": output_path,
                    "frame": payload.get("frame") if isinstance(payload, dict) else None,
                    "count": len(commands) if isinstance(commands, list) else 0,
                }
            return payload
        return await _in_bridge_thread(_impl)

    @mcp_app.tool()
    async def capture_texture(
        id: int,
        mip_level: int = 0,
        timeout_seconds: float = 30.0,
        output_path: str | None = None,
    ) -> dict:
        """Read texture pixel data. If `output_path` is set, save as PNG (or
        raw bytes for non-.png extensions).
        """
        def _impl():
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
        return await _in_bridge_thread(_impl)

    @mcp_app.tool()
    async def capture_buffer(
        id: int,
        format: str = "hex",
        offset: int = 0,
        size: int | None = None,
        struct_spec: str | None = None,
        max_records: int | None = None,
        wait_seconds: float = 5.0,
        output_path: str | None = None,
    ) -> dict:
        """Read buffer contents from the most recent captured frame.

        Requires a prior capture_frame — buffer data is only populated during
        a frame capture (via mapAsync). Buffer chunks arrive ASYNC after
        capture_frame returns; this tool polls for up to `wait_seconds`
        before giving up so a quick caller doesn't see "no data" just
        because mapAsync hasn't resolved yet.

        format: 'hex' (default), 'hex-dump' (xxd-style), 'u32-list',
            'i32-list', 'f32-list', 'f32-mat4', 'raw' (base64).
        struct_spec: if set, decode buffer as repeating records of this struct.
            Overrides `format`. Example:
            'mat4x4 anchorToWorld; u32 chunkIdDebug; pad12'.
            Supports u8/i8/u16/i16/u32/i32/u64/i64/f32/f64/bool, vec2/vec3/vec4
            (f32), mat2x2/mat3x3/mat4x4 (f32, column-major), and padN.
        output_path: if set, write the decoded text to this file and return
            a summary instead of the full decoded payload (good for buffers
            that decode to thousands of lines).
        """
        def _impl():
            b = _bridge_or_error()
            data = None
            start = time.time()
            while time.time() - start < max(wait_seconds, 0.0):
                data = b.query("getBufferData", id)
                if data and data.get("data"):
                    break
                time.sleep(0.1)
            if not data or not data.get("data"):
                return {
                    "error": (
                        f"No buffer data for #{id}. The buffer may not have "
                        "been bound during the captured frame, or buffer reads "
                        "are still in flight (try a longer wait_seconds). "
                        "Call capture_frame first."
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

            response = {
                "bufferId": id,
                "offset": data.get("offset", 0) + offset,
                "size": len(sliced),
                "totalSize": data.get("size", 0),
                "format": "struct" if struct_spec else format.lower(),
                "structSpec": struct_spec,
            }
            if output_path:
                with open(output_path, "w") as f:
                    f.write(decoded)
                response["path"] = output_path
                response["chars"] = len(decoded)
            else:
                response["decoded"] = decoded
            return response
        return await _in_bridge_thread(_impl)

    # --- shaders ---

    @mcp_app.tool()
    async def shaders_list() -> dict:
        """List all shader modules with id, label, and `codeLength`.

        `codeLength` is the byte length of the WGSL source — useful to tell
        an empty stub from a 30kB compute shader without fetching the full
        source.
        """
        def _impl():
            b = _bridge_or_error()
            result = b.query("getObjects", "ShaderModule") or []
            for s in result:
                desc = s.get("descriptor") or {}
                code = desc.get("code") if isinstance(desc, dict) else None
                if isinstance(code, str):
                    s["codeLength"] = len(code)
                else:
                    s["codeLength"] = s.get("size") or 0
            return {"shaders": result, "count": len(result)}
        return await _in_bridge_thread(_impl)

    @mcp_app.tool()
    async def shaders_view(id: int) -> dict:
        """Return the WGSL source code for a shader module."""
        def _impl():
            b = _bridge_or_error()
            code = b.query("getShaderCode", id)
            if code is None:
                return {"error": f"Shader {id} not found or has no code"}
            return {"shaderId": id, "code": code, "codeLength": len(code)}
        return await _in_bridge_thread(_impl)

    @mcp_app.tool()
    async def shaders_replace(id: int, code: str) -> dict:
        """Hot-replace a shader module's WGSL source. Returns immediately."""
        def _impl():
            b = _bridge_or_error()
            b.query("compileShader", id, code)
            return {"status": "compiled", "shaderId": id, "codeLength": len(code)}
        return await _in_bridge_thread(_impl)

    @mcp_app.tool()
    async def shaders_revert(id: int) -> dict:
        """Revert a shader module to its original code."""
        def _impl():
            b = _bridge_or_error()
            b.query("revertShader", id)
            return {"status": "reverted", "shaderId": id}
        return await _in_bridge_thread(_impl)

    # --- errors ---

    @mcp_app.tool()
    async def errors_list() -> dict:
        """All validation errors with messages, object ids, and stacktraces."""
        def _impl():
            b = _bridge_or_error()
            result = b.query("getErrors")
            return {"errors": result, "count": len(result)}
        return await _in_bridge_thread(_impl)

    @mcp_app.tool()
    async def errors_clear() -> dict:
        """Reset the error history."""
        def _impl():
            b = _bridge_or_error()
            b.query("clearErrors")
            return {"status": "cleared"}
        return await _in_bridge_thread(_impl)

    # --- status ---

    @mcp_app.tool()
    async def status_summary() -> dict:
        """Object counts by type, memory, FPS, and error count."""
        def _impl():
            b = _bridge_or_error()
            return b.query("getSummary")
        return await _in_bridge_thread(_impl)

    return mcp_app


def main() -> None:
    """Console-script entry point. Runs the FastMCP app over stdio."""
    app = build_server()
    app.run()


if __name__ == "__main__":
    main()
