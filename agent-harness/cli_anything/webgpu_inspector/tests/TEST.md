# WebGPU Inspector CLI - Test Plan & Results

## Test Inventory Plan

- `test_core.py`: ~20 unit tests planned
  - Collector message parsing (simulated messages)
  - Session undo/redo for shader edits
  - Bridge path resolution
  - Models and data formatting
- `test_full_e2e.py`: ~10 E2E tests planned
  - Browser launch/close lifecycle
  - Object detection on triangle.html
  - Shader code retrieval
  - Frame capture
  - Error detection
  - Status/memory summary
  - CLI subprocess tests via `_resolve_cli`

## Unit Test Plan (`test_core.py`)

### Session Module
- `test_push_pop_shader_edit`: Push edit, pop returns original code
- `test_pop_empty_returns_none`: Pop from empty history returns None
- `test_clear_shader_edits`: Clear removes history
- `test_has_shader_edits`: Boolean check for edit history
- `test_multiple_edits_stack`: Multiple pushes, pops in LIFO order

### Bridge Module
- `test_find_inspector_js`: Locates webgpu_inspector_loader.js
- `test_find_collector_js`: Locates collector.js
- `test_bridge_not_connected`: Bridge raises error when no session

### Object Formatting
- `test_format_bytes`: Human-readable byte formatting
- `test_format_bytes_zero`: Zero bytes edge case

## E2E Test Plan (`test_full_e2e.py`)

### Browser Lifecycle
- `test_launch_and_close`: Launch browser, verify connection, close
- `test_navigate`: Navigate to new URL, verify re-injection

### Object Detection
- `test_triangle_objects`: Load triangle.html, verify expected GPU objects
- `test_object_types`: Verify correct types detected (Adapter, Device, Texture, etc.)
- `test_shader_code`: Retrieve shader source code from ShaderModule

### Runtime Monitoring
- `test_frame_rate`: Verify FPS is reported after a few frames
- `test_memory_usage`: Verify memory tracking for textures
- `test_summary`: Verify summary includes all expected fields

### CLI Subprocess Tests
- `test_help`: Verify --help output
- `test_json_output`: Verify --json flag on commands

## Realistic Workflow Scenarios

### Scenario 1: Debug WebGPU Triangle
- Launch browser with triangle.html
- List GPU objects, verify adapter/device/pipeline/shader
- View shader code, confirm WGSL vertex/fragment shaders
- Check for validation errors (should be 0)
- Verify frame rate is positive

### Scenario 2: Memory Leak Investigation
- Launch browser with test page
- Check memory usage
- Verify buffer/texture memory is tracked
- Confirm objects are being garbage collected

---

## Test Results

*(To be appended after running tests)*
