import { DEFAULT_BACKEND_URL } from "../config/backendUrl.js";

let cachedRuntimeHealth = null;

function normalizeMode(mode, fallback = "auto") {
  const normalized = String(mode || "")
    .trim()
    .toLowerCase();
  return normalized || fallback;
}

export function cacheRuntimeHealth(health) {
  cachedRuntimeHealth = health && typeof health === "object" ? health : null;
  return cachedRuntimeHealth;
}

export function getCachedRuntimeHealth() {
  return cachedRuntimeHealth;
}

export async function fetchRuntimeHealth({
  backendUrl = DEFAULT_BACKEND_URL,
  fetchImpl = fetch,
} = {}) {
  const response = await fetchImpl(`${backendUrl}/health`);
  if (!response.ok) {
    throw new Error("health fetch failed");
  }
  const health = await response.json();
  cacheRuntimeHealth(health);
  return health;
}

export function modeLabel(mode) {
  switch (normalizeMode(mode, "unknown")) {
    case "opencl_gpu":
      return "OpenCL GPU";
    case "opencl_cpu":
      return "OpenCL CPU";
    case "auto":
      return "Auto";
    default:
      return String(mode || "Unknown").trim();
  }
}

export function describeSelectedDevice(health) {
  const deviceInfo = health?.deviceInterface;
  if (!deviceInfo || typeof deviceInfo !== "object") {
    return "";
  }

  const selectedMode = modeLabel(
    deviceInfo.selected_mode || deviceInfo.requested_mode || "auto",
  );
  const deviceName = String(deviceInfo.device_name || "").trim();
  const hasDeviceName = deviceName && deviceName.toLowerCase() !== "none";
  const modeAlreadyMentionsCpu = selectedMode.toLowerCase().includes("cpu");
  const deviceNameIsCpuLabel = /^cpu$/i.test(deviceName);
  const shouldShowDeviceSuffix =
    hasDeviceName && !(modeAlreadyMentionsCpu && deviceNameIsCpuLabel);

  return shouldShowDeviceSuffix
    ? `Using: ${selectedMode} (${deviceName})`
    : `Using: ${selectedMode}`;
}

export function describeSimBasicDeviceAvailability(
  health,
  requestedMode = "auto",
) {
  const deviceInfo = health?.deviceInterface;
  const availability = deviceInfo?.mode_availability;
  const normalizedRequestedMode = normalizeMode(requestedMode);

  if (!availability || typeof availability !== "object") {
    return {
      unavailableModes: ["opencl_gpu", "opencl_cpu"],
      statusText: "Solver unavailable. Auto mode only.",
    };
  }

  const unavailableModes = ["opencl_gpu", "opencl_cpu"].filter((mode) => {
    const info = availability[mode];
    return Boolean(info && info.available === false);
  });

  if (
    normalizedRequestedMode !== "auto" &&
    unavailableModes.includes(normalizedRequestedMode)
  ) {
    return {
      unavailableModes,
      statusText: `${modeLabel(normalizedRequestedMode)} unavailable on this machine.`,
    };
  }

  const selectedMode = normalizeMode(deviceInfo?.selected_mode, "");
  if (
    normalizedRequestedMode === "auto" &&
    selectedMode &&
    selectedMode !== "auto"
  ) {
    return {
      unavailableModes,
      statusText: `Auto resolves to: ${modeLabel(selectedMode)}`,
    };
  }

  return {
    unavailableModes,
    statusText:
      unavailableModes.length > 0
        ? `${unavailableModes.length} mode(s) unavailable on this machine`
        : "",
  };
}

/**
 * Return platform-specific OpenCL setup instructions based on OS/arch fields
 * from the health endpoint's opencl_diagnostics.
 *
 * @param {object|null} health - Cached health response from /health
 * @returns {string} Setup help text, or empty string if no guidance is available.
 */
export function getOpenCLSetupHelp(health) {
  const diagnostics = health?.deviceInterface?.opencl_diagnostics;
  if (!diagnostics || typeof diagnostics !== "object") {
    return "";
  }

  const osPlatform = String(diagnostics.os_platform || "").toLowerCase();
  const osArch = String(diagnostics.os_arch || "").toLowerCase();

  if (
    osPlatform === "darwin" &&
    (osArch === "arm64" || osArch.startsWith("aarch"))
  ) {
    return (
      "GPU is Metal-only on Apple Silicon. For CPU-based OpenCL, install pocl via Homebrew: " +
      "`brew install pocl ocl-icd`"
    );
  }

  if (osPlatform === "darwin") {
    return "Check Apple OpenCL driver status. Note: OpenCL is deprecated on macOS 13+.";
  }

  if (osPlatform === "linux") {
    return (
      "Install the appropriate OpenCL ICD package for your GPU vendor: " +
      "intel-opencl-icd, rocm-opencl-runtime, or nvidia-opencl-icd"
    );
  }

  if (osPlatform === "win32") {
    return "Install Intel OpenCL Runtime or CUDA toolkit (NVIDIA)";
  }

  return "";
}

