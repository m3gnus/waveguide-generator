export function displayResults(panel, results = null) {
  if (results) {
    panel.lastResults = results;
  }
}

function isObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function formatSymmetryTypeLabel(symmetryType) {
  switch (String(symmetryType || 'full').toLowerCase()) {
    case 'half_x':
      return 'Half-domain (X symmetry)';
    case 'half_z':
      return 'Half-domain (Z symmetry)';
    case 'quarter_xz':
      return 'Quarter-domain (X/Z symmetry)';
    case 'full':
      return 'Full model';
    default:
      return String(symmetryType || 'Unknown')
        .replaceAll('_', ' ')
        .replace(/\b\w/g, (match) => match.toUpperCase());
  }
}

function formatSymmetryPlaneLabel(plane) {
  switch (String(plane || '').toUpperCase()) {
    case 'YZ':
      return 'YZ plane';
    case 'XY':
      return 'XY plane';
    default:
      return String(plane || '')
        .replaceAll('_', ' ')
        .trim() || 'Unknown';
  }
}

function formatSymmetryReasonLabel(reason) {
  switch (String(reason || '').toLowerCase()) {
    case 'applied':
      return 'Applied';
    case 'disabled':
      return 'Disabled';
    case 'no_geometric_symmetry':
      return 'No geometric symmetry';
    case 'excitation_off_center':
      return 'Off-center source';
    case 'missing_original_mesh':
      return 'Missing original mesh';
    default:
      return String(reason || 'Unknown')
        .replaceAll('_', ' ')
        .replace(/\b\w/g, (match) => match.toUpperCase());
  }
}

function formatReductionFactor(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 1) {
    return 'None';
  }
  const rounded = Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(2).replace(/\.?0+$/, '');
  return `${rounded}x`;
}

function formatSourceAlignment(value) {
  if (value === true) {
    return 'Centered';
  }
  if (value === false) {
    return 'Off-center';
  }
  return 'Not checked';
}

function formatPlanesSummary(planes) {
  if (!planes.length) {
    return 'no symmetry planes';
  }
  if (planes.length === 1) {
    return formatSymmetryPlaneLabel(planes[0]);
  }
  if (planes.length === 2) {
    return `${formatSymmetryPlaneLabel(planes[0])} and ${formatSymmetryPlaneLabel(planes[1])}`;
  }
  return planes.map((plane) => formatSymmetryPlaneLabel(plane)).join(', ');
}

function buildSummaryDetails({
  requested,
  applied,
  reason,
  decisionLabel,
  detectedLabel,
  planes,
}) {
  const planesText = formatPlanesSummary(planes);
  if (!requested) {
    return 'Symmetry reduction was disabled for this run, so the solver kept the full model.';
  }
  if (applied) {
    return `The solver applied ${decisionLabel.toLowerCase()} reduction using ${planesText}.`;
  }
  if (reason === 'excitation_off_center') {
    return `The solver detected ${detectedLabel.toLowerCase()} across ${planesText}, but the source was off-center so it kept the full model.`;
  }
  if (reason === 'missing_original_mesh') {
    return 'The solver could not inspect the original mesh for symmetry, so it kept the full model.';
  }
  if (reason === 'no_geometric_symmetry') {
    return 'The solver did not find usable geometric symmetry, so it kept the full model.';
  }
  return 'The solver kept the full model for this run.';
}

export function getSymmetryPolicySummary(results = null) {
  const metadata = isObject(results?.metadata) ? results.metadata : null;
  if (!metadata || !isObject(metadata.symmetry_policy)) {
    return null;
  }

  const policy = metadata.symmetry_policy;
  const symmetry = isObject(metadata.symmetry) ? metadata.symmetry : {};
  const requested = Boolean(policy.requested);
  const applied = Boolean(policy.applied);
  const reason = String(policy.reason || '').toLowerCase();
  const detectedType = String(policy.detected_symmetry_type || 'full').toLowerCase();
  const decisionType = applied
    ? String(symmetry.symmetry_type || policy.detected_symmetry_type || 'full').toLowerCase()
    : 'full';
  const planes = Array.isArray(policy.detected_symmetry_planes)
    ? policy.detected_symmetry_planes
        .map((plane) => String(plane || '').trim())
        .filter(Boolean)
    : [];
  const detectedReduction = Number(
    policy.detected_reduction_factor ?? symmetry.reduction_factor ?? policy.reduction_factor ?? 1
  );
  const appliedReduction = Number(policy.reduction_factor ?? symmetry.reduction_factor ?? 1);
  const decisionLabel = formatSymmetryTypeLabel(decisionType);
  const detectedLabel = formatSymmetryTypeLabel(detectedType);
  const reductionText = applied
    ? `${formatReductionFactor(appliedReduction)} applied`
    : detectedType !== 'full'
      ? `${formatReductionFactor(detectedReduction)} available`
      : 'None';

  let headline = 'Kept full model';
  let tone = 'neutral';
  if (applied) {
    headline = `Applied ${decisionLabel.toLowerCase()} reduction`;
    tone = 'success';
  } else if (reason === 'excitation_off_center') {
    headline = 'Kept full model after source alignment check';
    tone = 'warning';
  } else if (!requested) {
    headline = 'Kept full model with symmetry disabled';
  } else if (reason === 'no_geometric_symmetry') {
    headline = 'Kept full model with no usable symmetry';
  } else if (reason === 'missing_original_mesh') {
    headline = 'Kept full model without original mesh';
  }

  return {
    badge: applied ? 'Reduced' : 'Full model',
    headline,
    tone,
    details: buildSummaryDetails({
      requested,
      applied,
      reason,
      decisionLabel,
      detectedLabel,
      planes,
    }),
    items: [
      { label: 'Requested', value: requested ? 'Enabled' : 'Disabled' },
      { label: 'Decision', value: decisionLabel },
      { label: 'Detected geometry', value: detectedLabel },
      {
        label: 'Symmetry planes',
        value: planes.length ? planes.map((plane) => formatSymmetryPlaneLabel(plane)).join(', ') : 'None',
      },
      { label: 'Source alignment', value: formatSourceAlignment(policy.excitation_centered) },
      { label: 'Reduction', value: reductionText },
      { label: 'Reason', value: formatSymmetryReasonLabel(reason) },
    ],
  };
}

export function renderSymmetryPolicySummary(results = null) {
  const summary = getSymmetryPolicySummary(results);
  if (!summary) {
    return '';
  }

  const itemsMarkup = summary.items
    .map(
      (item) => `
        <div class="view-results-summary-item">
          <span class="view-results-summary-label">${escapeHtml(item.label)}</span>
          <span class="view-results-summary-value">${escapeHtml(item.value)}</span>
        </div>
      `
    )
    .join('');

  return `
    <section class="view-results-summary" aria-label="Symmetry policy summary">
      <div class="view-results-summary-header">
        <div class="view-results-summary-copy">
          <div class="view-results-summary-title">Symmetry Policy</div>
          <div class="view-results-summary-headline">${escapeHtml(summary.headline)}</div>
        </div>
        <span class="view-results-summary-badge view-results-summary-badge--${escapeHtml(summary.tone)}">${escapeHtml(summary.badge)}</span>
      </div>
      <div class="view-results-summary-text">${escapeHtml(summary.details)}</div>
      <div class="view-results-summary-grid">${itemsMarkup}</div>
    </section>
  `;
}
