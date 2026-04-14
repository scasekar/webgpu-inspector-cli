"""Validation error tracking commands."""

import json
import time
import click

from cli_anything.webgpu_inspector.core.bridge import require_bridge


@click.group()
def errors():
    """Validation error tracking."""
    pass


@errors.command("list")
@click.pass_context
def list_errors(ctx):
    """List all validation errors."""
    bridge = require_bridge()
    result = bridge.query("getErrors")

    if ctx.obj.get("json"):
        click.echo(json.dumps({"errors": result, "count": len(result)}, indent=2))
    else:
        if not result:
            click.echo("No validation errors.")
            return
        for err in result:
            click.echo(f"Error #{err['id']}:")
            click.echo(f"  Message: {err['message']}")
            if err.get("objectId"):
                click.echo(f"  Object: #{err['objectId']}")
            if err.get("stacktrace"):
                click.echo("  Stacktrace:")
                for line in err["stacktrace"].split("\n"):
                    if line.strip():
                        click.echo(f"    {line.strip()}")
            click.echo()
        click.echo(f"Total: {len(result)} errors")


@errors.command()
@click.option("--timeout", type=float, default=30.0, help="Seconds to watch for errors.")
@click.option("--poll-interval", type=float, default=1.0, help="Seconds between polls.")
@click.pass_context
def watch(ctx, timeout, poll_interval):
    """Watch for new validation errors in real-time."""
    bridge = require_bridge()
    seen_count = bridge.query("getErrorCount")
    use_json = ctx.obj.get("json")

    click.echo(f"Watching for errors (timeout: {timeout}s)..." if not use_json else "", nl=not use_json)

    start = time.time()
    while time.time() - start < timeout:
        current_count = bridge.query("getErrorCount")
        if current_count > seen_count:
            all_errors = bridge.query("getErrors")
            new_errors = all_errors[seen_count:]
            for err in new_errors:
                if use_json:
                    click.echo(json.dumps(err))
                else:
                    click.echo(f"[{err['id']}] {err['message']}")
            seen_count = current_count
        time.sleep(poll_interval)

    if not use_json:
        click.echo(f"Watch ended. Total errors: {seen_count}")


@errors.command()
@click.pass_context
def clear(ctx):
    """Clear error history."""
    bridge = require_bridge()
    bridge.query("clearErrors")
    if ctx.obj.get("json"):
        click.echo(json.dumps({"status": "cleared"}))
    else:
        click.echo("Errors cleared.")
