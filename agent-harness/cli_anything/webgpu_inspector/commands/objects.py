"""GPU object inspection commands."""

import json
import click

from cli_anything.webgpu_inspector.core.bridge import require_bridge

GPU_TYPES = [
    "Adapter", "Device", "Buffer", "Texture", "TextureView", "Sampler",
    "ShaderModule", "BindGroup", "BindGroupLayout", "PipelineLayout",
    "RenderPipeline", "ComputePipeline", "RenderBundle",
]


def _format_bytes(n):
    if n is None or n == 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


@click.group()
def objects():
    """GPU object inspection."""
    pass


@objects.command("list")
@click.option("--type", "obj_type", type=click.Choice(GPU_TYPES, case_sensitive=False),
              default=None, help="Filter by object type.")
@click.pass_context
def list_objects(ctx, obj_type):
    """List all live GPU objects."""
    bridge = require_bridge()
    result = bridge.query("getObjects", obj_type)

    if ctx.obj.get("json"):
        click.echo(json.dumps({"objects": result, "count": len(result)}, indent=2))
    else:
        if not result:
            click.echo("No GPU objects found.")
            return
        click.echo(f"{'ID':>6}  {'Type':<20}  {'Label':<30}  {'Size':>12}")
        click.echo("-" * 72)
        for obj in result:
            size_str = ""
            if obj.get("size"):
                size_str = _format_bytes(obj["size"])
            elif obj.get("gpuSize"):
                size_str = _format_bytes(obj["gpuSize"])
            label = obj.get("label") or ""
            click.echo(f"{obj['id']:>6}  {obj['type']:<20}  {label:<30}  {size_str:>12}")
        click.echo(f"\nTotal: {len(result)} objects")


@objects.command()
@click.option("--id", "obj_id", type=int, required=True, help="Object ID.")
@click.pass_context
def inspect(ctx, obj_id):
    """Detailed view of a single GPU object."""
    bridge = require_bridge()
    obj = bridge.query("getObject", obj_id)

    if obj is None:
        click.echo(f"Object {obj_id} not found.", err=True)
        raise SystemExit(1)

    if ctx.obj.get("json"):
        click.echo(json.dumps(obj, indent=2))
    else:
        click.echo(f"Object #{obj['id']} ({obj['type']})")
        if obj.get("label"):
            click.echo(f"  Label: {obj['label']}")
        if obj.get("parent") is not None:
            click.echo(f"  Parent: #{obj['parent']}")
        if obj.get("pending"):
            click.echo("  Status: pending (async)")
        if obj.get("size"):
            click.echo(f"  Size: {_format_bytes(obj['size'])}")
        if obj.get("gpuSize"):
            click.echo(f"  GPU Memory: {_format_bytes(obj['gpuSize'])}")
        if obj.get("descriptor"):
            click.echo("  Descriptor:")
            desc_str = json.dumps(obj["descriptor"], indent=4)
            for line in desc_str.split("\n"):
                click.echo(f"    {line}")
        if obj.get("stacktrace"):
            click.echo("  Creation stacktrace:")
            for line in obj["stacktrace"].split("\n"):
                if line.strip():
                    click.echo(f"    {line.strip()}")


@objects.command()
@click.option("--label", required=True, help="Label substring to search for.")
@click.pass_context
def search(ctx, label):
    """Find objects by label."""
    bridge = require_bridge()
    all_objs = bridge.query("getObjects", None)
    matched = [o for o in all_objs if o.get("label") and label.lower() in o["label"].lower()]

    if ctx.obj.get("json"):
        click.echo(json.dumps({"objects": matched, "count": len(matched)}, indent=2))
    else:
        if not matched:
            click.echo(f"No objects matching '{label}'.")
            return
        for obj in matched:
            click.echo(f"  #{obj['id']} {obj['type']}: {obj.get('label', '')}")
        click.echo(f"\nFound: {len(matched)} objects")


@objects.command()
@click.pass_context
def memory(ctx):
    """Show GPU memory usage breakdown."""
    bridge = require_bridge()
    mem = bridge.query("getMemoryUsage")

    if ctx.obj.get("json"):
        click.echo(json.dumps(mem, indent=2))
    else:
        click.echo(f"Texture memory: {_format_bytes(mem['totalTextureMemory'])}")
        click.echo(f"Buffer memory:  {_format_bytes(mem['totalBufferMemory'])}")
        click.echo(f"Total:          {_format_bytes(mem['totalMemory'])}")
