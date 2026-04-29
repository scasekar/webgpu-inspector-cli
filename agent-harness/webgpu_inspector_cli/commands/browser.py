"""Browser session management commands."""

import json
import click

from webgpu_inspector_cli.core.bridge import get_bridge, require_bridge


@click.group()
def browser():
    """Browser session management."""
    pass


@browser.command()
@click.option(
    "--url",
    required=True,
    help="URL to navigate to. Quote URLs containing '&' or other shell metacharacters.",
)
@click.option("--headless", is_flag=True, default=False, help="Run in headless mode.")
@click.option("--gpu-backend", type=str, default=None, help="GPU backend (e.g., swiftshader).")
@click.option(
    "--capture-console",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Append all browser console messages to this file. Listener attaches before navigation, so page-bootstrap logs are captured.",
)
@click.option(
    "--user-data-dir",
    type=click.Path(file_okay=False),
    default=None,
    help="Use a persistent Chrome user-data directory (cookies, localStorage, extensions). Useful when the target app depends on existing browser state.",
)
@click.pass_context
def launch(ctx, url, headless, gpu_backend, capture_console, user_data_dir):
    """Launch browser, navigate to URL, and inject the WebGPU Inspector."""
    bridge = get_bridge()
    if bridge.is_connected:
        click.echo("Browser session already active. Close it first with 'browser close'.")
        return

    bridge.launch(
        url,
        headless=headless,
        gpu_backend=gpu_backend,
        capture_console_path=capture_console,
        user_data_dir=user_data_dir,
    )
    info = bridge.get_browser_info()

    if ctx.obj.get("json"):
        click.echo(json.dumps({
            "status": "launched",
            "url": info["url"],
            "title": info["title"],
            "gpu": info["gpu"],
            "consoleLog": capture_console,
            "userDataDir": user_data_dir,
        }, indent=2))
    else:
        click.echo(f"Browser launched: {info['url']}")
        click.echo(f"  Title: {info['title']}")
        click.echo(f"  GPU: {info['gpu']}")
        if capture_console:
            click.echo(f"  Console log: {capture_console}")
        if user_data_dir:
            click.echo(f"  User data dir: {user_data_dir}")
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


# --- Page-driving commands (NEW: solves the showstopper from feature request A) ---


@browser.command("eval")
@click.option("--js", "js_expr", type=str, default=None, help="JS expression or function to run.")
@click.option(
    "--file",
    "js_file",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Path to a .js file whose contents are run instead of --js.",
)
@click.option(
    "--await", "await_promise",
    is_flag=True,
    default=False,
    help="Treat the expression as a promise and await its resolved value.",
)
@click.pass_context
def eval_cmd(ctx, js_expr, js_file, await_promise):
    """Run an arbitrary JS expression in the page context.

    Equivalent to Playwright's page.evaluate. Returns whatever the expression
    evaluates to, JSON-serialized. Pass either --js '<expression>' or --file
    <path.js> for multi-line scripts.

    Examples:
      browser eval --js 'document.title'
      browser eval --js 'window.scene && window.scene.objectCount'
      browser eval --file ./debug-snippet.js
    """
    if not js_expr and not js_file:
        click.echo("Provide either --js '<expr>' or --file <path>.", err=True)
        raise SystemExit(2)
    if js_expr and js_file:
        click.echo("Provide only one of --js or --file.", err=True)
        raise SystemExit(2)

    bridge = require_bridge()
    if js_file:
        with open(js_file) as f:
            code = f.read()
    else:
        code = js_expr

    result = bridge.eval(code, await_promise=await_promise)

    if ctx.obj.get("json"):
        click.echo(json.dumps({"result": result}, default=str, indent=2))
    else:
        # Pretty-print structured results, raw-print primitives.
        if isinstance(result, (dict, list)):
            click.echo(json.dumps(result, default=str, indent=2))
        else:
            click.echo(str(result))


@browser.command("click")
@click.argument("selector")
@click.option("--timeout", type=float, default=30.0, help="Seconds to wait for the element.")
@click.pass_context
def click_cmd(ctx, selector, timeout):
    """Click an element matching a CSS or Playwright selector.

    Examples:
      browser click 'button.load-scene'
      browser click 'text=Login'
    """
    bridge = require_bridge()
    bridge.click(selector, timeout=timeout)
    if ctx.obj.get("json"):
        click.echo(json.dumps({"status": "clicked", "selector": selector}))
    else:
        click.echo(f"Clicked: {selector}")


@browser.command("type")
@click.argument("selector")
@click.argument("text")
@click.option("--timeout", type=float, default=30.0, help="Seconds to wait for the element.")
@click.pass_context
def type_cmd(ctx, selector, text, timeout):
    """Type text into an input matching a CSS selector.

    Examples:
      browser type 'input[name=email]' 'user@example.com'
    """
    bridge = require_bridge()
    bridge.fill(selector, text, timeout=timeout)
    if ctx.obj.get("json"):
        click.echo(json.dumps({"status": "typed", "selector": selector, "length": len(text)}))
    else:
        click.echo(f"Typed {len(text)} chars into: {selector}")


@browser.command("wait")
@click.option(
    "--condition",
    required=True,
    help="JS expression to poll until truthy. Example: 'window._scRenderer !== undefined'.",
)
@click.option("--timeout", type=float, default=30.0, help="Seconds before giving up.")
@click.pass_context
def wait_cmd(ctx, condition, timeout):
    """Block until a JS expression returns a truthy value.

    Common pattern: wait for a global to be defined before driving the app.

    Example:
      browser wait --condition 'window._scRenderer !== undefined' --timeout 10
    """
    bridge = require_bridge()
    try:
        value = bridge.wait_for_condition(condition, timeout=timeout)
    except Exception as exc:
        if ctx.obj.get("json"):
            click.echo(json.dumps({"status": "timeout", "error": str(exc)}))
        else:
            click.echo(f"Wait timed out: {exc}", err=True)
        raise SystemExit(1)

    if ctx.obj.get("json"):
        click.echo(json.dumps({"status": "ready", "value": value}, default=str))
    else:
        click.echo(f"Condition met. Value: {value}")
