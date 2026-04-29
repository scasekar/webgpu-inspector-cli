"""Frame capture and data inspection commands."""

import base64
import json
import time
import click

from webgpu_inspector_cli.core.bridge import require_bridge
from webgpu_inspector_cli.utils import buffer_decoders


@click.group()
def capture():
    """Frame capture and data inspection."""
    pass


@capture.command()
@click.option("--timeout", type=float, default=30.0, help="Seconds to wait for capture.")
@click.option("--poll-interval", type=float, default=0.5, help="Seconds between status polls.")
@click.pass_context
def frame(ctx, timeout, poll_interval):
    """Capture the next frame's GPU commands."""
    bridge = require_bridge()

    # Trigger capture
    bridge.query("requestCapture", {})

    if not ctx.obj.get("json"):
        click.echo("Capture requested, waiting for frame...")

    # Poll for completion
    start = time.time()
    while time.time() - start < timeout:
        status = bridge.query("getCaptureStatus")
        if status == "complete":
            break
        time.sleep(poll_interval)
    else:
        msg = f"Capture timed out after {timeout}s"
        if ctx.obj.get("json"):
            click.echo(json.dumps({"status": "timeout", "error": msg}))
        else:
            click.echo(msg, err=True)
        raise SystemExit(1)

    results = bridge.query("getCapturedFrameResults")
    if ctx.obj.get("json"):
        click.echo(json.dumps({"status": "complete", "results": results}, indent=2))
    else:
        click.echo("Frame captured successfully.")
        if results:
            click.echo(f"  Frame: {results.get('frame', '?')}")
            click.echo(f"  Commands: {results.get('count', '?')}")


@capture.command()
@click.option("--pass-index", type=int, default=None, help="Filter by render pass index.")
@click.option(
    "--output", "-o", "output_path",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Write the full captured-commands payload as JSON to this file. "
         "Useful for large captures (thousands of commands) to avoid spamming the terminal.",
)
@click.pass_context
def commands(ctx, pass_index, output_path):
    """List GPU commands from a captured frame.

    Without --output the captured commands print to stdout. With --output the
    JSON payload is written to disk and only a one-line summary is printed.
    """
    bridge = require_bridge()

    status = bridge.query("getCaptureStatus")
    if status != "complete":
        click.echo("No captured frame. Run 'capture frame' first.", err=True)
        raise SystemExit(1)

    payload = bridge.query("getCapturedCommands") or {}
    summary = bridge.query("getCapturedFrameResults") or {}
    cmds = payload.get("commands") if isinstance(payload, dict) else None

    if output_path:
        with open(output_path, "w") as f:
            json.dump({"summary": summary, "payload": payload}, f, indent=2, default=str)
        if ctx.obj.get("json"):
            click.echo(json.dumps({
                "status": "saved",
                "path": output_path,
                "frame": summary.get("frame"),
                "count": len(cmds) if isinstance(cmds, list) else 0,
            }))
        else:
            click.echo(
                f"Wrote {output_path} (frame={summary.get('frame')}, "
                f"count={len(cmds) if isinstance(cmds, list) else 0})"
            )
        return

    if ctx.obj.get("json"):
        click.echo(json.dumps({"summary": summary, "payload": payload}, indent=2, default=str))
        return

    if not isinstance(cmds, list) or not cmds:
        click.echo("No commands captured.")
        return

    click.echo(f"Frame {summary.get('frame', '?')}: {len(cmds)} commands")
    for i, cmd in enumerate(cmds):
        if pass_index is not None:
            # Naive pass filter: count beginRenderPass to derive pass index.
            # Kept consistent with prior behaviour even if approximate.
            if cmd.get("method") != "beginRenderPass" and i != pass_index:
                continue
        if isinstance(cmd, dict):
            click.echo(f"  [{cmd.get('commandId', i)}] {cmd.get('method', '?')}")
        else:
            click.echo(f"  {cmd}")


