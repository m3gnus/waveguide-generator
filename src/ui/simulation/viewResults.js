import { DEFAULT_BACKEND_URL } from '../../config/backendUrl.js';
import { renderResultDiagnostics, renderSolveStatsSummary } from './results.js';
import { trapFocus } from '../focusTrap.js';
import { getChartTheme } from '../settings/appearanceSettings.js';
import { applySmoothingSelection } from './smoothing.js';
import {
  CHART_TYPES,
  buildDirectivityPayload,
  buildLineChartsPayload,
  hasDirectivityPatterns,
  requestDirectivityMap,
  requestLineCharts,
} from './chartRequests.js';

const DEFAULT_DIRECTIVITY_REFERENCE_LEVEL = -6;
const DIRECTIVITY_REFERENCE_OPTIONS = [
  [-3, '-3 dB'],
  [-6, '-6 dB'],
  [-9, '-9 dB'],
  [-12, '-12 dB'],
];
/**
 * Open a modal dialog displaying all result charts rendered server-side
 * by Matplotlib as high-quality PNG images.
 */
export async function openViewResultsModal(panel, requestedJobId = null) {
  const preferredJobId = requestedJobId || panel.activeJobId || panel.currentJobId;
  const job = preferredJobId ? panel.jobs?.get(preferredJobId) || null : null;
  const results =
    preferredJobId && panel.resultCache?.has(preferredJobId)
      ? panel.resultCache.get(preferredJobId)
      : panel.lastResults;
  if (!results) return;

  // Build modal DOM
  const backdrop = document.createElement('div');
  backdrop.className = 'ui-choice-backdrop';

  const dialog = document.createElement('div');
  dialog.className = 'ui-choice-dialog view-results-dialog';
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-modal', 'true');
  dialog.setAttribute('aria-label', 'View Results');

  const header = document.createElement('div');
  header.className = 'view-results-header';

  const title = document.createElement('h4');
  title.className = 'ui-choice-title';
  title.textContent = 'Simulation Results';
  header.appendChild(title);

  const headerActions = document.createElement('div');
  headerActions.className = 'view-results-header-actions';

  // Smoothing dropdown in header
  const smoothingContainer = document.createElement('div');
  smoothingContainer.className = 'view-results-smoothing';
  const smoothingLabel = document.createElement('label');
  smoothingLabel.textContent = 'Smoothing';
  smoothingLabel.setAttribute('for', 'vr-smoothing-select');
  smoothingContainer.appendChild(smoothingLabel);

  const smoothingSelect = document.createElement('select');
  smoothingSelect.id = 'vr-smoothing-select';
  const smoothingOptions = [
    ['none', 'None'],
    ['1/1', '1/1 Oct'],
    ['1/2', '1/2 Oct'],
    ['1/3', '1/3 Oct'],
    ['1/6', '1/6 Oct'],
    ['1/12', '1/12 Oct'],
    ['1/24', '1/24 Oct'],
    ['1/48', '1/48 Oct'],
    ['variable', 'Variable'],
    ['psychoacoustic', 'Psychoacoustic'],
    ['erb', 'ERB'],
  ];
  for (const [value, text] of smoothingOptions) {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = text;
    if (value === panel.currentSmoothing) opt.selected = true;
    smoothingSelect.appendChild(opt);
  }
  smoothingContainer.appendChild(smoothingSelect);
  headerActions.appendChild(smoothingContainer);

  const directivityContainer = document.createElement('div');
  directivityContainer.className = 'view-results-smoothing';
  const directivityLabel = document.createElement('label');
  directivityLabel.textContent = 'Map Ref';
  directivityLabel.setAttribute('for', 'vr-directivity-ref-select');
  directivityContainer.appendChild(directivityLabel);

  const directivitySelect = document.createElement('select');
  directivitySelect.id = 'vr-directivity-ref-select';
  const selectedReferenceLevel = resolveDirectivityReferenceLevel(
    panel?.currentDirectivityReferenceLevel
  );
  for (const [value, text] of DIRECTIVITY_REFERENCE_OPTIONS) {
    const opt = document.createElement('option');
    opt.value = String(value);
    opt.textContent = text;
    if (value === selectedReferenceLevel) opt.selected = true;
    directivitySelect.appendChild(opt);
  }
  directivityContainer.appendChild(directivitySelect);
  headerActions.appendChild(directivityContainer);

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'view-results-close';
  closeBtn.textContent = '\u00d7';
  closeBtn.title = 'Close (Escape)';
  headerActions.appendChild(closeBtn);

  header.appendChild(headerActions);

  dialog.appendChild(header);

  const body = document.createElement('div');
  body.className = 'view-results-body';

  for (const summaryMarkup of [
    renderSolveStatsSummary(results, job),
    renderResultDiagnostics(results),
  ]) {
    if (!summaryMarkup) continue;
    const summaryWrapper = document.createElement('div');
    summaryWrapper.innerHTML = summaryMarkup.trim();
    const summarySection = summaryWrapper.firstElementChild;
    if (summarySection) {
      body.appendChild(summarySection);
    }
  }

  // Chart containers with loading placeholders
  const chartNames = CHART_TYPES;

  for (const chart of chartNames) {
    const container = document.createElement('div');
    container.className = 'view-results-chart';

    const chartTitle = document.createElement('div');
    chartTitle.className = 'view-results-chart-title';
    chartTitle.textContent = chart.label;
    container.appendChild(chartTitle);

    const imgContainer = document.createElement('div');
    imgContainer.id = `vr-${chart.key}`;
    imgContainer.className = 'view-results-img';
    imgContainer.innerHTML = '<div class="view-results-loading">Rendering...</div>';
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
    window.removeEventListener('keydown', onKeyDown);
    if (releaseFocus) releaseFocus();
    backdrop.remove();
  };

  const onKeyDown = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      close();
    }
  };

  closeBtn.addEventListener('click', close);
  backdrop.addEventListener('click', (event) => {
    if (event.target === backdrop) close();
  });
  window.addEventListener('keydown', onKeyDown);

  document.body.appendChild(backdrop);
  releaseFocus = trapFocus(dialog, { initialFocus: closeBtn });

  // Fetch and render charts (called on open and on smoothing change)
  function setChartLoading(chartKey) {
    const container = document.getElementById(`vr-${chartKey}`);
    if (container) {
      container.innerHTML = '<div class="view-results-loading">Rendering...</div>';
    }
  }

  function setChartImage(chartKey, label, imgData) {
    const container = document.getElementById(`vr-${chartKey}`);
    if (!container) return;
    if (imgData) {
      container.innerHTML = `<img src="${imgData}" alt="${label}" class="view-results-chart-img" />`;
    } else {
      container.innerHTML = '<div class="view-results-loading">No data available</div>';
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

  function showChartRenderError(chartKeys = [], message = 'Chart rendering failed.') {
    const safeMessage = escapeHtml(message);
    for (const chartKey of chartKeys) {
      const container = document.getElementById(`vr-${chartKey}`);
      if (!container) continue;
      container.innerHTML = `<div class="view-results-loading view-results-chart-error">${safeMessage}</div>`;
    }
  }

  function chartFailureMessage(detail, status) {
    const text = String(detail || '').trim();
    return `Chart rendering failed: ${text || `HTTP ${status}`}`;
  }

  const backendUrl = panel?.solver?.backendUrl || DEFAULT_BACKEND_URL;

  async function renderDirectivityMap() {
    const chart = chartNames.find((item) => item.key === 'directivity_map');
    if (!chart) return;

    setChartLoading(chart.key);

    const payload = buildDirectivityPayload(results, {
      referenceLevel: resolveDirectivityReferenceLevel(panel?.currentDirectivityReferenceLevel),
      theme: getChartTheme(),
    });

    if (!payload.frequencies.length || !hasDirectivityPatterns(payload.directivity)) {
      setChartImage(chart.key, chart.label, null);
      return;
    }

    const response = await requestDirectivityMap(backendUrl, payload);
    if (response.ok) {
      setChartImage(chart.key, chart.label, response.image);
      return;
    }

    if (response.kind === 'network') {
      console.warn('[view-results] Directivity render failed:', response.detail);
      showChartRenderError(
        [chart.key],
        'Chart rendering failed: backend is unreachable. Check that the backend is running.'
      );
      return;
    }

    console.warn(
      `[view-results] Directivity render returned ${response.status}: ${response.detail}`
    );
    if (response.kind === 'matplotlib-missing') {
      showMatplotlibRequiredForCharts([chart.key]);
    } else {
      showChartRenderError([chart.key], chartFailureMessage(response.detail, response.status));
    }
  }

  async function fetchCharts({ includeDirectivityMap = true } = {}) {
    const renderedChartKeys = chartNames
      .filter((chart) => chart.key !== 'directivity_map')
      .map((chart) => chart.key);

    // Show loading state
    for (const chart of chartNames) {
      if (!includeDirectivityMap && chart.key === 'directivity_map') {
        continue;
      }
      const container = document.getElementById(`vr-${chart.key}`);
      if (container) container.innerHTML = '<div class="view-results-loading">Rendering...</div>';
    }

    const payload = buildLineChartsPayload(results, {
      smoothing: panel.currentSmoothing,
      theme: getChartTheme(),
    });

    const chartsRenderPromise = (async () => {
      const response = await requestLineCharts(backendUrl, payload);
      if (response.ok) {
        for (const chart of chartNames) {
          if (chart.key === 'directivity_map') {
            continue;
          }
          setChartImage(chart.key, chart.label, response.charts[chart.key] || null);
        }
        return;
      }

      if (response.kind === 'network') {
        console.warn('[view-results] Fetch failed:', response.detail);
        showChartRenderError(
          renderedChartKeys,
          'Chart rendering failed: backend is unreachable. Check that the backend is running.'
        );
        return;
      }

      console.warn(`[view-results] Server returned ${response.status}: ${response.detail}`);
      if (response.kind === 'matplotlib-missing') {
        showMatplotlibRequiredForCharts(renderedChartKeys);
      } else {
        showChartRenderError(
          renderedChartKeys,
          chartFailureMessage(response.detail, response.status)
        );
      }
    })();
    const directivityRenderPromise = includeDirectivityMap
      ? renderDirectivityMap()
      : Promise.resolve();

    await Promise.all([chartsRenderPromise, directivityRenderPromise]);
  }

  // Re-fetch charts when smoothing changes
  smoothingSelect.addEventListener('change', (e) => {
    applySmoothingSelection(panel, e.target.value);
    fetchCharts({ includeDirectivityMap: false });
  });

  directivitySelect.addEventListener('change', (e) => {
    panel.currentDirectivityReferenceLevel = resolveDirectivityReferenceLevel(e.target.value);
    panel.app?.resultsDock?.markStaleAndRefresh();
    renderDirectivityMap();
  });

  fetchCharts();
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
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
