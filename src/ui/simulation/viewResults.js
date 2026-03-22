import { applySmoothing } from "../../results/smoothing.js";
import { extractPerPlaneDI } from "./diHelpers.js";
import { DEFAULT_BACKEND_URL } from "../../config/backendUrl.js";
import { renderSolveStatsSummary } from "./results.js";
import { trapFocus } from "../focusTrap.js";

const DEFAULT_DIRECTIVITY_REFERENCE_LEVEL = -6;
const DIRECTIVITY_REFERENCE_OPTIONS = [
  [-3, "-3 dB"],
  [-6, "-6 dB"],
  [-9, "-9 dB"],
  [-12, "-12 dB"],
];
const DIRECTIVITY_PLANE_ORDER = ["horizontal", "vertical", "diagonal"];

function normalizeDirectivityPayload(directivity) {
  if (!directivity || typeof directivity !== "object" || Array.isArray(directivity)) {
    return {};
  }

  const entries = Object.entries(directivity).filter(
    ([, patterns]) => Array.isArray(patterns),
  );
  entries.sort(([a], [b]) => {
    const aKey = String(a || "").trim().toLowerCase();
    const bKey = String(b || "").trim().toLowerCase();
    const aIndex = DIRECTIVITY_PLANE_ORDER.indexOf(aKey);
    const bIndex = DIRECTIVITY_PLANE_ORDER.indexOf(bKey);
    if (aIndex !== -1 && bIndex !== -1) return aIndex - bIndex;
    if (aIndex !== -1) return -1;
    if (bIndex !== -1) return 1;
    return aKey.localeCompare(bKey);
  });
  return Object.fromEntries(entries);
}

function hasDirectivityPatterns(directivity) {
  return Object.values(directivity).some(
    (patterns) => Array.isArray(patterns) && patterns.length > 0,
  );
}

/**
 * Open a modal dialog displaying all result charts rendered server-side
 * by Matplotlib as high-quality PNG images.
 */
