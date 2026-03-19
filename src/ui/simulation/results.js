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

export function renderSolveStatsSummary(results = null, job = null) {
  const metadata = isObject(results?.metadata) ? results.metadata : null;
  if (!metadata) return "";

  const performance = isObject(metadata.performance)
    ? metadata.performance
    : {};
  const observation = isObject(metadata.observation)
    ? metadata.observation
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

  const obsDistM = observation ? Number(observation.effective_distance_m) : NaN;
  const configSummary = isObject(job?.configSummary) ? job.configSummary : {};
  const obsOrigin = configSummary.observation_origin || "mouth";

  const items = [];

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
    items.push({
      label: "Observation",
      value: `${obsDistM.toFixed(2)} m from ${originLabel}`,
    });
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
