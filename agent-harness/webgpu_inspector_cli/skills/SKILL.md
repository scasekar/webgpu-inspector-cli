---
name: "webgpu-inspector-cli"
description: "Debug WebGPU applications from the command line - inspect GPU objects, view shaders, capture frames, check validation errors. Includes an MCP server for agent use."
---

# webgpu-inspector

Debug WebGPU applications. Two interfaces share the same Bridge:

- **`webgpu-inspector-mcp`** — MCP server. Long-lived process that any MCP-capable client (Claude Code, Claude Desktop, Cursor) can drive. **Recommended for agent-driven use** because it keeps a single browser session alive across tool calls.
- **`webgpu-inspector-cli`** — CLI. One process per invocation, so multi-step shell flows must run inside `repl` (otherwise `browser launch` and a follow-up `capture frame` get separate browsers and the second one fails with "no active browser session").

## Prerequisites

```bash
pip install webgpu-inspector-cli
python -m playwright install chromium
```

## Lifetime gotcha (CLI only)

Each bare `webgpu-inspector-cli ...` invocation starts a fresh Python process and a fresh browser. So this **does NOT work**:

```bash
webgpu-inspector-cli browser launch --url ...
webgpu-inspector-cli capture frame                # FAILS: separate process, no session
```

Use `repl` for terminal multi-step work, or use the MCP server.

## CLI REPL workflow

```bash
webgpu-inspector-cli                              # enters REPL
> browser launch --url https://your-app.com --capture-console /tmp/console.log
> browser wait --condition 'window._renderer !== undefined' --timeout 10
> browser eval --js 'document.title'
> browser click 'button.load-scene'
> --json errors list
> --json status summary
> --json objects list --type Buffer               # decoded usage flags shown
> shaders view --id 8
> capture frame
> capture buffer --id 42 --format f32-mat4
> capture buffer --id 42 --struct 'mat4x4 anchorToWorld; u32 chunkId; pad12'
> capture texture --id 6 -o debug.png
> shaders compile --id 8 --file fixed_shader.wgsl
> browser close
> exit
```

`--capture-console <path>` attaches a console listener BEFORE navigation so page-bootstrap logs are captured.

## MCP workflow (agent use)

Configure once in your client (e.g. `~/.claude/mcp.json`):

```json
{ "mcpServers": { "webgpu-inspector": { "command": "webgpu-inspector-mcp" } } }
```

Then call tools by name. The browser stays alive across calls.

```
browser_launch(url=..., capture_console_path="/tmp/log.txt")
browser_wait(condition="window._renderer !== undefined")
browser_eval(js="window._renderer.objectCount")
errors_list()
objects_list(type="Buffer")               # entries include 'usageFlags'
capture_frame()
capture_buffer(id=42, format="f32-mat4")
capture_buffer(id=42, struct_spec="mat4x4 m; u32 chunkId; pad12")
shaders_replace(id=8, code="...new WGSL...")
browser_close()
```

## Page-driving primitives

These solve the "agent can't drive my app past initial load" problem. No need to bake `?autoload=1` URL hacks into your app.

| Purpose | MCP | CLI |
|---|---|---|
| Run JS, get value | `browser_eval(js="...")` | `browser eval --js '...'` (or `--file path.js`) |
| Click element | `browser_click(selector)` | `browser click '<sel>'` |
| Type into input | `browser_type(selector, text)` | `browser type '<sel>' '<text>'` |
| Wait for truthy | `browser_wait(condition, timeout_seconds)` | `browser wait --condition '...' --timeout N` |
| Capture pre-bootstrap console | `browser_launch(capture_console_path=...)` | `browser launch --capture-console <path>` |
| Persistent profile | `browser_launch(user_data_dir=...)` | `browser launch --user-data-dir <path>` |

## Buffer decoding

`capture_buffer` / `capture buffer` requires a prior `capture_frame` — buffer data is collected during frame capture, not on demand.

Formats: `hex` (default), `hex-dump`, `u32-list`, `i32-list`, `f32-list`, `f32-mat4`, `raw` (base64).

Struct decoder for ad-hoc record shapes:
```
'mat4x4 anchorToWorld; u32 chunkIdDebug; pad12; vec3 origin; f32 scale'
```
Primitives: `u8/i8/u16/i16/u32/i32/u64/i64/f32/f64/bool`. Vectors: `vec2/vec3/vec4` (f32). Matrices: `mat2x2/mat3x3/mat4x4` (f32 column-major). Skip with `padN`.

## Tool / Command reference

| MCP tool | CLI equivalent |
|---|---|
| `browser_launch` | `browser launch` |
| `browser_close` | `browser close` |
| `browser_navigate` | `browser navigate` |
| `browser_screenshot` | `browser screenshot` |
| `browser_status` | `browser status` |
| `browser_eval` | `browser eval` |
| `browser_click` | `browser click` |
| `browser_type` | `browser type` |
| `browser_wait` | `browser wait` |
| `objects_list` | `objects list` |
| `objects_inspect` | `objects inspect` |
| `objects_search` | `objects search` |
| `objects_memory` | `objects memory` |
| `capture_frame` | `capture frame` |
| `capture_commands` | `capture commands` |
| `capture_texture` | `capture texture` |
| `capture_buffer` | `capture buffer` |
| `shaders_list` | `shaders list` |
| `shaders_view` | `shaders view` |
| `shaders_replace` | `shaders compile` |
| `shaders_revert` | `shaders revert` |
| `errors_list` | `errors list` |
| `errors_clear` | `errors clear` |
| `status_summary` | `status summary` |

## Object types (for `--type` / `type=`)

Adapter, Device, Buffer, Texture, TextureView, Sampler, ShaderModule, BindGroup, BindGroupLayout, PipelineLayout, RenderPipeline, ComputePipeline, RenderBundle.

## Agent guidance

- For multi-step work, **use MCP**. The CLI's per-invocation lifetime is the #1 source of "no active browser session" errors.
- Always pass `--json` to CLI subcommands when parsing output.
- Frame capture is async — `capture frame` polls until complete (default 30s).
- Use `browser eval` / `browser click` to drive apps that need interaction past initial load — don't ship URL-param load hacks to do this.
