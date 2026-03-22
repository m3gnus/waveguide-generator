const FEATURE_COMPONENTS = Object.freeze({
  meshBuild: ['gmsh_python'],
  solve: ['bempp_cl', 'opencl_runtime'],
  charts: ['matplotlib']
});

function normalizeComponent(component) {
  if (!component || typeof component !== 'object') {
    return null;
  }

  return {
    id: String(component.id || '').trim(),
    name: String(component.name || '').trim() || 'Unknown dependency',
    category: String(component.category || 'required').trim().toLowerCase() || 'required',
    status: String(component.status || 'missing').trim().toLowerCase() || 'missing',
    featureImpact: String(component.featureImpact || '').trim(),
    detail: String(component.detail || '').trim(),
    requiredFor: String(component.requiredFor || '').trim(),
    guidance: Array.isArray(component.guidance)
      ? component.guidance
        .map((entry) => String(entry || '').trim())
        .filter(Boolean)
      : []
  };
}

export function getRuntimeDoctorComponents(health) {
  const components = health?.dependencyDoctor?.components;
  if (!Array.isArray(components)) {
    return [];
  }

  return components
    .map(normalizeComponent)
    .filter(Boolean);
}

export function getRuntimeDoctorIssues(
  health,
  { features = [], includeOptional = true } = {}
) {
  const featureIds = new Set(
    (features || [])
      .flatMap((feature) => FEATURE_COMPONENTS[String(feature || '').trim()] || [])
  );

  return getRuntimeDoctorComponents(health).filter((component) => {
    if (component.status === 'installed') {
      return false;
    }
    if (!includeOptional && component.category === 'optional') {
      return false;
    }
    if (featureIds.size === 0) {
      return true;
    }
    return featureIds.has(component.id);
  });
}

export function summarizeRuntimeDoctor(health) {
  const summary = health?.dependencyDoctor?.summary;
  const issues = getRuntimeDoctorIssues(health);
  const requiredIssues = issues.filter((component) => component.category !== 'optional');
  const optionalIssues = issues.filter((component) => component.category === 'optional');

  return {
    requiredReady: summary?.requiredReady !== false && requiredIssues.length === 0,
    requiredIssues,
    optionalIssues
  };
}

function formatIssueLine(component) {
  const parts = [];
  if (component.featureImpact) {
    parts.push(component.featureImpact);
  } else if (component.detail) {
    parts.push(component.detail);
  }

  const primaryGuidance = component.guidance[0];
  if (primaryGuidance) {
    parts.push(primaryGuidance);
  }

  return `- ${component.name}: ${parts.join(' ')}`.trim();
}

export function formatDependencyBlockMessage(
  health,
  {
    features = [],
    fallback = 'Backend dependency check failed.',
    includeOptional = true
  } = {}
) {
  const issues = getRuntimeDoctorIssues(health, { features, includeOptional });
  if (issues.length === 0) {
    return fallback;
  }

  return [fallback, ...issues.map(formatIssueLine)].join('\n');
}
