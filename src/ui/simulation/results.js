export function displayResults(panel, results = null) {
  if (results) {
    panel.lastResults = results;
  }
}

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatSolveTime(seconds) {
  const s = Number(seconds);
  if (!Number.isFinite(s) || s <= 0) return "N/A";
  if (s < 60) return `${s.toFixed(1)}s`;
  const mins = Math.floor(s / 60);
  const secs = s % 60;
  return `${mins}m ${secs.toFixed(0)}s`;
}

function formatFrequencyHz(hz) {
  const v = Number(hz);
  if (!Number.isFinite(v)) return "N/A";
  if (v >= 1000) return `${(v / 1000).toFixed(1)} kHz`;
  return `${v.toFixed(0)} Hz`;
}

function formatCount(count) {
  const n = Number(count);
  if (!Number.isFinite(n)) return "N/A";
  return n.toLocaleString();
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
    .map((axis) => String(axis || "").trim().toLowerCase())
    .filter((axis, index, values) =>
      ["horizontal", "vertical", "diagonal"].includes(axis) &&
      values.indexOf(axis) === index
    )
    .map((axis) => axis.charAt(0).toUpperCase() + axis.slice(1));
  return labels.length > 0 ? labels.join(", ") : null;
}

function formatLocalDateTime(isoString) {
  const parsed = Date.parse(String(isoString || ""));
  if (!Number.isFinite(parsed)) {
    return null;
  }

  const date = new Date(parsed);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  return `${year}-${month}-${day} ${hours}:${minutes}`;
}

function resolveJobTimestampSummary(job = null) {
  const completed = formatLocalDateTime(job?.completedAt);
  if (completed) {
    return { label: "Completed", value: completed };
  }

  const started = formatLocalDateTime(job?.startedAt);
  if (started) {
    return { label: "Started", value: started };
  }

  const created = formatLocalDateTime(job?.createdAt);
  if (created) {
    return { label: "Queued", value: created };
  }

  return null;
}

export function renderSolveStatsSummary(results = null, job = null) {
  const metadata = isObject(results?.metadata) ? results.metadata : null;
  if (!metadata) return "";

  const performance = isObject(metadata.performance)
    ? metadata.performance
    : {};
  const observation = isObject(metadata.observation)
    ? metadata.observation
    : null;
  const directivity = isObject(metadata.directivity)
    ? metadata.directivity
    : null;
  const frequencies = Array.isArray(results?.frequencies)
    ? results.frequencies
    : [];

  const totalTime = performance.total_time_seconds;
  const freqCount = frequencies.length;
  const freqMin = freqCount > 0 ? Math.min(...frequencies) : null;
  const freqMax = freqCount > 0 ? Math.max(...frequencies) : null;

  const meshStats = isObject(job?.meshStats) ? job.meshStats : null;
  const vertexCount = meshStats?.vertex_count ?? meshStats?.vertexCount;
  const triangleCount = meshStats?.triangle_count ?? meshStats?.triangleCount;

  const obsDistM = Number(
    directivity?.effective_distance_m ?? observation?.effective_distance_m,
  );
  const requestedObsDistM = Number(
    directivity?.requested_distance_m ?? observation?.requested_distance_m,
  );
  const configSummary = isObject(job?.configSummary) ? job.configSummary : {};
  const obsOrigin = String(
    directivity?.observation_origin ?? configSummary.observation_origin ?? "mouth",
  ).trim().toLowerCase();

  const items = [];
  const timestampSummary = resolveJobTimestampSummary(job);

  if (timestampSummary) {
    items.push(timestampSummary);
  }

  if (Number.isFinite(totalTime) && totalTime > 0) {
    items.push({ label: "Solve time", value: formatSolveTime(totalTime) });
  }

  if (freqCount > 0) {
    const rangeStr =
      freqMin != null && freqMax != null
        ? `${formatFrequencyHz(freqMin)} – ${formatFrequencyHz(freqMax)}`
        : "N/A";
    items.push({ label: "Frequency range", value: rangeStr });
    items.push({ label: "Frequency count", value: String(freqCount) });
  }

  if (Number.isFinite(vertexCount)) {
    items.push({ label: "Vertices", value: formatCount(vertexCount) });
  }
  if (Number.isFinite(triangleCount)) {
    items.push({ label: "Triangles", value: formatCount(triangleCount) });
  }

  if (Number.isFinite(obsDistM)) {
    const originLabel = obsOrigin === "throat" ? "throat" : "mouth";
    const requestedSuffix =
      Number.isFinite(requestedObsDistM) &&
      Math.abs(requestedObsDistM - obsDistM) > 1e-9
        ? ` (requested ${requestedObsDistM.toFixed(2)} m)`
        : "";
    items.push({
      label: "Observation",
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
    const enabledAxes = formatAxesSummary(directivity.enabled_axes);
    const normalization = formatDegrees(directivity.normalization_angle_degrees);
    const diagonalAngle = formatDegrees(directivity.diagonal_angle_degrees);

    if (angleStart && angleEnd) {
      items.push({
        label: "Polar sweep",
        value: `${angleStart} – ${angleEnd}`,
      });
    }
    if (angularStep && Number.isFinite(sampleCount)) {
      items.push({
        label: "Angular sampling",
        value: `${angularStep} step, ${formatCount(sampleCount)} samples`,
      });
    } else if (Number.isFinite(sampleCount)) {
      items.push({
        label: "Angular sampling",
        value: `${formatCount(sampleCount)} samples`,
      });
    }
    if (enabledAxes) {
      items.push({ label: "Axes", value: enabledAxes });
    }
    if (normalization) {
      items.push({ label: "Normalization", value: normalization });
    }
    if (
      diagonalAngle &&
      Array.isArray(directivity.enabled_axes) &&
      directivity.enabled_axes.includes("diagonal")
    ) {
      items.push({ label: "Diagonal plane", value: diagonalAngle });
    }
  }

  if (items.length === 0) return "";

  const itemsMarkup = items
    .map(
      (item) => `
        <div class="view-results-summary-item">
          <span class="view-results-summary-label">${escapeHtml(item.label)}</span>
          <span class="view-results-summary-value">${escapeHtml(item.value)}</span>
        </div>
      `,
    )
    .join("");

  return `
    <section class="view-results-summary" aria-label="Solve statistics summary">
      <div class="view-results-summary-header">
        <div class="view-results-summary-copy">
          <div class="view-results-summary-title">Solve Statistics</div>
        </div>
      </div>
      <div class="view-results-summary-grid">${itemsMarkup}</div>
    </section>
  `;
}
