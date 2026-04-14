"""Runtime monitoring commands."""

import json
import click

from cli_anything.webgpu_inspector.core.bridge import require_bridge


def _format_bytes(n):
    if n is None or n == 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


@click.group()
def status():
    """Runtime monitoring."""
    pass


@status.command()
@click.pass_context
def summary(ctx):
    """Overall GPU state summary."""
    bridge = require_bridge()
    result = bridge.query("getSummary")

    if ctx.obj.get("json"):
        click.echo(json.dumps(result, indent=2))
    else:
        click.echo(f"Objects: {result['objectCount']}")
        if result.get("typeCounts"):
            for t, c in sorted(result["typeCounts"].items()):
                click.echo(f"  {t}: {c}")
        click.echo(f"Errors: {result['errorCount']}")
        fps = result.get("fps", 0)
        dt = result.get("deltaTime", -1)
        if dt > 0:
            click.echo(f"FPS: {fps} ({dt:.1f}ms)")
        else:
            click.echo("FPS: -- (no frame data)")
        click.echo(f"Texture memory: {_format_bytes(result['totalTextureMemory'])}")
        click.echo(f"Buffer memory:  {_format_bytes(result['totalBufferMemory'])}")
        click.echo(f"Total memory:   {_format_bytes(result['totalMemory'])}")


@status.command()
@click.pass_context
def fps(ctx):
    """Show current frame rate."""
    bridge = require_bridge()
    result = bridge.query("getFrameRate")

    if ctx.obj.get("json"):
        click.echo(json.dumps(result))
    else:
        if result["deltaTime"] <= 0:
            click.echo("FPS: -- (no frame data yet)")
        else:
            click.echo(f"FPS: {result['fps']} ({result['deltaTime']:.1f}ms per frame)")


@status.command("memory")
@click.pass_context
def memory_cmd(ctx):
    """Show GPU memory breakdown."""
    bridge = require_bridge()
    mem = bridge.query("getMemoryUsage")

    if ctx.obj.get("json"):
        click.echo(json.dumps(mem, indent=2))
    else:
        click.echo(f"Texture memory: {_format_bytes(mem['totalTextureMemory'])}")
        click.echo(f"Buffer memory:  {_format_bytes(mem['totalBufferMemory'])}")
        click.echo(f"Total:          {_format_bytes(mem['totalMemory'])}")
