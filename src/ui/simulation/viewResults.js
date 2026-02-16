import { applySmoothing } from '../../results/smoothing.js';
import {
  renderFrequencyResponseChart,
  renderDirectivityIndexChart,
  renderImpedanceChart,
  renderPolarDirectivityHeatmap,
} from './charts.js';

/**
 * Open a modal dialog displaying all result charts rendered server-side
 * by Matplotlib as high-quality PNG images.
 */
export async function openViewResultsModal(panel) {
  if (!panel.lastResults) return;

  const results = panel.lastResults;
  const splData = results.spl_on_axis || {};
  const frequencies = splData.frequencies || [];
  let spl = splData.spl || [];
  const diData = results.di || {};
  let di = diData.di || [];
  const diFrequencies = diData.frequencies || frequencies;
  const impedanceData = results.impedance || {};
  const impedanceFrequencies = impedanceData.frequencies || frequencies;
  let impedanceReal = impedanceData.real || [];
  let impedanceImag = impedanceData.imaginary || [];
  const directivity = results.directivity || {};

  // Apply current smoothing
  if (panel.currentSmoothing !== 'none') {
    spl = applySmoothing(frequencies, spl, panel.currentSmoothing);
    di = applySmoothing(diFrequencies, di, panel.currentSmoothing);
    impedanceReal = applySmoothing(impedanceFrequencies, impedanceReal, panel.currentSmoothing);
    impedanceImag = applySmoothing(impedanceFrequencies, impedanceImag, panel.currentSmoothing);
  }

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
  const smoothingLabel = panel.currentSmoothing !== 'none'
    ? ` [${panel.currentSmoothing} smoothed]`
    : '';
  title.textContent = `Simulation Results${smoothingLabel}`;
  header.appendChild(title);

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'view-results-close';
  closeBtn.textContent = '\u00d7';
  closeBtn.title = 'Close (Escape)';
  header.appendChild(closeBtn);

  dialog.appendChild(header);

  const body = document.createElement('div');
  body.className = 'view-results-body';

  // Chart containers with loading placeholders
  const chartNames = [
    { key: 'frequency_response', label: 'Frequency Response (SPL On-Axis)' },
    { key: 'directivity_index', label: 'Directivity Index' },
    { key: 'impedance', label: 'Acoustic Impedance' },
    { key: 'directivity_map', label: 'Polar Directivity Map' },
  ];

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

  // Close handlers
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    window.removeEventListener('keydown', onKeyDown);
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

  // Fetch charts from backend
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
    const response = await fetch('http://localhost:8000/api/render-charts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const detail = await response.text().catch(() => '');
      console.warn(`[view-results] Server returned ${response.status}: ${detail}`);
      _renderClientFallback();
      return;
    }

    const data = await response.json();
    const charts = data.charts || {};

    for (const chart of chartNames) {
      const container = document.getElementById(`vr-${chart.key}`);
      if (!container) continue;

      const imgData = charts[chart.key];
      if (imgData) {
        container.innerHTML = `<img src="${imgData}" alt="${chart.label}" style="width: 100%; border-radius: 4px;" />`;
      } else {
        container.innerHTML = '<div class="view-results-loading">No data available</div>';
      }
    }
  } catch (err) {
    console.warn('[view-results] Fetch failed:', err.message);
    _renderClientFallback();
  }

  function _renderClientFallback() {
    const fallbackCharts = {
      frequency_response: renderFrequencyResponseChart(frequencies, spl),
      directivity_index: renderDirectivityIndexChart(diFrequencies, di),
      impedance: renderImpedanceChart(impedanceFrequencies, impedanceReal, impedanceImag),
      directivity_map: renderPolarDirectivityHeatmap(frequencies, directivity),
    };

    for (const chart of chartNames) {
      const container = document.getElementById(`vr-${chart.key}`);
      if (!container) continue;
      container.innerHTML = fallbackCharts[chart.key] || '<div class="view-results-loading">No data available</div>';
    }
  }
}
