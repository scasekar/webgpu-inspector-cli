"""Frame capture and data inspection commands."""

import base64
import json
import time
import click

from webgpu_inspector_cli.core.bridge import require_bridge


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
@click.pass_context
def commands(ctx, pass_index):
    """List GPU commands from a captured frame."""
    bridge = require_bridge()

    status = bridge.query("getCaptureStatus")
    if status != "complete":
        click.echo("No captured frame. Run 'capture frame' first.", err=True)
        raise SystemExit(1)

    results = bridge.query("getCapturedFrameResults")
    if not results:
        click.echo("No capture data available.", err=True)
        raise SystemExit(1)

    if ctx.obj.get("json"):
        click.echo(json.dumps(results, indent=2))
    else:
        batches = results.get("batches", [])
        if not batches:
            click.echo("No command batches in capture.")
            return

        for i, batch in enumerate(batches):
            if pass_index is not None and i != pass_index:
                continue
            cmds = batch.get("commands", batch) if isinstance(batch, dict) else batch
            click.echo(f"Batch {i}:")
            if isinstance(cmds, list):
                for cmd in cmds:
                    if isinstance(cmd, dict):
                        click.echo(f"  {cmd.get('method', cmd.get('name', str(cmd)))}")
                    else:
                        click.echo(f"  {cmd}")
            else:
                click.echo(f"  {cmds}")


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


@capture.command()
@click.option("--id", "buf_id", type=int, required=True, help="Buffer object ID.")
@click.option("--offset", type=int, default=0, help="Byte offset into buffer.")
@click.option("--size", "read_size", type=int, default=None, help="Number of bytes to read.")
@click.option("--format", "fmt", type=click.Choice(["hex", "float32", "uint32", "raw"]),
              default="hex", help="Display format.")
@click.pass_context
def buffer(ctx, buf_id, offset, read_size, fmt):
    """Read buffer contents."""
    bridge = require_bridge()
    data = bridge.query("getBufferData", buf_id)

    if not data:
        click.echo(f"No buffer data for #{buf_id}. Capture a frame first.", err=True)
        raise SystemExit(1)

    if ctx.obj.get("json"):
        click.echo(json.dumps({
            "bufferId": buf_id,
            "offset": data.get("offset", 0),
            "size": data.get("size", 0),
            "data": data.get("data"),
        }, indent=2))
    else:
        click.echo(f"Buffer #{buf_id}:")
        click.echo(f"  Offset: {data.get('offset', 0)}")
        click.echo(f"  Size: {data.get('size', 0)} bytes")
        if data.get("data"):
            click.echo(f"  Data (first 256 chars): {str(data['data'])[:256]}")