export function summarizeRuntimeCapabilities(health) {
  const solverReady = Boolean(health?.solverReady);
  const occBuilderReady = Boolean(health?.occBuilderReady);
  const fullyReady = solverReady && occBuilderReady;
  const advancedCapability = health?.capabilities?.simulationAdvanced;
  const backendDeclaresAdvancedSupport = advancedCapability?.available === true;

  let statusText = "Backend status unavailable.";
  if (!solverReady) {
    statusText = "Solver is not available.";
  } else if (!occBuilderReady) {
    statusText = "Mesh builder is not available.";
  } else if (backendDeclaresAdvancedSupport) {
    statusText = String(
      advancedCapability?.reason || "Advanced solver overrides are available.",
    );
  } else if (!backendDeclaresAdvancedSupport) {
    statusText = String(
      advancedCapability?.reason ||
        "Advanced overrides are not supported by this backend.",
    );
  }

  return {
    solverReady,
    occBuilderReady,
    fullyReady,
    simulationAdvanced: {
      available: backendDeclaresAdvancedSupport,
      reason: statusText,
      controls: Array.isArray(advancedCapability?.controls)
        ? advancedCapability.controls
        : [],
      plannedControls: Array.isArray(advancedCapability?.plannedControls)
        ? advancedCapability.plannedControls
        : [],
    },
  };
}

export function getDependencyStatusSummary(health) {
  const deps = health?.dependencies?.runtime || {};
  const gmsh = deps?.gmsh_python || {};
  const bempp = deps?.bempp || {};
  const python = deps?.python || {};
  const deviceInfo = health?.deviceInterface || {};

  return {
    python: {
      name: "Python",
      version: python.version || null,
      supported: python.supported !== false,
      ready: python.supported !== false,
      feature: "Backend runtime",
      guidance:
        python.supported === false
          ? `Python ${python.version || "unknown"} is outside supported range (>=3.10,<3.15). Install a compatible Python version.`
          : null,
    },
    gmsh: {
      name: "Gmsh",
      version: gmsh.version || null,
      available: gmsh.available === true,
      supported: gmsh.supported !== false,
      ready: gmsh.ready === true,
      feature: "OCC mesh build/export",
      guidance: !gmsh.available
        ? "Install gmsh: pip install gmsh>=4.11,<5.0"
        : gmsh.supported === false
          ? `Gmsh ${gmsh.version || "unknown"} is outside supported range (>=4.11,<5.0). Install a compatible version.`
          : null,
    },
    bempp: {
      name: "Bempp-cl",
      version: bempp.version || null,
      variant: bempp.variant || null,
      available: bempp.available === true,
      supported: bempp.supported !== false,
      ready: bempp.ready === true,
      feature: "BEM simulation",
      guidance: !bempp.available
        ? "Install bempp-cl: pip install bempp-cl>=0.4,<0.5"
        : bempp.supported === false
          ? `Bempp-cl ${bempp.version || "unknown"} is outside supported range (>=0.4,<0.5). Install a compatible version.`
          : null,
    },
    opencl: {
      name: "OpenCL Runtime",
      version: null,
      available: Boolean(
        deviceInfo?.mode_availability?.opencl_gpu?.available ||
        deviceInfo?.mode_availability?.opencl_cpu?.available,
      ),
      supported: true,
      ready: Boolean(
        deviceInfo?.mode_availability?.opencl_gpu?.available ||
        deviceInfo?.mode_availability?.opencl_cpu?.available,
      ),
      feature: "BEM acceleration",
      guidance: getOpenCLSetupHelp(health) || null,
    },
  };
}

export function getFeatureBlockedReason(health, feature) {
  const summary = getDependencyStatusSummary(health);

  switch (feature) {
    case "occ-mesh":
    case "mesh-build":
    case "export-msh":
      if (!summary.gmsh.ready) {
        return (
          summary.gmsh.guidance ||
          `${summary.gmsh.name} is not ready for OCC mesh export.`
        );
      }
      return null;

    case "bem-solve":
    case "simulation":
      if (!summary.bempp.ready) {
        return (
          summary.bempp.guidance ||
          `${summary.bempp.name} is not ready for BEM simulation.`
        );
      }
      if (!summary.opencl.available) {
        return (
          summary.opencl.guidance ||
          "OpenCL runtime is not available. BEM solve may be slow or unavailable."
        );
      }
      return null;

    case "chart-render":
    case "matplotlib":
      return null;

    default:
      return null;
  }
}

export function renderDependencyStatusHTML(health) {
  const summary = getDependencyStatusSummary(health);
  const items = Object.values(summary);

  const renderItem = (dep) => {
    const statusClass = dep.ready
      ? "dep-status-ready"
      : dep.available
        ? "dep-status-partial"
        : "dep-status-missing";

    const statusIcon = dep.ready ? "✓" : dep.available ? "!" : "✗";
    const versionText = dep.version ? ` (${dep.version})` : "";

    let html = `<div class="dep-item ${statusClass}">`;
    html += `<span class="dep-icon">${statusIcon}</span>`;
    html += `<span class="dep-name">${dep.name}${versionText}</span>`;
    html += `<span class="dep-feature">${dep.feature}</span>`;
    if (dep.guidance) {
      html += `<span class="dep-guidance">${dep.guidance}</span>`;
    }
    html += "</div>";
    return html;
  };

  return `<div class="dependency-status">${items.map(renderItem).join("")}</div>`;
}
