"""GPU object inspection commands."""

import json
import click

from webgpu_inspector_cli.core.bridge import require_bridge
from webgpu_inspector_cli.utils.buffer_decoders import decode_buffer_usage

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


def _extract_buffer_usage(obj: dict) -> int | None:
    """Pull the GPUBufferUsage bitmask off a buffer object record."""
    desc = obj.get("descriptor") or {}
    if isinstance(desc, dict):
        usage = desc.get("usage")
        if isinstance(usage, int):
            return usage
    # Some collector paths surface usage at the top level.
    top = obj.get("usage")
    if isinstance(top, int):
        return top
    return None


@click.group()
def objects():
    """GPU object inspection."""
    pass


@objects.command("list")
@click.option("--type", "obj_type", type=click.Choice(GPU_TYPES, case_sensitive=False),
              default=None, help="Filter by object type.")
@click.option("--label", "label_substring", type=str, default=None,
              help="Case-insensitive substring filter on object label.")
@click.pass_context
def list_objects(ctx, obj_type, label_substring):
    """List all live GPU objects.

    For Buffers, decoded GPUBufferUsage flags (Storage, Indirect, CopyDst, etc.)
    are shown so you can quickly spot indirect-draw sources, uniform buffers,
    and writable storage targets.
    """
    bridge = require_bridge()
    result = bridge.query("getObjects", obj_type)

    if label_substring:
        needle = label_substring.lower()
        result = [
            o for o in result
            if o.get("label") and needle in o["label"].lower()
        ]

    is_buffer_view = obj_type and obj_type.lower() == "buffer"

    if ctx.obj.get("json"):
        # Enrich buffer entries with decoded usage flags.
        if is_buffer_view or any((o.get("type") == "Buffer") for o in result):
            for o in result:
                if o.get("type") == "Buffer":
                    bits = _extract_buffer_usage(o)
                    if bits is not None:
                        o["usageFlags"] = decode_buffer_usage(bits)
        click.echo(json.dumps({"objects": result, "count": len(result)}, indent=2))
        return

    if not result:
        click.echo("No GPU objects found.")
        return

    if is_buffer_view:
        # Wider table that includes usage flags. Truncated to fit common terminals.
        click.echo(f"{'ID':>6}  {'Label':<24}  {'Size':>10}  {'Usage'}")
        click.echo("-" * 78)
        for obj in result:
            size_str = _format_bytes(obj.get("size") or obj.get("gpuSize") or 0)
            label = (obj.get("label") or "")[:24]
            bits = _extract_buffer_usage(obj)
            flags = decode_buffer_usage(bits) if bits is not None else []
            flags_str = " | ".join(flags) if flags else "-"
            click.echo(f"{obj['id']:>6}  {label:<24}  {size_str:>10}  {flags_str}")
        click.echo(f"\nTotal: {len(result)} buffers")
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
        if obj.get("type") == "Buffer":
            bits = _extract_buffer_usage(obj)
            if bits is not None:
                obj["usageFlags"] = decode_buffer_usage(bits)
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
        if obj.get("type") == "Buffer":
            bits = _extract_buffer_usage(obj)
            if bits is not None:
                flags = decode_buffer_usage(bits)
                click.echo(f"  Usage: {' | '.join(flags) if flags else '-'} (0x{bits:04x})")
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
@click.option("--type", "obj_type", type=click.Choice(GPU_TYPES, case_sensitive=False),
              default=None, help="Optional object-type filter to narrow the search.")
@click.pass_context
def search(ctx, label, obj_type):
    """Find objects by label, optionally narrowed to a single GPU object type."""
    bridge = require_bridge()
    all_objs = bridge.query("getObjects", obj_type)
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
