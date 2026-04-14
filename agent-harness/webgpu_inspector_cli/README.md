# webgpu-inspector-cli

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
webgpu-inspector-cli browser launch --url https://your-webgpu-app.com

# List all GPU objects
webgpu-inspector-cli objects list --json

# View shader code
webgpu-inspector-cli shaders view --id 8

# Check for validation errors
webgpu-inspector-cli errors list --json

# Get GPU state summary
webgpu-inspector-cli status summary --json

# Capture a frame
webgpu-inspector-cli capture frame --json

# Close browser
webgpu-inspector-cli browser close
```

### REPL mode

```bash
webgpu-inspector-cli
# Enters interactive REPL with command history and completion
```

### JSON output

All commands support `--json` for machine-readable output:

```bash
webgpu-inspector-cli --json objects list
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
pytest webgpu_inspector_cli/tests/test_core.py -v

# E2E tests (launches real browser)
pytest webgpu_inspector_cli/tests/test_full_e2e.py -v -s
```