@capture.command()
@click.option("--id", "tex_id", type=int, required=True, help="Texture object ID.")
@click.option("--mip-level", type=int, default=0, help="Mip level to capture.")
@click.option("--output", "-o", type=str, default=None, help="Save as PNG to path.")
@click.option("--timeout", type=float, default=30.0, help="Seconds to wait for texture data.")
@click.pass_context
def texture(ctx, tex_id, mip_level, output, timeout):
    """Read texture data from a live or captured texture."""
    bridge = require_bridge()

    # Request the texture data
    bridge.query("requestTexture", tex_id, mip_level)

    if not ctx.obj.get("json"):
        click.echo(f"Requesting texture #{tex_id} mip {mip_level}...")

    # Poll for data
    start = time.time()
    while time.time() - start < timeout:
        data = bridge.query("getTextureData", tex_id)
        if data and data.get("complete"):
            break
        time.sleep(0.5)
    else:
        msg = f"Texture data timed out after {timeout}s"
        if ctx.obj.get("json"):
            click.echo(json.dumps({"status": "timeout", "error": msg}))
        else:
            click.echo(msg, err=True)
        raise SystemExit(1)

    if output and data.get("data"):
        # The data is base64 encoded - decode and save as raw or convert to PNG
        try:
            raw = base64.b64decode(data["data"].split(",")[-1] if "," in data["data"] else data["data"])
            # Get texture info for dimensions
            obj = bridge.query("getObject", tex_id)
            desc = obj.get("descriptor", {}) if obj else {}
            width = desc.get("size", {}).get("width", desc.get("size", [0])[0] if isinstance(desc.get("size"), list) else 0)
            height = desc.get("size", {}).get("height", desc.get("size", [0, 0])[1] if isinstance(desc.get("size"), list) and len(desc.get("size", [])) > 1 else 0)

            if output.endswith(".png") and width and height:
                try:
                    from PIL import Image
                    img = Image.frombytes("RGBA", (width, height), raw)
                    img.save(output)
                except Exception:
                    # Fallback: save raw bytes
                    with open(output, "wb") as f:
                        f.write(raw)
            else:
                with open(output, "wb") as f:
                    f.write(raw)

            if ctx.obj.get("json"):
                click.echo(json.dumps({"status": "saved", "path": output, "size": len(raw)}))
            else:
                click.echo(f"Texture saved: {output} ({len(raw)} bytes)")
        except Exception as e:
            click.echo(f"Error saving texture: {e}", err=True)
            raise SystemExit(1)
    else:
        if ctx.obj.get("json"):
            click.echo(json.dumps({
                "status": "complete",
                "textureId": tex_id,
                "mipLevel": mip_level,
                "dataSize": len(data.get("data", "")) if data else 0,
            }, indent=2))
        else:
            click.echo(f"Texture #{tex_id} data received ({data.get('totalChunks', 0)} chunks)")


_BUFFER_FORMATS = list(buffer_decoders.SUPPORTED_FORMATS)


