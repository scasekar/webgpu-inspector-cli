# WebGPU Inspector CLI

A command-line interface for the [WebGPU Inspector](https://github.com/brendan-duncan/webgpu_inspector), enabling AI agents and developers to debug WebGPU applications programmatically.

The CLI launches a browser, injects the WebGPU Inspector directly into any web page, and provides structured access to all GPU state — objects, shaders, textures, buffers, validation errors, frame captures, and performance metrics.

## Installation

**Requirements:** Python 3.10+, Chrome/Chromium

```bash
git clone --recurse-submodules https://github.com/scasekar/webgpu-inspector-cli
cd webgpu-inspector-cli/agent-harness
pip install -e .
python -m playwright install chromium
```

Verify the installation:

```bash
cli-anything-webgpu-inspector --help
```

## Quick Start

```bash
# Launch browser and start inspecting
cli-anything-webgpu-inspector browser launch --url https://your-webgpu-app.com

# Check for validation errors
cli-anything-webgpu-inspector --json errors list

# List all GPU objects
cli-anything-webgpu-inspector --json objects list

# View a shader's WGSL source
cli-anything-webgpu-inspector shaders view --id 8

# Get a full status summary
cli-anything-webgpu-inspector --json status summary

# Close the browser when done
cli-anything-webgpu-inspector browser close
```

## How It Works

```
┌─────────────────────────────────────────────────────┐
│  Python CLI (Click)                                 │
│  ┌───────────────────────────────────────────────┐  │
│  │  Bridge (Playwright)                          │  │
│  │  ┌─────────────────────────────────────────┐  │  │
│  │  │  Chrome                                 │  │  │
│  │  │  ┌──────────────┐  ┌─────────────────┐  │  │  │
│  │  │  │ webgpu_      │  │ collector.js    │  │  │  │
│  │  │  │ inspector.js │──│ (event listener │  │  │  │
│  │  │  │ (injected)   │  │  + state store) │  │  │  │
│  │  │  └──────────────┘  └─────────────────┘  │  │  │
│  │  │         │                   ▲            │  │  │
│  │  │         │ CustomEvent       │ query()    │  │  │
│  │  │         ▼                   │            │  │  │
│  │  │    __WebGPUInspector ───────┘            │  │  │
│  │  └─────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

1. **Playwright** launches Chrome with WebGPU enabled
2. The built `webgpu_inspector.js` from the [submodule](https://github.com/brendan-duncan/webgpu_inspector) is injected into the page — the same code the Chrome DevTools extension uses
3. A **collector.js** script listens for `__WebGPUInspector` CustomEvents and accumulates GPU state (objects, errors, memory, frame rate, captures)
4. The Python CLI queries the collector via `page.evaluate()` and returns structured JSON

This gives you the same inspection capabilities as the DevTools panel, without needing the extension installed.

## JSON Output

All commands support the `--json` flag for machine-readable output. Place it on the root command:

```bash
cli-anything-webgpu-inspector --json objects list
cli-anything-webgpu-inspector --json status summary
cli-anything-webgpu-inspector --json errors list
```

Example output from `--json objects list`:

```json
{
  "objects": [
    {
      "id": 8,
      "type": "ShaderModule",
      "label": null,
      "descriptor": { "code": "@vertex\nfn vertexMain..." },
      "stacktrace": "main (app.js:33:36)",
      "parent": 2,
      "pending": false,
      "size": 411
    }
  ],
  "count": 1
}
```

## REPL Mode

Running the CLI without a subcommand enters an interactive REPL with command history:

```bash
cli-anything-webgpu-inspector
```

## Command Reference

### `browser` — Session Lifecycle

| Command | Description |
|---------|-------------|
| `browser launch --url URL` | Launch Chrome, navigate to URL, inject inspector |
| `browser close` | Shut down the browser session |
| `browser navigate --url URL` | Navigate to a new URL and re-inject |
| `browser screenshot -o PATH` | Take a screenshot of the current page |
| `browser status` | Show browser URL, title, and GPU availability |

**Options for `browser launch`:**

| Flag | Description |
|------|-------------|
| `--url URL` | URL to navigate to (required) |
| `--headless` | Run in headless mode (needs GPU or `--gpu-backend swiftshader`) |
| `--gpu-backend TEXT` | GPU backend override (e.g., `swiftshader` for software rendering) |

### `objects` — GPU Object Inspection

| Command | Description |
|---------|-------------|
| `objects list [--type TYPE]` | List all live GPU objects, optionally filtered by type |
| `objects inspect --id ID` | Full details: descriptor, creation stacktrace, parent, size |
| `objects search --label PATTERN` | Find objects by label substring (case-insensitive) |
| `objects memory` | GPU memory breakdown (texture + buffer totals) |

**Supported object types for `--type`:**
Adapter, Device, Buffer, Texture, TextureView, Sampler, ShaderModule, BindGroup, BindGroupLayout, PipelineLayout, RenderPipeline, ComputePipeline, RenderBundle

### `capture` — Frame Capture & Data Inspection

| Command | Description |
|---------|-------------|
| `capture frame` | Capture the next frame's GPU commands |
| `capture commands [--pass-index N]` | List GPU commands from the captured frame |
| `capture texture --id ID [-o PATH]` | Read texture pixel data, optionally save as PNG |
| `capture buffer --id ID` | Read buffer contents |

**Options for `capture frame`:**

| Flag | Default | Description |
|------|---------|-------------|
| `--timeout FLOAT` | 30.0 | Seconds to wait for capture to complete |
| `--poll-interval FLOAT` | 0.5 | Seconds between status polls |

**Options for `capture texture`:**

| Flag | Default | Description |
|------|---------|-------------|
| `--id INTEGER` | — | Texture object ID (required) |
| `--mip-level INTEGER` | 0 | Mip level to capture |
| `-o, --output PATH` | — | Save as PNG (or raw bytes if not .png) |
| `--timeout FLOAT` | 30.0 | Seconds to wait for data |

**Options for `capture buffer`:**

| Flag | Default | Description |
|------|---------|-------------|
| `--id INTEGER` | — | Buffer object ID (required) |
| `--offset INTEGER` | 0 | Byte offset into buffer |
| `--size INTEGER` | — | Number of bytes to read |
| `--format` | hex | Display format: `hex`, `float32`, `uint32`, `raw` |

### `shaders` — Shader Inspection & Hot-Reload

| Command | Description |
|---------|-------------|
| `shaders list` | List all shader modules with size |
| `shaders view --id ID` | Display full WGSL source code |
| `shaders compile --id ID --file PATH` | Hot-replace shader code from a file |
| `shaders compile --id ID --code "..."` | Hot-replace shader code from a string |
| `shaders revert --id ID` | Revert shader to its original code |

Shader edits support undo — the original code is saved before each compile so `revert` can restore it.

### `errors` — Validation Error Tracking

| Command | Description |
|---------|-------------|
| `errors list` | List all validation errors with messages and stacktraces |
| `errors watch [--timeout N]` | Poll for new errors in real-time (default 30s) |
| `errors clear` | Clear the error history |

**Options for `errors watch`:**

| Flag | Default | Description |
|------|---------|-------------|
| `--timeout FLOAT` | 30.0 | Seconds to watch |
| `--poll-interval FLOAT` | 1.0 | Seconds between polls |

### `status` — Runtime Monitoring

| Command | Description |
|---------|-------------|
| `status summary` | Object counts by type, memory, FPS, error count |
| `status fps` | Current frame rate and frame time |
| `status memory` | GPU memory breakdown (texture + buffer) |

## Debugging Workflows

### Diagnosing Validation Errors

```bash
cli-anything-webgpu-inspector browser launch --url http://localhost:8080
cli-anything-webgpu-inspector --json errors list
```

Each error includes the validation message and the creation stacktrace pinpointing the offending API call.

### Investigating Missing or Incorrect Rendering

```bash
# What GPU objects exist?
cli-anything-webgpu-inspector --json objects list

# Is the expected pipeline there?
cli-anything-webgpu-inspector --json objects list --type RenderPipeline

# Inspect its descriptor (vertex/fragment config, blend state, etc.)
cli-anything-webgpu-inspector --json objects inspect --id 9

# Look at the shader it uses
cli-anything-webgpu-inspector shaders view --id 8
```

### Shader Debugging with Hot-Reload

```bash
# View current shader
cli-anything-webgpu-inspector shaders view --id 8

# Edit locally, then hot-reload
cli-anything-webgpu-inspector shaders compile --id 8 --file fixed_shader.wgsl

# Didn't work? Revert
cli-anything-webgpu-inspector shaders revert --id 8
```

### Inspecting Render Targets

```bash
# Capture a frame
cli-anything-webgpu-inspector --json capture frame

# List all textures
cli-anything-webgpu-inspector --json objects list --type Texture

# Save a render target as PNG for visual inspection
cli-anything-webgpu-inspector capture texture --id 6 -o render_target.png
```

### Performance Profiling

```bash
cli-anything-webgpu-inspector --json status summary
cli-anything-webgpu-inspector status fps
cli-anything-webgpu-inspector --json status memory
cli-anything-webgpu-inspector --json objects list --type Buffer   # find large buffers
cli-anything-webgpu-inspector --json objects list --type Texture  # find large textures
```

## Claude Code Plugin

A [Claude Code plugin](https://github.com/scasekar/webgpu-inspector-plugin) is available that adds a `/webgpu-inspect` command, a `webgpu-inspector` skill, and a `webgpu-debugger` agent to Claude Code:

```
/plugin marketplace add scasekar/webgpu-inspector-plugin
/plugin install webgpu-inspector
/reload-plugins
```

## Project Structure

```
webgpu-inspector-cli/
├── webgpu_inspector/              # Git submodule (WebGPU Inspector source)
├── agent-harness/
│   ├── setup.py                   # Package config
│   └── cli_anything/
│       └── webgpu_inspector/
│           ├── webgpu_inspector_cli.py   # CLI entry point
│           ├── core/
│           │   ├── bridge.py      # Playwright CDP bridge
│           │   └── session.py     # Shader edit undo/redo
│           ├── commands/          # Click command groups
│           │   ├── browser.py
│           │   ├── objects.py
│           │   ├── capture.py
│           │   ├── shaders.py
│           │   ├── errors.py
│           │   └── status.py
│           ├── js/
│           │   └── collector.js   # Injected event collector
│           └── tests/
│               ├── test_core.py       # 19 unit tests
│               └── test_full_e2e.py   # 16 E2E tests
└── examples/
```

## Running Tests

```bash
cd agent-harness

# Unit tests (fast, no browser)
pytest cli_anything/webgpu_inspector/tests/test_core.py -v

# E2E tests (launches real browser with test pages)
pytest cli_anything/webgpu_inspector/tests/test_full_e2e.py -v -s

# All tests
pytest cli_anything/webgpu_inspector/tests/ -v -s
```

## License

MIT

## Credits

- [WebGPU Inspector](https://github.com/brendan-duncan/webgpu_inspector) by Brendan Duncan
- Built with the [cli-anything](https://github.com/HKUDS/CLI-Anything) methodology
