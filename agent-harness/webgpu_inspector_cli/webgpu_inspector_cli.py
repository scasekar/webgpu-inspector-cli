"""Main CLI entry point for WebGPU Inspector CLI."""

import click

from webgpu_inspector_cli.commands.browser import browser
from webgpu_inspector_cli.commands.objects import objects
from webgpu_inspector_cli.commands.capture import capture
from webgpu_inspector_cli.commands.shaders import shaders
from webgpu_inspector_cli.commands.errors import errors
from webgpu_inspector_cli.commands.status import status


@click.group(invoke_without_command=True)
@click.option("--json", "use_json", is_flag=True, help="Output in JSON format.")
@click.pass_context
def cli(ctx, use_json):
    """WebGPU Inspector CLI - Debug WebGPU applications from the command line."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = use_json
    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


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
        "browser": "Browser session management (launch, close, navigate, screenshot, status)",
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
