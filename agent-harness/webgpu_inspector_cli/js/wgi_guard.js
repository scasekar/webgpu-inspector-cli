// WGI burst guard.
//
// Runs BEFORE the WebGPU Inspector loader bootstrap. Wraps the GPUDevice and
// GPUQueue prototype methods with a setter trap so when the inspector installs
// its synchronous JSON.stringify + postMessage wrappers, we can intercept and
// bypass them under burst conditions (>maxHooksPerWindow calls within
// windowMs). Bypass calls the real GPU method directly — the GPU work still
// happens, only the hook bookkeeping is dropped.
//
// Mitigates the macOS hard kernel panic seen when bursty WebGPU workloads
// (e.g. gsplat LOD streaming: ~50 createBuffer + writeBuffer + createBindGroup
// cycles in 2 s) overwhelm the inspector's main-thread bookkeeping. See
// /Users/asekar/code/sc_points3/.claude/memory/webgpu-inspector-crash-report-20260429.md
// and UPSTREAM-ISSUE-DRAFT.md.

(function () {
  if (typeof navigator === "undefined" || !navigator.gpu) return;

  var opts = (window.__wgiOptions = window.__wgiOptions || {});
  if (opts.enabled === false) return;
  var maxHooksPerWindow = opts.maxHooksPerWindow || 200;
  var windowMs = opts.windowMs || 100;

  var stats = {
    hookInvocations: 0,
    droppedDueToBurst: 0,
    burstWindowStart: 0,
    burstCount: 0,
    perMethod: {},
  };

  function shouldDrop(methodName) {
    var now = (typeof performance !== "undefined" ? performance.now() : Date.now());
    if (now - stats.burstWindowStart > windowMs) {
      stats.burstWindowStart = now;
      stats.burstCount = 0;
    }
    stats.burstCount++;
    stats.hookInvocations++;
    var pm = stats.perMethod[methodName] || (stats.perMethod[methodName] = { invocations: 0, dropped: 0 });
    pm.invocations++;
    if (stats.burstCount > maxHooksPerWindow) {
      stats.droppedDueToBurst++;
      pm.dropped++;
      return true;
    }
    return false;
  }

  // Install a getter/setter trap on `proto[name]`. Initially the getter
  // returns the real method, so the inspector's _wrapMethod sees the genuine
  // original when it captures it. When the inspector then assigns its
  // wrapped function back, our setter wraps that wrapper with the burst
  // guard. The setter can fire multiple times (re-hooking on navigate,
  // reloading inspector, etc.) — each new wrapper just replaces the old.
  function trap(proto, name) {
    if (!proto || typeof proto[name] !== "function") return;
    var realFn = proto[name];
    var current = realFn; // currently-installed wrapper (real fn until inspector loads)
    try {
      Object.defineProperty(proto, name, {
        configurable: true,
        get: function () { return current; },
        set: function (newFn) {
          // The inspector just installed `newFn`. Install our throttler
          // around it. Under burst, bypass `newFn` and call the real GPU
          // method — the GPU work proceeds, the inspector just misses
          // bookkeeping for those calls.
          var inspectorWrapper = newFn;
          current = function () {
            if (shouldDrop(name)) {
              return realFn.apply(this, arguments);
            }
            return inspectorWrapper.apply(this, arguments);
          };
        },
      });
    } catch (e) {
      // Some environments may freeze prototypes. If we can't install the
      // trap, leave the original method in place — better the inspector
      // works without a guard than nothing works at all.
      console.warn("[wgi-guard] could not trap " + name + ": " + e);
    }
  }

  var devProto = (typeof GPUDevice !== "undefined") ? GPUDevice.prototype : null;
  var queueProto = (typeof GPUQueue !== "undefined") ? GPUQueue.prototype : null;

  if (devProto) {
    trap(devProto, "createBuffer");
    trap(devProto, "createBindGroup");
    trap(devProto, "createTexture");
    trap(devProto, "createShaderModule");
    trap(devProto, "createPipelineLayout");
    trap(devProto, "createRenderPipeline");
    trap(devProto, "createComputePipeline");
  }
  if (queueProto) {
    trap(queueProto, "writeBuffer");
    trap(queueProto, "writeTexture");
    trap(queueProto, "submit");
  }

  window.__wgi = window.__wgi || {};
  window.__wgi.stats = function () {
    return {
      hookInvocations: stats.hookInvocations,
      droppedDueToBurst: stats.droppedDueToBurst,
      burstWindowStart: stats.burstWindowStart,
      burstCount: stats.burstCount,
      perMethod: JSON.parse(JSON.stringify(stats.perMethod)),
      options: { maxHooksPerWindow: maxHooksPerWindow, windowMs: windowMs },
    };
  };
})();
