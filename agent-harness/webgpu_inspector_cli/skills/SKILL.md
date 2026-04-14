---
name: "webgpu-inspector-cli"
description: "Debug WebGPU applications from the command line - inspect GPU objects, view shaders, capture frames, check validation errors"
---

# webgpu-inspector-cli

CLI tool for debugging WebGPU applications. Launches a browser, injects the WebGPU Inspector, and provides commands to inspect GPU state.

## Prerequisites

```bash
pip install webgpu-inspector-cli
python -m playwright install chromium
```

## Quick Start

```bash
# 1. Launch browser with your WebGPU app
webgpu-inspector-cli browser launch --url https://your-app.com

# 2. Check for problems
webgpu-inspector-cli --json errors list
webgpu-inspector-cli --json status summary

# 3. Inspect GPU objects
webgpu-inspector-cli --json objects list
webgpu-inspector-cli --json objects inspect --id 8

# 4. View shader code
webgpu-inspector-cli shaders view --id 8

# 5. Capture a frame
webgpu-inspector-cli --json capture frame

# 6. Clean up
webgpu-inspector-cli browser close
```

## Command Groups

### browser - Session lifecycle
- `browser launch --url URL [--headless] [--gpu-backend BACKEND]`
- `browser close`
- `browser navigate --url URL`
- `browser screenshot -o PATH`
- `browser status`

### objects - GPU object inspection
- `objects list [--type TYPE]` - List all GPU objects (Adapter, Device, Buffer, Texture, ShaderModule, RenderPipeline, etc.)
- `objects inspect --id ID` - Full details including descriptor and creation stacktrace
- `objects search --label PATTERN` - Find objects by label
- `objects memory` - GPU memory breakdown

### capture - Frame capture
- `capture frame [--timeout N]` - Capture next frame's GPU commands
- `capture commands [--pass-index N]` - List commands from captured frame
- `capture texture --id ID [-o PATH]` - Read texture pixels, optionally save as PNG
- `capture buffer --id ID [--format hex|float32|uint32]` - Read buffer data

### shaders - Shader inspection
- `shaders list` - List all shader modules
- `shaders view --id ID` - View WGSL source code
- `shaders compile --id ID --file PATH` - Hot-replace shader code
- `shaders revert --id ID` - Revert to original

### errors - Validation errors
- `errors list` - All validation errors with stacktraces
- `errors watch [--timeout N]` - Stream errors in real-time
- `errors clear` - Reset error history

### status - Runtime monitoring
- `status summary` - Object counts, memory, FPS, error count
- `status fps` - Current frame rate
- `status memory` - Memory breakdown

## Agent Guidance

- Always use `--json` flag for machine-readable output
- Launch browser first with `browser launch --url URL`
- Close browser when done with `browser close`
- For debugging workflow: check `errors list` first, then `objects list` to understand GPU state
- Shader IDs are integers - get them from `shaders list` before using `shaders view`
- Frame capture is async - `capture frame` polls until complete (default 30s timeout)
- Texture data can be saved as PNG with `capture texture --id ID -o output.png`
