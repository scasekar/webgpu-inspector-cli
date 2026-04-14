"""Browser session management commands."""

import json
import click

from webgpu_inspector_cli.core.bridge import get_bridge, require_bridge


@click.group()
def browser():
    """Browser session management."""
    pass


@browser.command()
@click.option("--url", required=True, help="URL to navigate to.")
@click.option("--headless", is_flag=True, default=False, help="Run in headless mode.")
@click.option("--gpu-backend", type=str, default=None, help="GPU backend (e.g., swiftshader).")
@click.pass_context
def launch(ctx, url, headless, gpu_backend):
    """Launch browser, navigate to URL, and inject the WebGPU Inspector."""
    bridge = get_bridge()
    if bridge.is_connected:
        click.echo("Browser session already active. Close it first with 'browser close'.")
        return

    bridge.launch(url, headless=headless, gpu_backend=gpu_backend)
    info = bridge.get_browser_info()

    if ctx.obj.get("json"):
        click.echo(json.dumps({
            "status": "launched",
            "url": info["url"],
            "title": info["title"],
            "gpu": info["gpu"],
        }, indent=2))
    else:
        click.echo(f"Browser launched: {info['url']}")
        click.echo(f"  Title: {info['title']}")
        click.echo(f"  GPU: {info['gpu']}")
        click.echo("Inspector injected and active.")


@browser.command()
@click.pass_context
def close(ctx):
    """Close the browser session."""
    bridge = get_bridge()
    if not bridge.is_connected:
        click.echo("No active browser session.")
        return
    bridge.close()
    if ctx.obj.get("json"):
        click.echo(json.dumps({"status": "closed"}))
    else:
        click.echo("Browser session closed.")


@browser.command()
@click.option("--url", required=True, help="URL to navigate to.")
@click.pass_context
def navigate(ctx, url):
    """Navigate to a new URL and re-inject the inspector."""
    bridge = require_bridge()
    bridge.navigate(url)
    info = bridge.get_browser_info()

    if ctx.obj.get("json"):
        click.echo(json.dumps({
            "status": "navigated",
            "url": info["url"],
            "title": info["title"],
        }, indent=2))
    else:
        click.echo(f"Navigated to: {info['url']}")
        click.echo(f"  Title: {info['title']}")


@browser.command()
@click.option("--output", "-o", required=True, help="Output file path.")
@click.option("--full-page", is_flag=True, default=False, help="Capture full scrollable page.")
@click.pass_context
def screenshot(ctx, output, full_page):
    """Take a screenshot of the current page."""
    bridge = require_bridge()
    path = bridge.screenshot(output, full_page=full_page)

    if ctx.obj.get("json"):
        click.echo(json.dumps({"status": "saved", "path": path}))
    else:
        click.echo(f"Screenshot saved: {path}")


@browser.command("status")
@click.pass_context
def browser_status(ctx):
    """Show browser and GPU status information."""
    bridge = require_bridge()
    info = bridge.get_browser_info()

    if ctx.obj.get("json"):
        click.echo(json.dumps(info, indent=2))
    else:
        click.echo(f"URL: {info['url']}")
        click.echo(f"Title: {info['title']}")
        click.echo(f"GPU: {info['gpu']}")
