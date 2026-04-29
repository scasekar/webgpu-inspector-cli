"""Main CLI entry point for WebGPU Inspector CLI."""

import sys

import click

from webgpu_inspector_cli.commands.browser import browser
from webgpu_inspector_cli.commands.objects import objects
from webgpu_inspector_cli.commands.capture import capture
from webgpu_inspector_cli.commands.shaders import shaders
from webgpu_inspector_cli.commands.errors import errors
from webgpu_inspector_cli.commands.status import status


@click.group(invoke_without_command=True)
@click.option("--json", "use_json", is_flag=True, help="Output in JSON format.")
@click.option(
    "--commands-from",
    "commands_from",
    type=click.Path(),
    default=None,
    help=(
        "Read commands from a file (one per line) and run them in a single "
        "Bridge session. Pass '-' to read from stdin. Lines starting with '#' "
        "and blank lines are ignored. Useful for non-TTY use where the REPL "
        "isn't viable (e.g. piping commands from a script or an agent harness)."
    ),
)
@click.pass_context
def cli(ctx, use_json, commands_from):
    """WebGPU Inspector CLI - Debug WebGPU applications from the command line."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = use_json
    if ctx.invoked_subcommand is None:
        if commands_from:
            ctx.invoke(batch, source=commands_from)
        else:
            ctx.invoke(repl)


@cli.command(hidden=True)
@click.argument("source", type=click.Path())
@click.pass_context
def batch(ctx, source):
    """Internal: run commands from a file (or '-' for stdin) in one process.

    Exposed via `--commands-from`, not invoked directly. Each line is parsed
    via shlex and dispatched through the same Click router the REPL uses, so
    the Bridge singleton persists across the whole script.
    """
    import shlex

    if source == "-":
        stream = sys.stdin
        close_after = False
    else:
        stream = open(source, "r")
        close_after = True

    exit_code = 0
    try:
        for raw_line in stream:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                args = shlex.split(line)
            except ValueError as exc:
                click.echo(f"parse error: {exc}", err=True)
                exit_code = 2
                continue
            click.echo(f">>> {line}")
            try:
                cli.main(args, standalone_mode=False)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else (1 if exc.code else 0)
                if code:
                    exit_code = code
            except click.exceptions.UsageError as exc:
                click.echo(f"usage: {exc}", err=True)
                exit_code = 2
            except RuntimeError as exc:
                click.echo(f"error: {exc}", err=True)
                exit_code = 2
            except Exception as exc:  # noqa: BLE001
                click.echo(f"error: {exc}", err=True)
                exit_code = 1
    finally:
        if close_after:
            stream.close()

    if exit_code:
        sys.exit(exit_code)


@cli.command()
def repl():
    """Start interactive REPL mode."""
    try:
        from webgpu_inspector_cli.utils.repl_skin import ReplSkin
    except ImportError:
        click.echo("REPL mode requires prompt_toolkit. Install with: pip install prompt_toolkit")
        return

    from webgpu_inspector_cli import __version__

    skin = ReplSkin("webgpu-inspector", version=__version__)
    skin.print_banner()

    commands = {
        "browser": "Browser session management (launch, close, navigate, screenshot, status, eval, click, type, wait)",
        "objects": "GPU object inspection (list, inspect, search, memory)",
        "capture": "Frame capture and data inspection (frame, commands, texture, buffer)",
        "shaders": "Shader module inspection (list, view, compile, revert)",
        "errors": "Validation error tracking (list, watch, clear)",
        "status": "Runtime monitoring (summary, fps, memory)",
        "help": "Show this help",
        "exit": "Exit REPL",
    }

    pt_session = skin.create_prompt_session()

    while True:
        try:
            line = skin.get_input(pt_session)
            if not line:
                continue
            line = line.strip()
            if line in ("exit", "quit", "q"):
                break
            if line == "help":
                skin.help(commands)
                continue

            # Split line into args and invoke via Click
            import shlex
            try:
                args = shlex.split(line)
            except ValueError as e:
                skin.error(f"Parse error: {e}")
                continue

            try:
                cli.main(args, standalone_mode=False)
            except SystemExit:
                pass
            except click.exceptions.UsageError as e:
                skin.error(str(e))
            except RuntimeError as e:
                # Friendly bridge errors and similar runtime errors — no traceback.
                skin.error(str(e))
            except Exception as e:
                skin.error(str(e))

        except (KeyboardInterrupt, EOFError):
            break

    skin.print_goodbye()


cli.add_command(browser)
cli.add_command(objects)
cli.add_command(capture)
cli.add_command(shaders)
cli.add_command(errors)
cli.add_command(status)


def main():
    """Console-script entry point.

    Wraps `cli` so that RuntimeError from bridge.require_bridge (no active
    session) prints a friendly message instead of a Python traceback.
    """
    try:
        cli(standalone_mode=False)
    except click.exceptions.UsageError as exc:
        exc.show()
        sys.exit(exc.exit_code)
    except click.exceptions.Abort:
        sys.exit(1)
    except SystemExit:
        raise
    except RuntimeError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)
