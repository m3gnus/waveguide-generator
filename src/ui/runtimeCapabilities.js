import { DEFAULT_BACKEND_URL } from '../config/backendUrl.js';

let cachedRuntimeHealth = null;

function normalizeMode(mode, fallback = 'auto') {
  const normalized = String(mode || '').trim().toLowerCase();
  return normalized || fallback;
}

export function cacheRuntimeHealth(health) {
  cachedRuntimeHealth = health && typeof health === 'object' ? health : null;
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
    throw new Error('health fetch failed');
  }
  const health = await response.json();
  cacheRuntimeHealth(health);
  return health;
}

export function modeLabel(mode) {
  switch (normalizeMode(mode, 'unknown')) {
    case 'opencl_gpu':
      return 'OpenCL GPU';
    case 'opencl_cpu':
      return 'OpenCL CPU';
    case 'auto':
      return 'Auto';
    default:
      return String(mode || 'Unknown').trim();
  }
}

export function describeSelectedDevice(health) {
  const deviceInfo = health?.deviceInterface;
  if (!deviceInfo || typeof deviceInfo !== 'object') {
    return '';
  }

  const selectedMode = modeLabel(deviceInfo.selected_mode || deviceInfo.requested_mode || 'auto');
  const deviceName = String(deviceInfo.device_name || '').trim();
  const hasDeviceName = deviceName && deviceName.toLowerCase() !== 'none';
  const modeAlreadyMentionsCpu = selectedMode.toLowerCase().includes('cpu');
  const deviceNameIsCpuLabel = /^cpu$/i.test(deviceName);
  const shouldShowDeviceSuffix = hasDeviceName && !(modeAlreadyMentionsCpu && deviceNameIsCpuLabel);

  return shouldShowDeviceSuffix
    ? `Using: ${selectedMode} (${deviceName})`
    : `Using: ${selectedMode}`;
}

export function describeSimBasicDeviceAvailability(health, requestedMode = 'auto') {
  const deviceInfo = health?.deviceInterface;
  const availability = deviceInfo?.mode_availability;
  const normalizedRequestedMode = normalizeMode(requestedMode);

  if (!availability || typeof availability !== 'object') {
    return {
      unavailableModes: ['opencl_gpu', 'opencl_cpu'],
      statusText: 'Solver unavailable. Auto mode only.',
    };
  }

  const unavailableModes = ['opencl_gpu', 'opencl_cpu'].filter((mode) => {
    const info = availability[mode];
    return Boolean(info && info.available === false);
  });

  if (normalizedRequestedMode !== 'auto' && unavailableModes.includes(normalizedRequestedMode)) {
    return {
      unavailableModes,
      statusText: `${modeLabel(normalizedRequestedMode)} unavailable on this machine.`,
    };
  }

  const selectedMode = normalizeMode(deviceInfo?.selected_mode, '');
  if (normalizedRequestedMode === 'auto' && selectedMode && selectedMode !== 'auto') {
    return {
      unavailableModes,
      statusText: `Auto resolves to: ${modeLabel(selectedMode)}`,
    };
  }

  return {
    unavailableModes,
    statusText: unavailableModes.length > 0
      ? `${unavailableModes.length} mode(s) unavailable on this machine`
      : '',
  };
}

export function summarizeRuntimeCapabilities(health) {
  const solverReady = Boolean(health?.solverReady);
  const occBuilderReady = Boolean(health?.occBuilderReady);
  const fullyReady = solverReady && occBuilderReady;
  const advancedCapability = health?.capabilities?.simulationAdvanced;
  const backendDeclaresAdvancedSupport = advancedCapability?.available === true;

  let statusText = 'Backend status unavailable.';
  if (!solverReady) {
    statusText = 'Solver is not available.';
  } else if (!occBuilderReady) {
    statusText = 'Mesh builder is not available.';
  } else if (backendDeclaresAdvancedSupport) {
    statusText = String(
      advancedCapability?.reason || 'Advanced solver overrides are available.'
    );
  } else if (!backendDeclaresAdvancedSupport) {
    statusText = String(
      advancedCapability?.reason || 'Advanced overrides are not supported by this backend.'
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
