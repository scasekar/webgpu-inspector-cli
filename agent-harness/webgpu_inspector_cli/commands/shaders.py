"""Shader module inspection commands."""

import json
import click

from webgpu_inspector_cli.core.bridge import require_bridge
from webgpu_inspector_cli.core.session import get_session


@click.group()
def shaders():
    """Shader module inspection."""
    pass


@shaders.command("list")
@click.pass_context
def list_shaders(ctx):
    """List all shader modules."""
    bridge = require_bridge()
    result = bridge.query("getObjects", "ShaderModule")

    if ctx.obj.get("json"):
        click.echo(json.dumps({"shaders": result, "count": len(result)}, indent=2))
    else:
        if not result:
            click.echo("No shader modules found.")
            return
        click.echo(f"{'ID':>6}  {'Label':<30}  {'Size':>10}")
        click.echo("-" * 50)
        for obj in result:
            label = obj.get("label") or ""
            size = obj.get("size", 0)
            click.echo(f"{obj['id']:>6}  {label:<30}  {size:>10} chars")
        click.echo(f"\nTotal: {len(result)} shader modules")


@shaders.command()
@click.option("--id", "shader_id", type=int, required=True, help="Shader module ID.")
@click.pass_context
def view(ctx, shader_id):
    """View WGSL source code of a shader module."""
    bridge = require_bridge()
    code = bridge.query("getShaderCode", shader_id)

    if code is None:
        click.echo(f"Shader #{shader_id} not found or has no code.", err=True)
        raise SystemExit(1)

    if ctx.obj.get("json"):
        click.echo(json.dumps({"shaderId": shader_id, "code": code}, indent=2))
    else:
        click.echo(f"--- Shader #{shader_id} ---")
        click.echo(code)
        click.echo(f"--- End ({len(code)} chars) ---")


@shaders.command("compile")
@click.option("--id", "shader_id", type=int, required=True, help="Shader module ID.")
@click.option("--file", "code_file", type=click.Path(exists=True), default=None,
              help="Path to WGSL file.")
@click.option("--code", "code_str", type=str, default=None,
              help="WGSL code string (alternative to --file).")
@click.pass_context
def compile_shader(ctx, shader_id, code_file, code_str):
    """Hot-replace shader code with new WGSL source."""
    if not code_file and not code_str:
        click.echo("Provide either --file or --code.", err=True)
        raise SystemExit(1)

    bridge = require_bridge()

    # Save original for undo
    session = get_session()
    original = bridge.query("getShaderCode", shader_id)
    if original:
        session.push_shader_edit(shader_id, original)

    if code_file:
        with open(code_file) as f:
            code = f.read()
    else:
        code = code_str

    bridge.query("compileShader", shader_id, code)

    if ctx.obj.get("json"):
        click.echo(json.dumps({"status": "compiled", "shaderId": shader_id, "codeLength": len(code)}))
    else:
        click.echo(f"Shader #{shader_id} recompiled ({len(code)} chars)")


@shaders.command("revert")
@click.option("--id", "shader_id", type=int, required=True, help="Shader module ID.")
@click.pass_context
def revert_shader(ctx, shader_id):
    """Revert shader to its original code."""
    bridge = require_bridge()
    bridge.query("revertShader", shader_id)

    # Also clear session history for this shader
    session = get_session()
    session.clear_shader_edits(shader_id)

    if ctx.obj.get("json"):
        click.echo(json.dumps({"status": "reverted", "shaderId": shader_id}))
    else:
        click.echo(f"Shader #{shader_id} reverted to original.")
