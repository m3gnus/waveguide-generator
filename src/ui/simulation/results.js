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

function formatSolveTime(seconds) {
  const s = Number(seconds);
  if (!Number.isFinite(s) || s <= 0) return 'N/A';
  if (s < 60) return `${s.toFixed(1)}s`;
  const mins = Math.floor(s / 60);
  const secs = s % 60;
  return `${mins}m ${secs.toFixed(0)}s`;
}

function formatFrequencyHz(hz) {
  const v = Number(hz);
  if (!Number.isFinite(v)) return 'N/A';
  if (v >= 1000) return `${(v / 1000).toFixed(1)} kHz`;
  return `${v.toFixed(0)} Hz`;
}

function formatCount(count) {
  const n = Number(count);
  if (!Number.isFinite(n)) return 'N/A';
  return n.toLocaleString();
}

function formatLengthMeters(meters) {
  const n = Number(meters);
  if (!Number.isFinite(n) || n < 0) return null;
  if (n === 0) return '0 mm';

  if (Math.abs(n) < 1) {
    const mm = n * 1000;
    const absMm = Math.abs(mm);
    const decimals = absMm >= 100 ? 0 : absMm >= 10 ? 1 : 2;
    return `${mm.toFixed(decimals)} mm`;
  }

  const decimals = Math.abs(n) >= 10 ? 1 : 2;
  return `${n.toFixed(decimals)} m`;
}

function resolveMeshStats(results = null, job = null) {
  const metadata = isObject(results?.metadata) ? results.metadata : null;
  const candidates = [
    job?.meshStats,
    metadata?.meshStats,
    metadata?.mesh_stats,
    metadata?.mesh,
    results?.meshStats,
    results?.mesh_stats,
  ];

  return candidates.find((candidate) => isObject(candidate)) || null;
}

