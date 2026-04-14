# cli-anything-webgpu-inspector

CLI tool for the [WebGPU Inspector](https://github.com/brendan-duncan/webgpu_inspector) browser extension. Lets AI agents inspect and debug WebGPU applications from the command line.

## Prerequisites

- Python 3.10+
- Chrome/Chromium (installed via Playwright)

## Installation

```bash
cd agent-harness
pip install -e .
python -m playwright install chromium
```

The webgpu_inspector submodule must be initialized:
```bash
git submodule update --init
```

## Usage

### One-shot commands

```bash
# Launch browser and inspect a WebGPU app
cli-anything-webgpu-inspector browser launch --url https://your-webgpu-app.com

# List all GPU objects
cli-anything-webgpu-inspector objects list --json

# View shader code
cli-anything-webgpu-inspector shaders view --id 8

# Check for validation errors
cli-anything-webgpu-inspector errors list --json

# Get GPU state summary
cli-anything-webgpu-inspector status summary --json

# Capture a frame
cli-anything-webgpu-inspector capture frame --json

# Close browser
cli-anything-webgpu-inspector browser close
```

### REPL mode

```bash
cli-anything-webgpu-inspector
# Enters interactive REPL with command history and completion
```

### JSON output

All commands support `--json` for machine-readable output:

```bash
cli-anything-webgpu-inspector --json objects list
```

## Command Groups

| Group | Commands | Description |
|-------|----------|-------------|
| `browser` | launch, close, navigate, screenshot, status | Browser session lifecycle |
| `objects` | list, inspect, search, memory | GPU object inspection |
| `capture` | frame, commands, texture, buffer | Frame capture and data inspection |
| `shaders` | list, view, compile, revert | Shader module inspection and hot-reload |
| `errors` | list, watch, clear | Validation error tracking |
| `status` | summary, fps, memory | Runtime monitoring |

## How It Works

The CLI uses Playwright to launch Chrome, then injects the WebGPU Inspector's JavaScript directly into the page via CDP. A collector script listens for the inspector's `__WebGPUInspector` CustomEvents and accumulates GPU state. Python queries this state via `page.evaluate()` calls.

This means you get the same inspection data as the DevTools panel, without needing the Chrome extension installed.

## Running Tests

```bash
# Unit tests
pytest cli_anything/webgpu_inspector/tests/test_core.py -v

# E2E tests (launches real browser)
pytest cli_anything/webgpu_inspector/tests/test_full_e2e.py -v -s
```
