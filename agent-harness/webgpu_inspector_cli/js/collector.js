/**
 * collector.js - Injected into the page to collect WebGPU Inspector messages.
 *
 * Listens for __WebGPUInspector CustomEvents and accumulates GPU object state,
 * validation errors, frame timing, and capture results. Exposes a query API
 * on window.__wgi that the Python bridge calls via page.evaluate().
 */
(function () {
  "use strict";

  if (window.__wgi) return; // Already injected

  // --- Action constants (mirrors src/utils/actions.js) ---
  const Actions = {
    AddObject: "webgpu_inspect_add_object",
    DeleteObject: "webgpu_inspect_delete_object",
    DeleteObjects: "webgpu_inspect_delete_objects",
    ObjectSetLabel: "webgpu_inspect_object_set_label",
    ResolveAsyncObject: "webgpu_inspect_resolve_async_object",
    ValidationError: "webgpu_inspect_validation_error",
    MemoryLeakWarning: "webgpu_inspect_memory_leak_warning",
    DeltaTime: "webgpu_inspect_delta_time",
    CaptureFrameResults: "webgpu_inspect_capture_frame_results",
    CaptureFrameCommands: "webgpu_inspect_capture_frame_commands",
    CaptureTextureData: "webgpu_inspect_capture_texture_data",
    CaptureBufferData: "webgpu_inspect_capture_buffer_data",
    CaptureTextureFrames: "webgpu_inspect_capture_texture_frames",
    Recording: "webgpu_record_recording",
  };

  const PanelActions = {
    RequestTexture: "webgpu_inspect_request_texture",
    CompileShader: "webgpu_inspect_compile_shader",
    RevertShader: "webgpu_inspect_revert_shader",
    Capture: "webgpu_inspector_capture",
    InitializeInspector: "webgpu_initialize_inspector",
  };

  // --- State ---
  const objects = new Map(); // id -> { id, type, descriptor, label, stacktrace, parent, pending }
  const errors = [];         // [{ id, objectId, message, stacktrace, timestamp }]
  const memoryLeaks = [];
  let errorCount = 0;
  let deltaFrameTime = -1;
  let totalTextureMemory = 0;
  let totalBufferMemory = 0;

  // Capture state
  let captureStatus = "idle"; // "idle" | "pending" | "complete"
  let capturedFrameResults = null;
  let capturedCommands = null;
  let capturedTextures = new Map(); // id -> { chunks: [], totalChunks, assembled }
  let capturedBuffers = new Map();  // id -> { data }

  // --- Texture memory calculation ---
  const FORMAT_SIZES = {
    "r8unorm": 1, "r8snorm": 1, "r8uint": 1, "r8sint": 1,
    "r16uint": 2, "r16sint": 2, "r16float": 2,
    "rg8unorm": 2, "rg8snorm": 2, "rg8uint": 2, "rg8sint": 2,
    "r32uint": 4, "r32sint": 4, "r32float": 4,
    "rg16uint": 4, "rg16sint": 4, "rg16float": 4,
    "rgba8unorm": 4, "rgba8unorm-srgb": 4, "rgba8snorm": 4,
    "rgba8uint": 4, "rgba8sint": 4,
    "bgra8unorm": 4, "bgra8unorm-srgb": 4,
    "rgb10a2uint": 4, "rgb10a2unorm": 4, "rg11b10ufloat": 4,
    "rg32uint": 8, "rg32sint": 8, "rg32float": 8,
    "rgba16uint": 8, "rgba16sint": 8, "rgba16float": 8,
    "rgba32uint": 16, "rgba32sint": 16, "rgba32float": 16,
    "depth16unorm": 2, "depth24plus": 4, "depth24plus-stencil8": 4,
    "depth32float": 4, "depth32float-stencil8": 5,
    "stencil8": 1,
  };

  function getTextureGpuSize(descriptor) {
    if (!descriptor) return 0;
    const format = descriptor.format;
    const bpp = FORMAT_SIZES[format];
    if (bpp === undefined) return 0;
    const w = descriptor.size?.[0] ?? descriptor.size?.width ?? 1;
    const h = descriptor.size?.[1] ?? descriptor.size?.height ?? 1;
    const d = descriptor.size?.[2] ?? descriptor.size?.depthOrArrayLayers ?? 1;
    const mips = descriptor.mipLevelCount ?? 1;
    let total = 0;
    for (let m = 0; m < mips; m++) {
      const mw = Math.max(1, w >> m);
      const mh = Math.max(1, h >> m);
      total += mw * mh * d * bpp;
    }
    return total;
  }

  // --- Message handler ---
  function handleMessage(detail) {
    if (!detail || !detail.__webgpuInspector || !detail.__webgpuInspectorPage) {
      return;
    }
    const action = detail.action;

    switch (action) {
      case Actions.AddObject: {
        let descriptor = null;
        try {
          descriptor = detail.descriptor ? JSON.parse(detail.descriptor) : null;
        } catch (e) {
          descriptor = null;
        }
        const obj = {
          id: detail.id,
          type: detail.type,
          descriptor: descriptor,
          label: descriptor?.label || null,
          stacktrace: detail.stacktrace || "",
          parent: detail.parent ?? null,
          pending: !!detail.pending,
        };

        // Track memory
        if (detail.type === "Buffer") {
          obj.size = descriptor?.size ?? 0;
          totalBufferMemory += obj.size;
        } else if (detail.type === "Texture") {
          // If texture already exists (reconfigured), update it
          const prev = objects.get(detail.id);
          if (prev && prev.type === "Texture") {
            totalTextureMemory -= prev.gpuSize || 0;
          }
          obj.gpuSize = getTextureGpuSize(descriptor);
          totalTextureMemory += obj.gpuSize;
        } else if (detail.type === "ShaderModule") {
          obj.code = descriptor?.code || null;
          obj.size = descriptor?.code?.length ?? 0;
        }

        objects.set(detail.id, obj);
        break;
      }

      case Actions.DeleteObject: {
        const obj = objects.get(detail.id);
        if (obj) {
          if (obj.type === "Buffer") totalBufferMemory -= obj.size || 0;
          if (obj.type === "Texture") totalTextureMemory -= obj.gpuSize || 0;
          objects.delete(detail.id);
        }
        break;
      }

      case Actions.DeleteObjects: {
        const ids = detail.idList || [];
        for (const id of ids) {
          const obj = objects.get(id);
          if (obj) {
            if (obj.type === "Buffer") totalBufferMemory -= obj.size || 0;
            if (obj.type === "Texture") totalTextureMemory -= obj.gpuSize || 0;
            objects.delete(id);
          }
        }
        break;
      }

      case Actions.ObjectSetLabel: {
        const obj = objects.get(detail.id);
        if (obj) obj.label = detail.label;
        break;
      }

      case Actions.ResolveAsyncObject: {
        const obj = objects.get(detail.id);
        if (obj) obj.pending = false;
        break;
      }

      case Actions.ValidationError: {
        errorCount++;
        errors.push({
          id: errorCount,
          objectId: detail.id ?? 0,
          message: detail.message,
          stacktrace: detail.stacktrace || "",
          timestamp: Date.now(),
        });
        break;
      }

      case Actions.MemoryLeakWarning: {
        memoryLeaks.push({
          id: detail.id,
          type: detail.type,
          message: detail.message,
          timestamp: Date.now(),
        });
        break;
      }

      case Actions.DeltaTime: {
        deltaFrameTime = detail.deltaTime;
        break;
      }

      case Actions.CaptureFrameResults: {
        capturedFrameResults = {
          frame: detail.frame,
          count: detail.count,
          batches: detail.batches,
        };
        captureStatus = "complete";
        break;
      }

      case Actions.CaptureFrameCommands: {
        capturedCommands = detail;
        break;
      }

      case Actions.CaptureTextureData: {
        const texId = detail.id;
        if (!capturedTextures.has(texId)) {
          capturedTextures.set(texId, { chunks: [], totalChunks: 0, complete: false });
        }
        const entry = capturedTextures.get(texId);
        if (detail.index !== undefined) {
          entry.chunks[detail.index] = detail.data;
          entry.totalChunks = detail.count || entry.totalChunks;
          // Check if all chunks received
          const received = entry.chunks.filter(c => c !== undefined).length;
          if (received >= entry.totalChunks && entry.totalChunks > 0) {
            entry.complete = true;
          }
        } else {
          // Single chunk
          entry.chunks = [detail.data];
          entry.totalChunks = 1;
          entry.complete = true;
        }
        break;
      }

      case Actions.CaptureBufferData: {
        const bufId = detail.id;
        capturedBuffers.set(bufId, {
          data: detail.data,
          offset: detail.offset || 0,
          size: detail.size || 0,
        });
        break;
      }
    }
  }

  // --- Listen for inspector messages ---
  window.addEventListener("__WebGPUInspector", (event) => {
    handleMessage(event.detail);
  });

  // --- Query API ---
  window.__wgi = {
    getObjects(type) {
      const result = [];
      for (const obj of objects.values()) {
        if (!type || obj.type === type) {
          result.push({
            id: obj.id,
            type: obj.type,
            label: obj.label,
            descriptor: obj.descriptor,
            stacktrace: obj.stacktrace,
            parent: obj.parent,
            pending: obj.pending,
            size: obj.size,
            gpuSize: obj.gpuSize,
          });
        }
      }
      return result;
    },

    getObject(id) {
      const obj = objects.get(id);
      if (!obj) return null;
      return {
        id: obj.id,
        type: obj.type,
        label: obj.label,
        descriptor: obj.descriptor,
        stacktrace: obj.stacktrace,
        parent: obj.parent,
        pending: obj.pending,
        size: obj.size,
        gpuSize: obj.gpuSize,
        code: obj.code,
      };
    },

    getObjectCount() {
      return objects.size;
    },

    getErrors() {
      return errors.slice();
    },

    getErrorCount() {
      return errors.length;
    },

    clearErrors() {
      errors.length = 0;
      errorCount = 0;
    },

    getFrameRate() {
      if (deltaFrameTime <= 0) return { fps: 0, deltaTime: -1 };
      return {
        fps: Math.round(1000 / deltaFrameTime),
        deltaTime: deltaFrameTime,
      };
    },

    getMemoryUsage() {
      return {
        totalTextureMemory: totalTextureMemory,
        totalBufferMemory: totalBufferMemory,
        totalMemory: totalTextureMemory + totalBufferMemory,
      };
    },

    getSummary() {
      const typeCounts = {};
      for (const obj of objects.values()) {
        typeCounts[obj.type] = (typeCounts[obj.type] || 0) + 1;
      }
      const fr = deltaFrameTime > 0 ? Math.round(1000 / deltaFrameTime) : 0;
      return {
        objectCount: objects.size,
        typeCounts: typeCounts,
        errorCount: errors.length,
        fps: fr,
        deltaTime: deltaFrameTime,
        totalTextureMemory: totalTextureMemory,
        totalBufferMemory: totalBufferMemory,
        totalMemory: totalTextureMemory + totalBufferMemory,
      };
    },

    // --- Capture ---

    requestCapture(options) {
      captureStatus = "pending";
      capturedFrameResults = null;
      capturedCommands = null;
      capturedTextures = new Map();
      capturedBuffers = new Map();

      const data = options || {};
      window.dispatchEvent(new CustomEvent("__WebGPUInspector", {
        detail: {
          __webgpuInspector: true,
          action: "webgpu_inspector_capture",
          data: JSON.stringify(data),
        }
      }));
      return true;
    },

    getCaptureStatus() {
      return captureStatus;
    },

    getCapturedFrameResults() {
      return capturedFrameResults;
    },

    getCapturedCommands() {
      return capturedCommands;
    },

    requestTexture(id, mipLevel) {
      window.dispatchEvent(new CustomEvent("__WebGPUInspector", {
        detail: {
          __webgpuInspector: true,
          action: "webgpu_inspect_request_texture",
          id: id,
          mipLevel: mipLevel || 0,
        }
      }));
      return true;
    },

    getTextureData(id) {
      const entry = capturedTextures.get(id);
      if (!entry) return null;
      return {
        complete: entry.complete,
        totalChunks: entry.totalChunks,
        receivedChunks: entry.chunks.filter(c => c !== undefined).length,
        data: entry.complete ? entry.chunks.join("") : null,
      };
    },

    getBufferData(id) {
      return capturedBuffers.get(id) || null;
    },

    // --- Shaders ---

    getShaderCode(id) {
      const obj = objects.get(id);
      if (!obj || obj.type !== "ShaderModule") return null;
      return obj.code || (obj.descriptor?.code ?? null);
    },

    compileShader(id, code) {
      window.dispatchEvent(new CustomEvent("__WebGPUInspector", {
        detail: {
          __webgpuInspector: true,
          action: "webgpu_inspect_compile_shader",
          id: id,
          code: code,
        }
      }));
      return true;
    },

    revertShader(id) {
      window.dispatchEvent(new CustomEvent("__WebGPUInspector", {
        detail: {
          __webgpuInspector: true,
          action: "webgpu_inspect_revert_shader",
          id: id,
        }
      }));
      return true;
    },

    getMemoryLeaks() {
      return memoryLeaks.slice();
    },
  };
})();