function resolveDimensionValue(dimensions, name) {
  if (!isObject(dimensions)) return null;
  const value = Number(dimensions[name] ?? dimensions[`${name}_m`] ?? dimensions[`${name}M`]);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

function resolveBoundsDimension(bounds, minKey, maxKey) {
  if (!isObject(bounds)) return null;
  const min = Number(bounds[minKey] ?? bounds[minKey.replace('_', '')]);
  const max = Number(bounds[maxKey] ?? bounds[maxKey.replace('_', '')]);
  if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
  const value = max - min;
  return value >= 0 ? value : null;
}

function resolveWaveguideDimensions(meshStats) {
  if (!isObject(meshStats)) return null;

  const dimensions =
    (isObject(meshStats.dimensions_m) && meshStats.dimensions_m) ||
    (isObject(meshStats.dimensionsM) && meshStats.dimensionsM) ||
    (isObject(meshStats.dimensions) && meshStats.dimensions) ||
    null;
  let width = resolveDimensionValue(dimensions, 'width');
  let height = resolveDimensionValue(dimensions, 'height');
  let depth = resolveDimensionValue(dimensions, 'depth');

  if (width == null || height == null || depth == null) {
    const bounds =
      (isObject(meshStats.bounds_m) && meshStats.bounds_m) ||
      (isObject(meshStats.boundsM) && meshStats.boundsM) ||
      (isObject(meshStats.bounds) && meshStats.bounds) ||
      null;
    width = width ?? resolveBoundsDimension(bounds, 'min_x', 'max_x');
    height = height ?? resolveBoundsDimension(bounds, 'min_z', 'max_z');
    depth = depth ?? resolveBoundsDimension(bounds, 'min_y', 'max_y');
  }

  return width != null && height != null && depth != null ? { width, height, depth } : null;
}

function formatDegrees(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  const rounded = Math.round(n);
  const display = Math.abs(n - rounded) < 1e-9 ? String(rounded) : n.toFixed(1);
  return `${display}\u00b0`;
}

function formatAxesSummary(axes) {
  if (!Array.isArray(axes) || axes.length === 0) return null;
  const labels = axes
    .map((axis) =>
      String(axis || '')
        .trim()
        .toLowerCase()
    )
    .filter((axis, index, values) => axis && values.indexOf(axis) === index)
    .map((axis) =>
      axis
        .split(/[^a-z0-9]+/i)
        .filter(Boolean)
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ')
    );
  return labels.length > 0 ? labels.join(', ') : null;
}

function resolveEnabledDirectivityAxes(metadataDirectivity, resultDirectivity) {
  const fromMetadata = Array.isArray(metadataDirectivity?.enabled_axes)
    ? metadataDirectivity.enabled_axes
    : null;
  if (fromMetadata && fromMetadata.length > 0) {
    return fromMetadata
      .map((axis) =>
        String(axis || '')
          .trim()
          .toLowerCase()
      )
      .filter(Boolean);
  }

  const fromPlaneDescriptors = Array.isArray(metadataDirectivity?.planes)
    ? metadataDirectivity.planes
        .map((plane) =>
          String(plane?.id || '')
            .trim()
            .toLowerCase()
        )
        .filter(Boolean)
    : [];
  if (fromPlaneDescriptors.length > 0) {
    return fromPlaneDescriptors;
  }

  if (isObject(resultDirectivity)) {
    return Object.entries(resultDirectivity)
      .filter(([, patterns]) => Array.isArray(patterns))
      .map(([axis]) =>
        String(axis || '')
          .trim()
          .toLowerCase()
      )
      .filter(Boolean);
  }

  return [];
}

function formatLocalDateTime(isoString) {
  const parsed = Date.parse(String(isoString || ''));
  if (!Number.isFinite(parsed)) {
    return null;
  }

  const date = new Date(parsed);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  return `${year}-${month}-${day} ${hours}:${minutes}`;
}

function resolveJobTimestampSummary(job = null) {
  const completed = formatLocalDateTime(job?.completedAt);
  if (completed) {
    return { label: 'Completed', value: completed };
  }

  const started = formatLocalDateTime(job?.startedAt);
  if (started) {
    return { label: 'Started', value: started };
  }

  const created = formatLocalDateTime(job?.createdAt);
  if (created) {
    return { label: 'Queued', value: created };
  }

  return null;
}

const MAX_RESULT_DIAGNOSTICS = 3;

function normalizeDiagnosticMessage(entry) {
  if (entry == null) return null;
  if (!isObject(entry)) {
    const message = String(entry).trim();
    return message || null;
  }

  const message = String(entry.detail ?? entry.message ?? entry.error ?? entry.code ?? '').trim();
  return message || null;
}

function normalizeDiagnosticList(value) {
  return Array.isArray(value) ? value.map(normalizeDiagnosticMessage).filter(Boolean) : [];
}

function formatFailureDiagnostic(failure) {
  const message = normalizeDiagnosticMessage(failure);
  if (!message) return null;

  if (!isObject(failure)) {
    return {
      label: 'Failure',
      value: message,
    };
  }

  const frequency = Number(failure.frequency_hz ?? failure.frequencyHz ?? failure.frequency);
  const stage = String(failure.stage ?? '').trim();
  const code = String(failure.code ?? '').trim();
  const label = Number.isFinite(frequency) ? formatFrequencyHz(frequency) : stage || 'Failure';
  const prefix = code && !message.includes(code) ? `${code}: ` : '';

  return {
    label,
    value: `${prefix}${message}`,
  };
}

function formatDiagnosticCount(count, fallback = 0) {
  const parsed = Number(count);
  if (Number.isFinite(parsed) && parsed > 0) return parsed;
  return fallback;
}

export function renderResultDiagnostics(results = null) {
  const metadata = isObject(results?.metadata) ? results.metadata : null;
  if (!metadata) return '';

  const meshValidation = isObject(metadata.mesh_validation) ? metadata.mesh_validation : null;
  const meshWarnings = normalizeDiagnosticList(meshValidation?.warnings);
  const resultWarnings = normalizeDiagnosticList(metadata.warnings);
  const failures = Array.isArray(metadata.failures)
    ? metadata.failures.map(formatFailureDiagnostic).filter(Boolean)
    : [];
  const warningCount = formatDiagnosticCount(metadata.warning_count, resultWarnings.length);
  const failureCount = formatDiagnosticCount(metadata.failure_count, failures.length);
  const meshIsInvalid = meshValidation?.is_valid === false || meshValidation?.valid === false;
  const partialSuccess = metadata.partial_success === true;

  if (
    !meshIsInvalid &&
    meshWarnings.length === 0 &&
    warningCount === 0 &&
    failureCount === 0 &&
    !partialSuccess
  ) {
    return '';
  }

  const items = [];
  if (meshIsInvalid || meshWarnings.length > 0) {
    const mode = String(meshValidation?.mode ?? '').trim();
    const status = meshIsInvalid ? 'Invalid' : 'Warnings';
    items.push({
      label: 'Mesh validation',
      value: mode ? `${status} (${mode} mode)` : status,
    });
  }
  if (warningCount > 0) {
    items.push({
      label: 'Warnings',
      value: `${formatCount(warningCount)} warning${warningCount === 1 ? '' : 's'}`,
    });
  }
  if (failureCount > 0) {
    items.push({
      label: 'Frequency failures',
      value: `${formatCount(failureCount)} failed`,
    });
  }
  if (partialSuccess) {
    items.push({
      label: 'Solve status',
      value: 'Partial success',
    });
  }

  const itemsMarkup = items
    .map(
      (item) => `
        <div class="view-results-summary-item">
          <span class="view-results-summary-label">${escapeHtml(item.label)}</span>
          <span class="view-results-summary-value">${escapeHtml(item.value)}</span>
        </div>
      `
    )
    .join('');

  const renderMessageRows = (messages) =>
    messages
      .slice(0, MAX_RESULT_DIAGNOSTICS)
      .map(
        (message) => `
          <div class="view-results-diagnostics-row">
            <span class="view-results-diagnostics-label">Warning</span>
            <span class="view-results-diagnostics-value">${escapeHtml(message)}</span>
          </div>
        `
      )
      .join('');

  const renderFailureRows = () =>
    failures
      .slice(0, MAX_RESULT_DIAGNOSTICS)
      .map(
        (failure) => `
          <div class="view-results-diagnostics-row">
            <span class="view-results-diagnostics-label">${escapeHtml(failure.label)}</span>
            <span class="view-results-diagnostics-value">${escapeHtml(failure.value)}</span>
          </div>
        `
      )
      .join('');

  const hiddenMeshWarningCount = Math.max(0, meshWarnings.length - MAX_RESULT_DIAGNOSTICS);
  const hiddenWarningCount = Math.max(0, resultWarnings.length - MAX_RESULT_DIAGNOSTICS);
  const hiddenFailureCount = Math.max(0, failures.length - MAX_RESULT_DIAGNOSTICS);
  const detailSections = [
    meshWarnings.length > 0
      ? `
        <div class="view-results-diagnostics-section">
          <div class="view-results-diagnostics-section-title">Mesh Validation Warnings</div>
          ${renderMessageRows(meshWarnings)}
          ${
            hiddenMeshWarningCount > 0
              ? `<div class="view-results-diagnostics-more">+${formatCount(hiddenMeshWarningCount)} more</div>`
              : ''
          }
        </div>
      `
      : '',
    resultWarnings.length > 0
      ? `
        <div class="view-results-diagnostics-section">
          <div class="view-results-diagnostics-section-title">Run Warnings</div>
          ${renderMessageRows(resultWarnings)}
          ${
            hiddenWarningCount > 0
              ? `<div class="view-results-diagnostics-more">+${formatCount(hiddenWarningCount)} more</div>`
              : ''
          }
        </div>
      `
      : '',
    failures.length > 0
      ? `
        <div class="view-results-diagnostics-section">
          <div class="view-results-diagnostics-section-title">Failed Frequencies</div>
          ${renderFailureRows()}
          ${
            hiddenFailureCount > 0
              ? `<div class="view-results-diagnostics-more">+${formatCount(hiddenFailureCount)} more</div>`
              : ''
          }
        </div>
      `
      : '',
  ].join('');

  return `
    <section class="view-results-summary view-results-diagnostics" aria-label="Result validation diagnostics">
      <div class="view-results-summary-header">
        <div class="view-results-summary-copy">
          <div class="view-results-summary-title">Result Diagnostics</div>
          <div class="view-results-summary-text">Review warnings before trusting plots.</div>
        </div>
        <span class="view-results-summary-badge view-results-summary-badge--warning">Review</span>
      </div>
      <div class="view-results-summary-grid">${itemsMarkup}</div>
      ${detailSections ? `<div class="view-results-diagnostics-list">${detailSections}</div>` : ''}
    </section>
  `;
}

export function renderSolveStatsSummary(results = null, job = null) {
  const metadata = isObject(results?.metadata) ? results.metadata : null;
  if (!metadata) return '';

  const performance = isObject(metadata.performance) ? metadata.performance : {};
  const observation = isObject(metadata.observation) ? metadata.observation : null;
  const directivity = isObject(metadata.directivity) ? metadata.directivity : null;
  const directivityResults = isObject(results?.directivity) ? results.directivity : null;
  const frequencies = Array.isArray(results?.frequencies) ? results.frequencies : [];

  const totalTime = performance.total_time_seconds;
  const freqCount = frequencies.length;
  const freqMin = freqCount > 0 ? Math.min(...frequencies) : null;
  const freqMax = freqCount > 0 ? Math.max(...frequencies) : null;

  const meshStats = resolveMeshStats(results, job);
  const vertexCount = meshStats?.vertex_count ?? meshStats?.vertexCount;
  const triangleCount = meshStats?.triangle_count ?? meshStats?.triangleCount;
  const waveguideDimensions = resolveWaveguideDimensions(meshStats);

  const obsDistM = Number(directivity?.effective_distance_m ?? observation?.effective_distance_m);
  const requestedObsDistM = Number(
    directivity?.requested_distance_m ?? observation?.requested_distance_m
  );
  const configSummary = isObject(job?.configSummary) ? job.configSummary : {};
  const obsOrigin = String(
    directivity?.observation_origin ?? configSummary.observation_origin ?? 'mouth'
  )
    .trim()
    .toLowerCase();

  const items = [];
  const timestampSummary = resolveJobTimestampSummary(job);

  if (timestampSummary) {
    items.push(timestampSummary);
  }

  if (Number.isFinite(totalTime) && totalTime > 0) {
    items.push({ label: 'Solve time', value: formatSolveTime(totalTime) });
  }

  if (freqCount > 0) {
    const rangeStr =
      freqMin != null && freqMax != null
        ? `${formatFrequencyHz(freqMin)} – ${formatFrequencyHz(freqMax)}`
        : 'N/A';
    items.push({ label: 'Frequency range', value: rangeStr });
    items.push({ label: 'Frequency count', value: String(freqCount) });
  }

  if (Number.isFinite(vertexCount)) {
    items.push({ label: 'Vertices', value: formatCount(vertexCount) });
  }
  if (Number.isFinite(triangleCount)) {
    items.push({ label: 'Triangles', value: formatCount(triangleCount) });
  }
  if (waveguideDimensions) {
    const height = formatLengthMeters(waveguideDimensions.height);
    const depth = formatLengthMeters(waveguideDimensions.depth);
    const width = formatLengthMeters(waveguideDimensions.width);
    if (height && depth && width) {
      items.push({
        label: 'Waveguide shape',
        value: `Height ${height}, Depth ${depth}, Width ${width}`,
      });
    }
  }

  if (Number.isFinite(obsDistM)) {
    const originLabel = obsOrigin === 'throat' ? 'throat' : 'mouth';
    const requestedSuffix =
      Number.isFinite(requestedObsDistM) && Math.abs(requestedObsDistM - obsDistM) > 1e-9
        ? ` (requested ${requestedObsDistM.toFixed(2)} m)`
        : '';
    items.push({
      label: 'Observation',
      value: `${obsDistM.toFixed(2)} m from ${originLabel}${requestedSuffix}`,
    });
  }

  if (directivity) {
    const angleRange = Array.isArray(directivity.angle_range_degrees)
      ? directivity.angle_range_degrees
      : [];
    const angleStart = formatDegrees(angleRange[0]);
    const angleEnd = formatDegrees(angleRange[1]);
    const sampleCount = Number(directivity.sample_count);
    const angularStep = formatDegrees(directivity.angular_step_degrees);
    const enabledAxesList = resolveEnabledDirectivityAxes(directivity, directivityResults);
    const enabledAxes = formatAxesSummary(enabledAxesList);
    const normalization = formatDegrees(directivity.normalization_angle_degrees);
    const diagonalAngle = formatDegrees(directivity.diagonal_angle_degrees);

    if (angleStart && angleEnd) {
      items.push({
        label: 'Polar sweep',
        value: `${angleStart} – ${angleEnd}`,
      });
    }
    if (angularStep && Number.isFinite(sampleCount)) {
      items.push({
        label: 'Angular sampling',
        value: `${angularStep} step, ${formatCount(sampleCount)} samples`,
      });
    } else if (Number.isFinite(sampleCount)) {
      items.push({
        label: 'Angular sampling',
        value: `${formatCount(sampleCount)} samples`,
      });
    }
    if (enabledAxes) {
      items.push({ label: 'Axes', value: enabledAxes });
    }
    if (normalization) {
      items.push({ label: 'Normalization', value: normalization });
    }
    if (diagonalAngle && enabledAxesList.includes('diagonal')) {
      items.push({ label: 'Diagonal plane', value: diagonalAngle });
    }
  }

  if (items.length === 0) return '';

  const itemsMarkup = items
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
    <section class="view-results-summary" aria-label="Simulation summary">
      <div class="view-results-summary-header">
        <div class="view-results-summary-copy">
          <div class="view-results-summary-title">Simulation Summary</div>
        </div>
      </div>
      <div class="view-results-summary-grid">${itemsMarkup}</div>
    </section>
  `;
}
