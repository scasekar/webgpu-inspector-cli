# WebGPU Inspector CLI + MCP Server

Debug WebGPU applications programmatically. Two interfaces share the same Bridge:

- **`webgpu-inspector-mcp`** — MCP server. Long-lived process that any MCP-capable LLM client (Claude Code, Claude Desktop, Cursor) can drive. **Recommended for agent-driven use** because the browser session persists for the lifetime of the server.
- **`webgpu-inspector-cli`** — terminal CLI + REPL. Each bare invocation is a fresh process, so for multi-step shell flows use the REPL (no subcommand) or use the MCP server.

Both expose the same surface: launching Chromium with WebGPU, injecting the [WebGPU Inspector](https://github.com/brendan-duncan/webgpu_inspector), and providing structured access to GPU state — objects, shaders, textures, buffers, validation errors, frame captures, and performance metrics — plus page-driving primitives (`eval`, `click`, `type`, `wait`) and a buffer struct decoder.

## Installation

**Requirements:** Python 3.10+, Chrome/Chromium

```bash
pip install webgpu-inspector-cli
python -m playwright install chromium
```

Or from source:
```bash
git clone --recurse-submodules https://github.com/scasekar/webgpu-inspector-cli
cd webgpu-inspector-cli/agent-harness
pip install -e .
python -m playwright install chromium
```

This installs two executables: `webgpu-inspector-cli` (terminal) and `webgpu-inspector-mcp` (server).

Verify:
```bash
webgpu-inspector-cli --help
webgpu-inspector-mcp --help     # rarely useful — clients invoke this directly
```

## Lifecycle: each CLI invocation is independent

Each `webgpu-inspector-cli ...` call starts a fresh Python process and a fresh browser, then exits. So this **does not work**:

```bash
webgpu-inspector-cli browser launch --url https://...
webgpu-inspector-cli capture frame                # FAILS: browser is gone
```

Use one of:
- **MCP server** — tools share state automatically
- **REPL** — `webgpu-inspector-cli` (no subcommand)
- **Custom Python** — `from webgpu_inspector_cli.core.bridge import get_bridge`

## Configure the MCP server

**Claude Code** — add to `~/.claude/mcp.json` or project `.mcp.json`:

```json
{
  "mcpServers": {
    "webgpu-inspector": {
      "command": "webgpu-inspector-mcp"
    }
  }
}
```

**Claude Desktop** — add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or the platform equivalent:

```json
{
  "mcpServers": {
    "webgpu-inspector": {
      "command": "webgpu-inspector-mcp"
    }
  }
}
```

Restart the client. Tools appear as `browser_launch`, `browser_eval`, `capture_frame`, `capture_buffer`, etc.

## Quick Start (REPL)

```bash
webgpu-inspector-cli                              # enter REPL
> browser launch --url https://your-app.com --capture-console /tmp/console.log
> browser wait --condition 'window._renderer !== undefined' --timeout 10
> browser click 'button.load-scene'
> --json errors list
> --json objects list --type Buffer               # decoded usage flags
> capture frame
> capture buffer --id 42 --format f32-mat4
> capture buffer --id 42 --struct 'mat4x4 m; u32 chunkId; pad12'
> shaders compile --id 8 --file fixed.wgsl
> browser close
> exit
```

## How It Works

```
┌─────────────────────────────────────────────────────────┐
│  MCP server (webgpu-inspector-mcp)  +  CLI (Click)      │
│       └────────────┬───────────────┘                    │
│                    ▼                                    │
│              Bridge (Playwright, singleton)             │
│                    │                                    │
│                    ▼                                    │
│           ┌─────────────────────────────┐               │
│           │  Chromium (WebGPU enabled)  │               │
│           │  ┌──────────┐ ┌──────────┐  │               │
│           │  │ inspector│ │collector │  │               │
│           │  │ .js      │─│ .js      │  │               │
│           │  └──────────┘ └──────────┘  │               │
│           └─────────────────────────────┘               │
└─────────────────────────────────────────────────────────┘
```

1. **Playwright** launches Chromium with `--enable-unsafe-webgpu --enable-features=Vulkan`.
2. The built `webgpu_inspector_loader.js` from the [submodule](https://github.com/brendan-duncan/webgpu_inspector) is injected — same code the Chrome DevTools extension uses.
3. **collector.js** listens for `__WebGPUInspector` CustomEvents and accumulates GPU state.
4. The **Bridge** queries the collector via `page.evaluate()` and returns structured data to either the CLI commands or the MCP tools.

The MCP server holds the Bridge for its full lifetime. The CLI binds the Bridge to a single Python process — so REPL is the multi-step CLI flow.

## Heavy WebGPU workloads — when to disable the inspector

The WebGPU Inspector hooks every `createBuffer`, `createBindGroup`, `writeBuffer`, etc. with a synchronous wrapper that runs `JSON.stringify(descriptor)` and dispatches a `CustomEvent` on the main thread. Apps that issue large bursts of WebGPU operations (e.g. gsplat LOD streaming: ~50 `createBuffer` + `writeBuffer` + `createBindGroup` cycles inside ~2 s) can overload that bookkeeping path. On macOS this has been observed to trigger a hard kernel-class crash requiring a cold reboot. See `UPSTREAM-ISSUE-DRAFT.md` for the upstream-side fixes.

**Mitigations shipped in v0.3.0:**

1. **`inspector=False` opt-out** — skip injection entirely when you only need Playwright control + console capture:

   ```python
   from webgpu_inspector_cli.core.bridge import Bridge
   b = Bridge()
   b.launch("https://your-app.com", inspector=False, capture_console_path="/tmp/console.log")
   ```

   Same flag on the MCP tool: `browser_launch(url=..., inspector=False)`.

2. **In-page burst guard** — when `inspector=True` (default), a setter-trap script wraps the inspector's hooks with a rolling-window throttle. Under burst (>200 hooked calls within 100 ms), excess calls bypass the inspector wrapper and call the real GPU method directly — the GPU work proceeds, the inspector just misses bookkeeping for the dropped calls. Read counters from the page:

   ```js
   window.__wgi.stats()
   // { hookInvocations, droppedDueToBurst, perMethod, options: { maxHooksPerWindow, windowMs } }
   ```

   Tune via `window.__wgiOptions = { maxHooksPerWindow: 500, windowMs: 100 }` set BEFORE the inspector loads (e.g. in a `<script>` near the top of `<head>`).

3. **Stderr warning at launch** — every `Bridge.launch()` with the inspector active prints a one-line warning so the opt-out is discoverable.

If you keep hitting `droppedDueToBurst > 0` and need full inspection coverage, the right fix is upstream — see `UPSTREAM-ISSUE-DRAFT.md`.

## CLI / MCP command surface

Every CLI command has a 1:1 MCP tool counterpart. The MCP tool names are the underscore form (`browser_launch` ↔ `browser launch`).

### `browser` — Session lifecycle + page driving

| Command | Purpose |
|---|---|
| `browser launch --url URL` | Launch Chromium, navigate, inject inspector |
| `browser launch --capture-console PATH` | Also: write all console messages to `PATH` (listener attached BEFORE navigation, so page-bootstrap logs are captured) |
| `browser launch --user-data-dir PATH` | Use a persistent Chrome profile (cookies, localStorage, extensions) |
| `browser launch --headless` | Headless (needs GPU or `--gpu-backend swiftshader`) |
| `browser close` | Shut down the session |
| `browser navigate --url URL` | Navigate + re-inject |
| `browser screenshot -o PATH` | Save page screenshot |
| `browser status` | URL, title, GPU availability |
| `browser eval --js '<expr>'` | Run JS in page; returns the value |
| `browser eval --file PATH` | Run a `.js` file |
| `browser click '<selector>'` | Click DOM element (CSS or Playwright selector) |
| `browser type '<selector>' '<text>'` | Type into input |
| `browser wait --condition '<js>'` | Block until expression is truthy |

### `objects` — GPU object inspection

| Command | Purpose |
|---|---|
| `objects list [--type TYPE]` | List GPU objects. `--type Buffer` includes a decoded **usage flags** column (Storage / Indirect / CopyDst / etc.) |
| `objects inspect --id ID` | Full descriptor + creation stacktrace |
| `objects search --label PATTERN` | Find by label substring |
| `objects memory` | Texture + buffer totals |

Object types: Adapter, Device, Buffer, Texture, TextureView, Sampler, ShaderModule, BindGroup, BindGroupLayout, PipelineLayout, RenderPipeline, ComputePipeline, RenderBundle.

### `capture` — Frame capture & data inspection

| Command | Purpose |
|---|---|
| `capture frame` | Capture next frame's GPU commands |
| `capture commands [--pass-index N]` | List captured GPU commands |
| `capture texture --id ID [-o PATH]` | Read texture pixels, optionally save as PNG |
| `capture buffer --id ID [--format FMT]` | **Requires a prior `capture frame`.** Read buffer data from the captured frame. |
| `capture buffer --id ID --struct '<spec>'` | Decode buffer as repeated records |

**Buffer formats:** `hex` (default), `hex-dump` (xxd-style), `u32-list`, `i32-list`, `f32-list`, `f32-mat4`, `raw` (base64).

**Struct spec:** `'mat4x4 anchorToWorld; u32 chunkIdDebug; pad12; vec3 origin; f32 scale'`. Supports `u8/i8/u16/i16/u32/i32/u64/i64/f32/f64/bool`, `vec2/vec3/vec4` (f32), `mat2x2/mat3x3/mat4x4` (f32, column-major), `padN`.

### `shaders` — Inspection & hot-reload

| Command | Purpose |
|---|---|
| `shaders list` | List shader modules with size |
| `shaders view --id ID` | Display WGSL source |
| `shaders compile --id ID --file PATH` | Hot-replace shader code from a file |
| `shaders compile --id ID --code "..."` | Hot-replace from a string |
| `shaders revert --id ID` | Revert to original |

### `errors` — Validation errors

| Command | Purpose |
|---|---|
| `errors list` | All errors with messages and stacktraces |
| `errors watch [--timeout N]` | Stream new errors in real time (CLI only) |
| `errors clear` | Reset history |

### `status` — Runtime monitoring

| Command | Purpose |
|---|---|
| `status summary` | Object counts, FPS, memory, error count |
| `status fps` | Current frame rate |
| `status memory` | GPU memory breakdown |

## JSON output (CLI)

All commands support `--json`:

```bash
webgpu-inspector-cli --json objects list
webgpu-inspector-cli --json status summary
webgpu-inspector-cli --json errors list
```

MCP tools return JSON natively — no flag needed.

## Debugging Workflows

### Diagnosing validation errors

```
errors_list()                                # MCP
> --json errors list                         # CLI REPL
```

Each error includes the validation message + creation stacktrace pinpointing the offending API call.

### Driving an app that needs interaction past initial load

Use the page-driving primitives instead of adding `?autoload=1` URL params to your app:

```
browser_launch(url=...)
browser_wait(condition="window.app && window.app.ready")
browser_eval(js="window.app.loadScene('demo')")
browser_click(selector="button.start")
```

### Inspecting buffer contents

Frame capture must run first — buffer data is populated via `mapAsync` during capture, not on demand.

```
capture_frame()
capture_buffer(id=42, format="f32-mat4")                     # 4×4 matrices
capture_buffer(id=42, struct_spec="u32 chunkId; vec3 pos")   # decoded records
```

### Hot-reloading shaders

```
shaders_view(id=8)                                   # read current
shaders_replace(id=8, code="<new WGSL>")             # try a fix
shaders_revert(id=8)                                 # rollback
```

### Inspecting render targets

```
capture_frame()
capture_texture(id=6, output_path="rt.png")
```

## Project Structure

```
webgpu-inspector-cli/
├── webgpu_inspector/                    # Git submodule (WebGPU Inspector source)
├── agent-harness/
│   ├── setup.py                         # Package config (CLI + MCP entry points)
│   └── webgpu_inspector_cli/
│       ├── webgpu_inspector_cli.py      # CLI entry point (Click)
│       ├── core/
│       │   ├── bridge.py                # Playwright bridge (singleton)
│       │   └── session.py               # Shader edit undo/redo
│       ├── commands/                    # Click command groups
│       │   ├── browser.py               # incl. eval/click/type/wait
│       │   ├── objects.py
│       │   ├── capture.py               # incl. struct decoder
│       │   ├── shaders.py
│       │   ├── errors.py
│       │   └── status.py
│       ├── mcp_server/                  # MCP server
│       │   ├── server.py                # Tool definitions
│       │   └── __main__.py
│       ├── utils/
│       │   ├── buffer_decoders.py       # Format decoders + struct spec parser
│       │   └── repl_skin.py             # REPL UI
│       ├── js/
│       │   └── collector.js             # Injected event collector
│       └── tests/
│           ├── test_core.py
│           ├── test_buffer_decoders.py
│           ├── test_mcp_server.py
│           └── test_full_e2e.py
└── examples/
```

## Running Tests

```bash
cd agent-harness

# Unit tests (fast, no browser)
pytest webgpu_inspector_cli/tests/test_core.py -v
pytest webgpu_inspector_cli/tests/test_buffer_decoders.py -v
pytest webgpu_inspector_cli/tests/test_mcp_server.py -v

# E2E tests (real browser, requires GPU or --gpu-backend swiftshader)
pytest webgpu_inspector_cli/tests/test_full_e2e.py -v -s
```

## Claude Code Plugin

A [Claude Code plugin](https://github.com/scasekar/webgpu-inspector-plugin) packages this with a skill, slash command, and subagent:

```
/plugin marketplace add scasekar/webgpu-inspector-plugin
/plugin install webgpu-inspector
/reload-plugins
```

## License

MIT

## Credits

- [WebGPU Inspector](https://github.com/brendan-duncan/webgpu_inspector) by Brendan Duncan
- Built on [Playwright](https://playwright.dev/) and the [Model Context Protocol](https://modelcontextprotocol.io/)
