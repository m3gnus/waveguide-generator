export function displayResults(panel, results = null) {
  if (results) {
    panel.lastResults = results;
  }
}

function isObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function cloneSummaryItem(item = {}) {
  return {
    label: String(item.label || '').trim(),
    value: String(item.value || '').trim()
  };
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

function readRequestedSymmetryFromJob(job = null) {
  if (!isObject(job)) {
    return null;
  }

  if (typeof job?.script?.enableSymmetry === 'boolean') {
    return job.script.enableSymmetry;
  }

  const configSummary = isObject(job.configSummary) ? job.configSummary : {};
  if (typeof configSummary.enable_symmetry === 'boolean') {
    return configSummary.enable_symmetry;
  }
  if (typeof configSummary.enableSymmetry === 'boolean') {
    return configSummary.enableSymmetry;
  }

  return null;
}

function normalizeStoredSymmetrySummary(summary = null) {
  if (!isObject(summary)) {
    return null;
  }

  const badge = String(summary.badge || '').trim();
  const headline = String(summary.headline || '').trim();
  if (!badge || !headline) {
    return null;
  }

  const details = String(summary.details || '').trim();
  const tone = String(summary.tone || 'neutral').trim() || 'neutral';
  const items = Array.isArray(summary.items)
    ? summary.items
        .map((item) => cloneSummaryItem(item))
        .filter((item) => item.label && item.value)
    : [];

  return {
    badge,
    headline,
    details,
    tone,
    items
  };
}

export function getJobSymmetrySummary(job = null) {
  const stored = normalizeStoredSymmetrySummary(job?.symmetrySummary ?? job?.symmetry_summary ?? null);
  if (stored) {
    return stored;
  }

  const requested = readRequestedSymmetryFromJob(job);
  if (typeof requested !== 'boolean') {
    return null;
  }

  if (requested) {
    return {
      badge: 'Requested',
      headline: 'Symmetry reduction requested',
      details: 'The solve request allows symmetry reduction. The final solver policy appears after results are fetched.',
      tone: 'neutral',
      items: [
        { label: 'Requested', value: 'Enabled' },
        { label: 'Decision', value: 'Pending results' }
      ]
    };
  }

  return {
    badge: 'Full model',
    headline: 'Symmetry reduction disabled',
    details: 'The solve request disabled symmetry reduction, so the solver keeps the full model.',
    tone: 'neutral',
    items: [
      { label: 'Requested', value: 'Disabled' },
      { label: 'Decision', value: 'Full model' }
    ]
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