export async function openViewResultsModal(panel) {
  const preferredJobId = panel.activeJobId || panel.currentJobId;
  const job = preferredJobId ? panel.jobs?.get(preferredJobId) || null : null;
  const results =
    preferredJobId && panel.resultCache?.has(preferredJobId)
      ? panel.resultCache.get(preferredJobId)
      : panel.lastResults;
  if (!results) return;

  // Build modal DOM
  const backdrop = document.createElement("div");
  backdrop.className = "ui-choice-backdrop";

  const dialog = document.createElement("div");
  dialog.className = "ui-choice-dialog view-results-dialog";
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-label", "View Results");

  const header = document.createElement("div");
  header.className = "view-results-header";

  const title = document.createElement("h4");
  title.className = "ui-choice-title";
  title.textContent = "Simulation Results";
  header.appendChild(title);

  const headerActions = document.createElement("div");
  headerActions.className = "view-results-header-actions";

  // Smoothing dropdown in header
  const smoothingContainer = document.createElement("div");
  smoothingContainer.className = "view-results-smoothing";
  const smoothingLabel = document.createElement("label");
  smoothingLabel.textContent = "Smoothing";
  smoothingLabel.setAttribute("for", "vr-smoothing-select");
  smoothingContainer.appendChild(smoothingLabel);

  const smoothingSelect = document.createElement("select");
  smoothingSelect.id = "vr-smoothing-select";
  const smoothingOptions = [
    ["none", "None"],
    ["1/1", "1/1 Oct"],
    ["1/2", "1/2 Oct"],
    ["1/3", "1/3 Oct"],
    ["1/6", "1/6 Oct"],
    ["1/12", "1/12 Oct"],
    ["1/24", "1/24 Oct"],
    ["1/48", "1/48 Oct"],
    ["variable", "Variable"],
    ["psychoacoustic", "Psychoacoustic"],
    ["erb", "ERB"],
  ];
  for (const [value, text] of smoothingOptions) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = text;
    if (value === panel.currentSmoothing) opt.selected = true;
    smoothingSelect.appendChild(opt);
  }
  smoothingContainer.appendChild(smoothingSelect);
  headerActions.appendChild(smoothingContainer);

  const directivityContainer = document.createElement("div");
  directivityContainer.className = "view-results-smoothing";
  const directivityLabel = document.createElement("label");
  directivityLabel.textContent = "Map Ref";
  directivityLabel.setAttribute("for", "vr-directivity-ref-select");
  directivityContainer.appendChild(directivityLabel);

  const directivitySelect = document.createElement("select");
  directivitySelect.id = "vr-directivity-ref-select";
  const selectedReferenceLevel = resolveDirectivityReferenceLevel(
    panel?.currentDirectivityReferenceLevel,
  );
  for (const [value, text] of DIRECTIVITY_REFERENCE_OPTIONS) {
    const opt = document.createElement("option");
    opt.value = String(value);
    opt.textContent = text;
    if (value === selectedReferenceLevel) opt.selected = true;
    directivitySelect.appendChild(opt);
  }
  directivityContainer.appendChild(directivitySelect);
  headerActions.appendChild(directivityContainer);

  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "view-results-close";
  closeBtn.textContent = "\u00d7";
  closeBtn.title = "Close (Escape)";
  headerActions.appendChild(closeBtn);

  header.appendChild(headerActions);

  dialog.appendChild(header);

  const body = document.createElement("div");
  body.className = "view-results-body";

  const solveStatsMarkup = renderSolveStatsSummary(results, job);
  if (solveStatsMarkup) {
    const summaryWrapper = document.createElement("div");
    summaryWrapper.innerHTML = solveStatsMarkup.trim();
    const summarySection = summaryWrapper.firstElementChild;
    if (summarySection) {
      body.appendChild(summarySection);
    }
  }

  // Chart containers with loading placeholders
  const chartNames = [
    { key: "directivity_map", label: "Polar Directivity Map" },
    { key: "impedance", label: "Acoustic Impedance" },
    { key: "directivity_index", label: "Directivity Index" },
    { key: "frequency_response", label: "Frequency Response (SPL On-Axis)" },
  ];

  for (const chart of chartNames) {
    const container = document.createElement("div");
    container.className = "view-results-chart";

    const chartTitle = document.createElement("div");
    chartTitle.className = "view-results-chart-title";
    chartTitle.textContent = chart.label;
    container.appendChild(chartTitle);

    const imgContainer = document.createElement("div");
    imgContainer.id = `vr-${chart.key}`;
    imgContainer.className = "view-results-img";
    imgContainer.innerHTML =
      '<div class="view-results-loading">Rendering...</div>';
    container.appendChild(imgContainer);

    body.appendChild(container);
  }

  dialog.appendChild(body);
  backdrop.appendChild(dialog);

  let releaseFocus;
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    window.removeEventListener("keydown", onKeyDown);
    if (releaseFocus) releaseFocus();
    backdrop.remove();
  };

  const onKeyDown = (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
  };

  closeBtn.addEventListener("click", close);
  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) close();
  });
  window.addEventListener("keydown", onKeyDown);

  document.body.appendChild(backdrop);
  releaseFocus = trapFocus(dialog, { initialFocus: closeBtn });

  // Fetch and render charts (called on open and on smoothing change)
  function setChartLoading(chartKey) {
    const container = document.getElementById(`vr-${chartKey}`);
    if (container) {
      container.innerHTML =
        '<div class="view-results-loading">Rendering...</div>';
    }
  }

  function setChartImage(chartKey, label, imgData) {
    const container = document.getElementById(`vr-${chartKey}`);
    if (!container) return;
    if (imgData) {
      container.innerHTML = `<img src="${imgData}" alt="${label}" class="view-results-chart-img" />`;
    } else {
      container.innerHTML =
        '<div class="view-results-loading">No data available</div>';
    }
  }

  function showMatplotlibRequiredForCharts(chartKeys = []) {
    for (const chartKey of chartKeys) {
      const container = document.getElementById(`vr-${chartKey}`);
      if (!container) continue;
      container.innerHTML = `<div class="view-results-loading view-results-matplotlib-error">
        <div class="view-results-matplotlib-error-title">Matplotlib is required for chart rendering</div>
        <div class="view-results-matplotlib-error-detail">Install it with: <code>pip install matplotlib</code></div>
        <div class="view-results-matplotlib-error-hint">Then restart the backend server.</div>
      </div>`;
    }
  }

  const backendUrl = panel?.solver?.backendUrl || DEFAULT_BACKEND_URL;

  async function renderDirectivityMap() {
    const chart = chartNames.find((item) => item.key === "directivity_map");
    if (!chart) return;

    setChartLoading(chart.key);

    const directivity = normalizeDirectivityPayload(results.directivity);
    const splData = results.spl_on_axis || {};
    const frequencies = splData.frequencies || [];

    if (!frequencies.length || !hasDirectivityPatterns(directivity)) {
      setChartImage(chart.key, chart.label, null);
      return;
    }

    try {
      const response = await fetch(`${backendUrl}/api/render-directivity`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          frequencies,
          directivity,
          reference_level: resolveDirectivityReferenceLevel(
            panel?.currentDirectivityReferenceLevel,
          ),
        }),
      });

      if (!response.ok) {
        const detail = await response.text().catch(() => "");
        console.warn(
          `[view-results] Directivity render returned ${response.status}: ${detail}`,
        );
        showMatplotlibRequiredForCharts([chart.key]);
        return;
      }

      const data = await response.json();
      setChartImage(chart.key, chart.label, data.image || null);
    } catch (err) {
      console.warn("[view-results] Directivity render failed:", err.message);
      showMatplotlibRequiredForCharts([chart.key]);
    }
  }

  async function fetchCharts() {
    const splData = results.spl_on_axis || {};
    const frequencies = splData.frequencies || [];
    let spl = splData.spl || [];
    const diData = results.di || {};
    const diFrequencies = diData.frequencies || frequencies;
    let di = extractPerPlaneDI(diData);
    const impedanceData = results.impedance || {};
    const impedanceFrequencies = impedanceData.frequencies || frequencies;
    let impedanceReal = impedanceData.real || [];
    let impedanceImag = impedanceData.imaginary || [];
    const directivity = normalizeDirectivityPayload(results.directivity);

    if (panel.currentSmoothing !== "none") {
      spl = applySmoothing(frequencies, spl, panel.currentSmoothing);
      if (Array.isArray(di)) {
        di = applySmoothing(diFrequencies, di, panel.currentSmoothing);
      } else if (di && typeof di === "object") {
        const smoothedDi = {};
        for (const [plane, vals] of Object.entries(di)) {
          smoothedDi[plane] = applySmoothing(diFrequencies, vals, panel.currentSmoothing);
        }
        di = smoothedDi;
      }
      impedanceReal = applySmoothing(
        impedanceFrequencies,
        impedanceReal,
        panel.currentSmoothing,
      );
      impedanceImag = applySmoothing(
        impedanceFrequencies,
        impedanceImag,
        panel.currentSmoothing,
      );
    }

    // Show loading state
    for (const chart of chartNames) {
      const container = document.getElementById(`vr-${chart.key}`);
      if (container)
        container.innerHTML =
          '<div class="view-results-loading">Rendering...</div>';
    }

    const payload = {
      frequencies,
      spl,
      di,
      di_frequencies: diFrequencies,
      impedance_frequencies: impedanceFrequencies,
      impedance_real: impedanceReal,
      impedance_imaginary: impedanceImag,
      directivity,
    };

    try {
      const response = await fetch(`${backendUrl}/api/render-charts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const detail = await response.text().catch(() => "");
        console.warn(
          `[view-results] Server returned ${response.status}: ${detail}`,
        );
        showMatplotlibRequiredForCharts(chartNames.map((chart) => chart.key));
        return;
      }

      const data = await response.json();
      const charts = data.charts || {};

      for (const chart of chartNames) {
        if (chart.key === "directivity_map") {
          continue;
        }
        setChartImage(chart.key, chart.label, charts[chart.key] || null);
      }

      await renderDirectivityMap();
    } catch (err) {
      console.warn("[view-results] Fetch failed:", err.message);
      showMatplotlibRequiredForCharts(chartNames.map((chart) => chart.key));
    }
  }

  // Re-fetch charts when smoothing changes
  smoothingSelect.addEventListener("change", (e) => {
    panel.currentSmoothing = e.target.value;
    fetchCharts();
  });

  directivitySelect.addEventListener("change", (e) => {
    panel.currentDirectivityReferenceLevel = resolveDirectivityReferenceLevel(
      e.target.value,
    );
    renderDirectivityMap();
  });

  fetchCharts();
}

function resolveDirectivityReferenceLevel(value) {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) {
    return DEFAULT_DIRECTIVITY_REFERENCE_LEVEL;
  }
  const supportedLevels = DIRECTIVITY_REFERENCE_OPTIONS.map(([level]) => level);
  return supportedLevels.includes(numericValue)
    ? numericValue
    : DEFAULT_DIRECTIVITY_REFERENCE_LEVEL;
}
