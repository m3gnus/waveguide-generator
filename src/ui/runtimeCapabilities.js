import { DEFAULT_BACKEND_URL } from '../config/backendUrl.js';

let cachedRuntimeHealth = null;

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

export function describeSelectedDevice(health) {
  const solver = String(health?.solver || '')
    .trim()
    .toLowerCase();
  if (solver === 'metal-bem') {
    return 'Using: Metal BEM';
  }
  if (solver === 'bempp-bem') {
    const assemblyBackend = String(
      health?.solverBackends?.bempp?.status?.assemblyBackend || ''
    ).trim();
    return assemblyBackend ? `Using: Bempp (${assemblyBackend})` : 'Using: Bempp';
  }
  return '';
}

export function summarizeRuntimeCapabilities(health) {
  const solverReady = Boolean(
    health?.solverReady ||
    health?.solverBackends?.metal?.ready ||
    health?.solverBackends?.bempp?.ready
  );
  const mesherReady = Boolean(health?.mesherReady);
  const fullyReady = solverReady && mesherReady;
  const advancedCapability = health?.capabilities?.simulationAdvanced;
  const backendDeclaresAdvancedSupport = advancedCapability?.available === true;

  let statusText = 'Backend status unavailable.';
  if (!solverReady) {
    statusText = 'Solver is not available.';
  } else if (!mesherReady) {
    statusText = 'HornLab mesher is not available.';
  } else if (backendDeclaresAdvancedSupport) {
    statusText = String(advancedCapability?.reason || 'Advanced solver overrides are available.');
  } else if (!backendDeclaresAdvancedSupport) {
    statusText = String(
      advancedCapability?.reason || 'Advanced overrides are not supported by this backend.'
    );
  }

  return {
    solverReady,
    mesherReady,
    fullyReady,
    simulationAdvanced: {
      available: backendDeclaresAdvancedSupport,
      reason: statusText,
      controls: Array.isArray(advancedCapability?.controls) ? advancedCapability.controls : [],
      plannedControls: Array.isArray(advancedCapability?.plannedControls)
        ? advancedCapability.plannedControls
        : [],
    },
  };
}

export function getDependencyStatusSummary(health) {
  const deps = health?.dependencies?.runtime || {};
  const gmsh = deps?.gmsh_python || {};
  const mesher = deps?.hornlab_waveguide_mesher || {};
  const metalDep = deps?.hornlab_metal_bem || {};
  const bemppDep = deps?.hornlab_bempp_bem || {};
  const python = deps?.python || {};
  const metalStatus = health?.solverBackends?.metal?.status || {};
  const metalReady = Boolean(health?.solverBackends?.metal?.ready);
  const metalAvailable = Boolean(metalStatus?.available || metalReady);
  const metalHelperBuild = String(metalStatus?.nativeHelperBuild || '').trim();
  const bemppStatus = health?.solverBackends?.bempp?.status || {};
  const bemppReady = Boolean(health?.solverBackends?.bempp?.ready);
  const bemppAvailable = Boolean(bemppStatus?.available || bemppReady);

  return {
    python: {
      name: 'Python',
      version: python.version || null,
      supported: python.supported !== false,
      ready: python.supported !== false,
      feature: 'Backend runtime',
      guidance:
        python.supported === false
          ? `Python ${python.version || 'unknown'} is outside supported range (>=3.10,<3.15). Install a compatible Python version.`
          : null,
    },
    gmsh: {
      name: 'Gmsh',
      version: gmsh.version || null,
      available: gmsh.available === true,
      supported: gmsh.supported !== false,
      ready: gmsh.ready === true,
      feature: 'HornLab mesher build/export',
      guidance: !gmsh.available
        ? 'Install gmsh: pip install gmsh>=4.11.1,<5.0'
        : gmsh.supported === false
          ? `Gmsh ${gmsh.version || 'unknown'} is outside supported range (>=4.11.1,<5.0). Install a compatible version.`
          : null,
    },
    hornlabMesher: {
      name: 'HornLab waveguide mesher',
      version: mesher.version || null,
      available: mesher.available === true,
      supported: mesher.supported !== false,
      ready: mesher.ready === true,
      feature: 'HornLab mesher build/export',
      guidance: !mesher.available
        ? 'Install backend requirements: pip install -r server/requirements.txt'
        : mesher.supported === false
          ? 'Installed HornLab waveguide mesher is outside the supported runtime contract.'
          : null,
    },
    metal: {
      name: 'Metal BEM',
      version: metalDep?.version || null,
      available: metalAvailable,
      supported: metalStatus.supportedPlatform !== false,
      ready: metalReady,
      feature: 'BEM simulation (Apple Silicon macOS)',
      guidance: !metalReady
        ? metalStatus.reason
          ? String(metalStatus.reason)
          : metalAvailable && metalHelperBuild !== 'release'
            ? `Build the Metal release helper: npm run build:metal-helper (current: ${metalHelperBuild || 'missing'})`
            : 'Install hornlab-metal-bem (Apple Silicon macOS required): pip install -r server/requirements.txt'
        : null,
    },
    bempp: {
      name: 'Bempp',
      version: bemppDep?.version || null,
      available: bemppAvailable,
      supported: bemppDep.supported !== false,
      ready: bemppReady,
      feature: 'BEM simulation (cross-platform)',
      guidance: !bemppReady
        ? bemppStatus.reason
          ? String(bemppStatus.reason)
          : 'Install Bempp requirements: pip install -r server/requirements-bempp.txt'
        : null,
    },
  };
}

export function getFeatureBlockedReason(health, feature) {
  const summary = getDependencyStatusSummary(health);

  switch (feature) {
    case 'hornlab-mesher-mesh':
    case 'hornlab-mesh':
    case 'occ-mesh':
    case 'mesh-build':
    case 'export-msh':
      if (!summary.gmsh.ready) {
        return (
          summary.gmsh.guidance || `${summary.gmsh.name} is not ready for HornLab mesher export.`
        );
      }
      if (!summary.hornlabMesher.ready) {
        return (
          summary.hornlabMesher.guidance ||
          `${summary.hornlabMesher.name} is not ready for HornLab mesher export.`
        );
      }
      return null;

    case 'bem-solve':
    case 'simulation':
      if (!summary.metal.ready && !summary.bempp.ready) {
        return (
          summary.metal.guidance ||
          summary.bempp.guidance ||
          'Metal BEM or Bempp must be ready for BEM simulation.'
        );
      }
      return null;

    case 'chart-render':
    case 'matplotlib':
      return null;

    default:
      return null;
  }
}