@capture.command()
@click.option("--id", "buf_id", type=int, required=True, help="Buffer object ID.")
@click.option("--offset", type=int, default=0, help="Byte offset into buffer.")
@click.option("--size", "read_size", type=int, default=None, help="Number of bytes to read.")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(_BUFFER_FORMATS, case_sensitive=False),
    default="hex",
    help=(
        "Display format. 'hex' = compact hex string. 'hex-dump' = xxd-style "
        "with offsets and ASCII gutter. 'u32-list' / 'i32-list' / 'f32-list' = "
        "decoded little-endian array. 'f32-mat4' = 4x4 column-major matrix. "
        "'raw' = base64. 'uint32'/'float32' are legacy aliases for u32-list/f32-list."
    ),
)
@click.option(
    "--struct",
    "struct_spec",
    type=str,
    default=None,
    help=(
        "Decode buffer as repeating records of this struct shape. "
        "Overrides --format. Example: "
        "'mat4x4 anchorToWorld; u32 chunkIdDebug; pad12'. "
        "Supports u8/i8/u16/i16/u32/i32/u64/i64/f32/f64/bool, "
        "vec2/vec3/vec4 (f32), mat2x2/mat3x3/mat4x4 (f32, column-major), and padN."
    ),
)
@click.option(
    "--max-records",
    type=int,
    default=None,
    help="With --struct, limit output to the first N records.",
)
@click.option(
    "--wait",
    "wait_seconds",
    type=float,
    default=5.0,
    help=(
        "Seconds to wait for buffer data to arrive. Buffer reads land async "
        "(via mapAsync) AFTER capture_frame returns; if the buffer is missing "
        "right after a capture, give it more time before declaring failure."
    ),
)
@click.option(
    "--output", "-o", "output_path",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Write the decoded text to this file instead of stdout (useful for large struct decodes).",
)
@click.pass_context
def buffer(ctx, buf_id, offset, read_size, fmt, struct_spec, max_records, wait_seconds, output_path):
    """Read buffer contents from the most recent captured frame.

    Requires a prior 'capture frame'. Returns the buffer state from that frame
    — buffer data is collected during frame capture (via mapAsync), not on
    demand. If you don't see the buffer you expect, run 'capture frame' first.
    """
    bridge = require_bridge()

    # Buffer data lands async via mapAsync; poll briefly so a fast caller
    # doesn't see a false negative right after capture_frame returned.
    data = None
    deadline = time.time() + max(wait_seconds, 0.0)
    while time.time() < deadline:
        data = bridge.query("getBufferData", buf_id)
        if data and data.get("data"):
            break
        time.sleep(0.1)

    if not data or not data.get("data"):
        click.echo(
            f"No buffer data for #{buf_id}. The buffer may not have been bound "
            "during the captured frame, or buffer reads are still in flight "
            "(try a longer --wait). Run 'capture frame' first.",
            err=True,
        )
        raise SystemExit(1)

    raw_payload = data.get("data")
    raw_offset = data.get("offset", 0)
    raw_size = data.get("size", 0)

    # Normalize and apply user-supplied offset/size window on top of whatever
    # the inspector returned.
    try:
        all_bytes = buffer_decoders.to_bytes(raw_payload)
    except ValueError as exc:
        click.echo(f"Could not decode buffer payload: {exc}", err=True)
        raise SystemExit(1)
    sliced = buffer_decoders.slice_bytes(all_bytes, offset=offset, size=read_size)

    try:
        if struct_spec:
            decoded = buffer_decoders.format_struct(
                sliced, struct_spec, max_records=max_records
            )
        else:
            decoded = buffer_decoders.dispatch_format(
                sliced, fmt.lower(), base_offset=raw_offset + offset
            )
    except ValueError as exc:
        click.echo(f"Decode error: {exc}", err=True)
        raise SystemExit(1)

    summary = {
        "bufferId": buf_id,
        "offset": raw_offset + offset,
        "size": len(sliced),
        "totalSize": raw_size,
        "format": "struct" if struct_spec else fmt.lower(),
        "structSpec": struct_spec,
    }

    if output_path:
        with open(output_path, "w") as f:
            f.write(decoded)
        summary["path"] = output_path
        summary["chars"] = len(decoded)
        if ctx.obj.get("json"):
            click.echo(json.dumps(summary, indent=2))
        else:
            click.echo(
                f"Buffer #{buf_id} ({len(sliced)} bytes, "
                f"{'struct: ' + struct_spec if struct_spec else fmt.lower()}) "
                f"→ {output_path} ({len(decoded)} chars)"
            )
        return

    if ctx.obj.get("json"):
        click.echo(json.dumps({**summary, "decoded": decoded}, indent=2))
    else:
        click.echo(f"Buffer #{buf_id}:")
        click.echo(f"  Source offset: {raw_offset + offset}")
        click.echo(f"  Window size:   {len(sliced)} bytes (of {raw_size} total)")
        click.echo(f"  Format:        {'struct: ' + struct_spec if struct_spec else fmt.lower()}")
        click.echo("")
        click.echo(decoded)
