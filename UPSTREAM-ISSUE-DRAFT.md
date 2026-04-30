<!-- intentionally not in README; one-shot upstream issue draft for github.com/brendan-duncan/webgpu_inspector -->

# Hard kernel panic on macOS under bursty createBuffer + writeBuffer workloads

## Summary

When an inspected page issues a tight burst of WebGPU operations (~50 `createBuffer` + `writeBuffer` + `createBindGroup` cycles inside ~2 s), with the WebGPU Inspector loader injected, macOS deadlocks at the GPU/WindowServer layer and requires a hard cold-start. The same workload runs cleanly without the inspector. Two consecutive reproductions on identical workload, on Apple Silicon (Darwin 25.3.0).

This is not a sandboxed tab crash and not a Chromium GPU-process crash — it's a full machine reboot, with no spinning beach ball, no WindowServer restart, no "Aw, snap" dialog.

## Reproducer

System: macOS 25.3.0 (Darwin), Apple Silicon (M-series), Chromium via Playwright with `--enable-unsafe-webgpu --enable-features=Vulkan`.

Workload (gsplat LOD streaming, ~921 chunks): per upload-drainer cycle, on every frame where the resident-chunk set changes:

```
device.createBuffer × 9   (growth)
queue.writeBuffer        (~700 KB splat bytes)
queue.writeBuffer        (~400 KB identity fill)
device.createBindGroup   (rebuild)
```

51 cycles fire in ~2 s. With 1 chunk (1 cycle), the inspector is stable. With 921 chunks (51 cycles), the host kernel panics.

## Root cause: synchronous main-thread bookkeeping per hooked call

Every hooked WebGPU method runs synchronously on the main thread, including `JSON.stringify` of the descriptor and a `CustomEvent` dispatch:

- `webgpu_inspector.js:1566` — `_recordCommand` calls `_stringifyDescriptor` which runs `JSON.stringify(descriptor)` synchronously inside the wrapper.
- `webgpu_inspector.js:432–459` — `_postMessage` dispatches a `CustomEvent("__WebGPUInspector", {...})` synchronously inside the same hook.
- `webgpu_inspector.js:48–53` — the `_trackedObjects` Map (and friends) are unbounded; nothing caps them.
- `webgpu_inspector.js:166–204` — the `FinalizationRegistry` callback flushes in 200 ms batches, so under burst the working set grows much faster than it shrinks.
- `src/utils/gpu_object_wrapper.js:178–231` — `_wrapMethod` replaces `proto[methodName]` with a wrapper that fires `onPreCall` / `onPostCall` signals, both synchronous.

With 9 buffers × 51 cycles = 459 invocations, each running `JSON.stringify(descriptor)` + `CustomEvent` dispatch + a tracked-object map insert, the main thread stalls long enough that GPU command submissions back up, and on macOS the resulting GPU-driver state appears to be unrecoverable from the application layer — the OS-level kernel panics.

## Suggested fixes

### H1 — Cap the resource-tracking maps

`_trackedObjects` and `_trackedObjectInfo` grow without ceiling. With 459 buffers in 2 s and the FinalizationRegistry only flushing every 200 ms, the Maps can hold thousands of WeakRef entries simultaneously.

Add an `options.maxBuffers` (default ~4096) / `maxTextures` / `maxPipelines` ceiling. When exceeded, drop the oldest entries (FIFO) and bump a `droppedDueToCeiling` counter. Surface the ceiling and counter via `window.__wgiOptions` and `window.__wgi.stats()` so apps can detect when they're hitting it.

### H2 — Move bookkeeping off the synchronous return path

The synchronous return path of the hook does:

1. `JSON.stringify(descriptor)` — can be megabytes for shader source or textures.
2. `_postMessage` — `CustomEvent` dispatch (synchronous handler invocation).
3. Map inserts.

None of this needs to be synchronous. The hook should:

- Capture references to the args (no cloning, no stringify).
- Push a `{method, args, returnValue, timestamp}` record onto a queue.
- Schedule a `queueMicrotask` (or `MessageChannel`-driven async) that drains the queue, runs `JSON.stringify` once per batch, and emits the `CustomEvent` once per batch.

This drops per-call overhead from O(JSON-size) + O(listeners) to O(1) push.

### H3 — Burst guard

Even with H1+H2, a runaway app should not be able to indefinitely starve the host. Add a rolling-window guard that, when invocations exceed `maxHooksPerWindow` (default ~200) within `windowMs` (default 100), bypasses the wrapper and calls the real GPU method directly. The GPU work still happens; only inspector bookkeeping is dropped for the burst tail. Surface `droppedDueToBurst` via `window.__wgi.stats()`.

This is the smallest fix that would have prevented the crashes I observed.

### H4 — Lazy bind-group introspection

`_objectReplacementMap` is touched on each `createBindGroup` (`webgpu_inspector.js:818`); 51 rebuilds in 2 s causes cache-key churn. Defer bind-group entry introspection to frame-capture time — at create time, store only the entry list, not resolved buffers/textures.

## Temporary downstream mitigation

`webgpu-inspector-cli` v0.3.0 (https://github.com/scasekar/webgpu-inspector-cli) ships a small page-side burst-guard script that runs **before** the loader bootstrap. It uses an `Object.defineProperty` setter trap on the GPUDevice / GPUQueue prototypes to wrap the inspector's wrappers with a rolling-window throttle implementing fix H3. It also adds a `Bridge(inspector=False)` opt-out so headless probe-style runs can skip injection entirely.

That's a workaround at the call boundary, not a fix. The hooks themselves still need H1 and H2 — the burst guard just prevents the hot path from melting the driver while a proper fix lands.

## Repro artifacts

- Crash report (full notes): `/Users/asekar/code/sc_points3/.claude/memory/webgpu-inspector-crash-report-20260429.md`
- Downstream burst-guard implementation: `webgpu-inspector-cli/agent-harness/webgpu_inspector_cli/js/wgi_guard.js`
