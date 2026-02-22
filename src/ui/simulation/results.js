import validationManager from '../../validation/index.js';
import { applySmoothing } from '../../results/smoothing.js';

export function displayResults(panel, results = null) {
  const resultsContainer = document.getElementById('results-container');
  const chartsDiv = document.getElementById('results-charts');

  resultsContainer.classList.remove('is-hidden');
  resultsContainer.style.display = 'block';

  if (!results) {
    // Display fallback mock results when backend BEM data is unavailable
    chartsDiv.innerHTML = `
                <div class="chart-container">
                    <div class="chart-title">Frequency Response (Mock Data)</div>
                    <p style="color: var(--text-color); opacity: 0.7; font-size: 0.85rem;">
                        Fallback preview only. Start the backend solver to run full BEM results.
                    </p>
                    <svg width="100%" height="200" style="margin-top: 10px;">
                        <line x1="10%" y1="90%" x2="90%" y2="90%" stroke="var(--border-color)" stroke-width="2"/>
                        <line x1="10%" y1="10%" x2="10%" y2="90%" stroke="var(--border-color)" stroke-width="2"/>
                        <polyline points="10,180 50,160 100,170 150,150 200,165 250,140 280,150"
                                  fill="none" stroke="var(--accent-color)" stroke-width="2"/>
                        <text x="50%" y="195" text-anchor="middle" fill="var(--text-color)" font-size="12">
                            Frequency (Hz)
                        </text>
                        <text x="5" y="100" text-anchor="middle" fill="var(--text-color)" font-size="12"
                              transform="rotate(-90, 5, 100)">
                            SPL (dB)
                        </text>
                    </svg>
                </div>
                <div class="chart-container">
                    <div class="chart-title">Directivity Pattern (Mock Data)</div>
                    <svg width="100%" height="200" style="margin-top: 10px;">
                        <circle cx="50%" cy="50%" r="80" fill="none" stroke="var(--border-color)" stroke-width="1"/>
                        <circle cx="50%" cy="50%" r="60" fill="none" stroke="var(--border-color)" stroke-width="1"/>
                        <circle cx="50%" cy="50%" r="40" fill="none" stroke="var(--border-color)" stroke-width="1"/>
                        <line x1="50%" y1="10%" x2="50%" y2="90%" stroke="var(--border-color)" stroke-width="1"/>
                        <line x1="10%" y1="50%" x2="90%" y2="50%" stroke="var(--border-color)" stroke-width="1"/>
                        <path d="M 150,100 Q 170,80 180,100 Q 170,120 150,100"
                              fill="var(--accent-color)" opacity="0.3" stroke="var(--accent-color)" stroke-width="2"/>
                    </svg>
                </div>
            `;
  } else {
    // Display real BEM results with Matplotlib-rendered charts
    chartsDiv.innerHTML = renderBemResults(panel, results);

    // Fetch all charts from Matplotlib backend (async)
    // Must run AFTER innerHTML assignment so containers exist in the DOM.
    const splData = results.spl_on_axis || {};
    const freqs = results.frequencies || splData.frequencies || [];
    const directivity = results.directivity || {};
    const refSelect = document.getElementById('directivity-ref-level');
    const refLevel = refSelect ? parseFloat(refSelect.value) : -6;
    _fetchDirectivityPlot(freqs, directivity, refLevel);
    _fetchInlineCharts(panel, results);

    // Re-render when reference level changes
    if (refSelect) {
      refSelect.addEventListener('change', () => {
        _fetchDirectivityPlot(freqs, directivity, parseFloat(refSelect.value));
      });
    }
  }

  // Enable results buttons
  document.getElementById('export-results-btn').disabled = false;
  const viewBtn = document.getElementById('view-results-btn');
  if (viewBtn) viewBtn.disabled = false;
}

export function renderBemResults(panel, results) {
  // Surface partial-failure information from solver metadata
  const metadata = results.metadata || {};
  const failureCount = metadata.failure_count || 0;
  const totalFreqs = (results.frequencies || []).length;
  let failureBanner = '';
  if (failureCount > 0 && totalFreqs > 0) {
    const successCount = totalFreqs - failureCount;
    const failures = (metadata.failures || []).slice(0, 3);
    const failureDetails = failures.map(f =>
      `${f.frequency_hz ? f.frequency_hz.toFixed(0) + ' Hz: ' : ''}${f.detail || f.code || 'unknown'}`
    ).join('<br>');
    const color = successCount === 0 ? '#f44336' : '#ff9800';
    const icon = successCount === 0 ? 'ERROR' : 'WARNING';
    failureBanner = `
      <div class="chart-container" style="border-left: 3px solid ${color}; margin-bottom: 12px;">
        <div class="chart-title" style="color: ${color};">${icon}: ${failureCount} of ${totalFreqs} frequencies failed</div>
        <div style="color: var(--text-color); font-size: 0.8rem; opacity: 0.9; padding: 4px 0;">
          ${failureDetails || 'Check backend logs for details.'}
        </div>
      </div>`;
  }

  // Run validation on results
  const validationReport = validationManager.runFullValidation(results);
  const validationHtml = renderValidationReport(validationReport);

  // Smoothing indicator
  const smoothingLabel =
    panel.currentSmoothing !== 'none'
      ? ` <span style="color: #4CAF50; font-size: 0.85rem;">[${panel.currentSmoothing} smoothed]</span>`
      : '';

  const loadingPlaceholder = '<div style="text-align: center; padding: 20px; color: var(--text-color); opacity: 0.7; font-size: 0.85rem;">Rendering...</div>';

  return `
            ${failureBanner}
            <div class="chart-container" style="width: 100%;">
                <div class="chart-title" style="display: flex; align-items: center; gap: 12px;">
                    Polar Directivity Map (ABEC.Polars)
                    <span style="font-size: 0.8rem; font-weight: normal; color: var(--text-color); opacity: 0.8; display: flex; align-items: center; gap: 4px; margin-left: auto;">
                        Reference dB level
                        <select id="directivity-ref-level" style="background: var(--input-bg, #2a2a2a); color: var(--text-color); border: 1px solid var(--border-color); border-radius: 3px; padding: 2px 4px; font-size: 0.8rem;">
                            <option value="-3">-3 dB</option>
                            <option value="-6" selected>-6 dB</option>
                            <option value="-9">-9 dB</option>
                            <option value="-12">-12 dB</option>
                        </select>
                    </span>
                </div>
                <div id="directivity-plot-container">
                    ${loadingPlaceholder}
                </div>
            </div>
            <div class="chart-container">
                <div class="chart-title">Acoustic Impedance (BEM)${smoothingLabel}</div>
                <div id="inline-chart-impedance">${loadingPlaceholder}</div>
            </div>
            <div class="chart-container">
                <div class="chart-title">Directivity Index (BEM)${smoothingLabel}</div>
                <div id="inline-chart-directivity_index">${loadingPlaceholder}</div>
            </div>
            <div class="chart-container">
                <div class="chart-title">Frequency Response (BEM)${smoothingLabel}</div>
                <div id="inline-chart-frequency_response">${loadingPlaceholder}</div>
            </div>
            ${validationHtml}
        `;
}

export function renderValidationReport(report) {
  const statusColor = report.overallPassed ? '#4CAF50' : '#f44336';
  const statusIcon = report.overallPassed ? '✓' : '✗';
  const statusText = report.overallPassed ? 'PASSED' : 'ISSUES FOUND';

  let checksHtml = '';

  for (const [sectionName, section] of Object.entries(report.sections)) {
    if (!section.checks) continue;

    const sectionIcon = section.passed ? '✓' : section.severity === 'error' ? '✗' : '⚠';
    const sectionColor = section.passed ? '#4CAF50' : section.severity === 'error' ? '#f44336' : '#ff9800';

    checksHtml += `
                <div style="margin-bottom: 12px;">
                    <div style="font-weight: 600; color: ${sectionColor}; margin-bottom: 4px;">
                        ${sectionIcon} ${sectionName.replace(/([A-Z])/g, ' $1').trim()}
                    </div>
                    <div style="font-size: 0.8rem; opacity: 0.9; margin-left: 16px;">
                        ${section.checks
        .map((check) => {
          const icon = check.passed ? '✓' : check.severity === 'error' ? '✗' : '⚠';
          const color = check.passed ? '#4CAF50' : check.severity === 'error' ? '#f44336' : '#ff9800';
          return `<div style="color: ${color}; margin: 2px 0;">${icon} ${check.message}</div>`;
        })
        .join('')}
                    </div>
                </div>
            `;
  }

  // Add diagnostics summary
  const diag = report.sections.physicalBehavior?.diagnostics || {};
  let diagHtml = '';
  if (diag.splStats) {
    diagHtml = `
                <div style="margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border-color); font-size: 0.75rem; opacity: 0.8;">
                    <strong>Diagnostics:</strong>
                    <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 4px; margin-top: 4px;">
                        <div>SPL: ${diag.splStats.min?.toFixed(1)} - ${diag.splStats.max?.toFixed(1)} dB</div>
                        <div>DI: ${diag.diStats?.min?.toFixed(1) || 'N/A'} - ${diag.diStats?.max?.toFixed(1) || 'N/A'} dB</div>
                        <div>Freq: ${diag.frequencyRange?.min?.toFixed(0)} - ${diag.frequencyRange?.max?.toFixed(0)} Hz</div>
                        <div>Points: ${diag.frequencyRange?.points || 0}</div>
                    </div>
                </div>
            `;
  }

  return `
            <div class="chart-container" style="background: var(--panel-bg);">
                <div class="chart-title" style="display: flex; align-items: center; gap: 8px;">
                    <span style="color: ${statusColor}; font-size: 1.2rem;">${statusIcon}</span>
                    Validation Report
                    <span style="color: ${statusColor}; font-size: 0.85rem; font-weight: normal;">(${statusText})</span>
                </div>
                <div style="color: var(--text-color); font-size: 0.85rem; padding: 8px 0;">
                    ${checksHtml}
                    ${diagHtml}
                </div>
            </div>
        `;
}

export function renderBackendMetadata(metadata) {
  if (!metadata) return '';

  let sectionsHtml = '';
  let hasWarnings = false;

  // Mesh Validation Section
  if (metadata.validation) {
    const val = metadata.validation;
    const warnings = val.warnings || [];
    const recommendations = val.recommendations || [];
    hasWarnings = warnings.length > 0;

    const statusIcon = warnings.length === 0 ? '✓' : '⚠';
    const statusColor = warnings.length === 0 ? '#4CAF50' : '#ff9800';

    let warningsHtml = '';
    if (warnings.length > 0) {
      warningsHtml = warnings
        .map((w) => `<div style="color: #ff9800; margin: 4px 0;">⚠ ${w}</div>`)
        .join('');
    }

    let recommendationsHtml = '';
    if (recommendations.length > 0) {
      recommendationsHtml = `
        <div style="margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--border-color);">
          <div style="font-weight: 600; color: #2196F3; margin-bottom: 4px;">💡 Recommendations:</div>
          ${recommendations
          .map((r) => {
            // Highlight different recommendation types
            const isRecommended = r.includes('RECOMMENDED');
            const isAlternative = r.includes('ALTERNATIVE');
            const canProceed = r.includes('proceed') || r.includes('safe to proceed');
            const color = isRecommended
              ? '#4CAF50'
              : isAlternative
                ? '#2196F3'
                : canProceed
                  ? '#4CAF50'
                  : 'var(--text-color)';
            const prefix = isRecommended ? '✓' : isAlternative ? '→' : canProceed ? '✓' : '•';
            return `<div style="color: ${color}; margin: 4px 0;">${prefix} ${r.replace(/^(RECOMMENDED|ALTERNATIVE):\s*/, '')}</div>`;
          })
          .join('')}
        </div>
      `;
    }

    sectionsHtml += `
      <div style="margin-bottom: 12px;">
        <div style="font-weight: 600; color: ${statusColor}; margin-bottom: 4px;">
          ${statusIcon} Mesh Resolution
        </div>
        <div style="font-size: 0.8rem; opacity: 0.9; margin-left: 16px;">
          <div style="color: var(--text-color);">Max valid frequency: ${val.max_valid_frequency?.toFixed(0) || 'N/A'} Hz</div>
          <div style="color: var(--text-color);">Recommended max: ${val.recommended_max_frequency?.toFixed(0) || 'N/A'} Hz</div>
          <div style="color: var(--text-color);">Elements/wavelength: ${val.elements_per_wavelength?.toFixed(1) || 'N/A'}</div>
          ${warningsHtml}
          ${recommendationsHtml}
        </div>
      </div>
    `;
  }

  // Symmetry Section
  if (metadata.symmetry && metadata.symmetry.reduction_factor > 1.0) {
    const sym = metadata.symmetry;
    sectionsHtml += `
      <div style="margin-bottom: 12px;">
        <div style="font-weight: 600; color: #4CAF50; margin-bottom: 4px;">
          ✓ Symmetry Optimization
        </div>
        <div style="font-size: 0.8rem; opacity: 0.9; margin-left: 16px;">
          <div style="color: var(--text-color);">Type: ${sym.symmetry_type?.replace('_', ' ') || 'N/A'}</div>
          <div style="color: var(--text-color);">Speedup: ${sym.reduction_factor?.toFixed(1) || 'N/A'}×</div>
          ${sym.reduced_triangles ? `<div style="color: var(--text-color);">Elements: ${sym.original_triangles} → ${sym.reduced_triangles}</div>` : ''}
        </div>
      </div>
    `;
  }

  // Performance Section
  if (metadata.performance) {
    const perf = metadata.performance;
    sectionsHtml += `
      <div style="margin-bottom: 12px;">
        <div style="font-weight: 600; color: #2196F3; margin-bottom: 4px;">
          ⓘ Performance
        </div>
        <div style="font-size: 0.8rem; opacity: 0.9; margin-left: 16px;">
          <div style="color: var(--text-color);">Total time: ${perf.total_time_seconds?.toFixed(1) || 'N/A'}s</div>
          <div style="color: var(--text-color);">Time per frequency: ${perf.time_per_frequency?.toFixed(2) || 'N/A'}s</div>
          ${perf.directivity_compute_time ? `<div style="color: var(--text-color);">Directivity compute: ${perf.directivity_compute_time.toFixed(1)}s</div>` : ''}
        </div>
      </div>
    `;
  }

  if (!sectionsHtml) return '';

  const titleColor = hasWarnings ? '#ff9800' : '#4CAF50';
  const titleIcon = hasWarnings ? '⚠' : 'ⓘ';
  const titleText = hasWarnings ? 'WARNINGS' : 'INFO';

  return `
    <div class="chart-container" style="background: var(--panel-bg); margin-top: 16px;">
      <div class="chart-title" style="display: flex; align-items: center; gap: 8px;">
        <span style="color: ${titleColor}; font-size: 1.2rem;">${titleIcon}</span>
        Simulation Metadata
        <span style="color: ${titleColor}; font-size: 0.85rem; font-weight: normal;">(${titleText})</span>
      </div>
      <div style="color: var(--text-color); font-size: 0.85rem; padding: 8px 0;">
        ${sectionsHtml}
      </div>
    </div>
  `;
}

function _setPlotMessage(container, msg) {
  container.innerHTML = `<div style="text-align: center; padding: 20px; color: var(--text-color); opacity: 0.7; font-size: 0.85rem;">${msg}</div>`;
}

function _matplotlibRequiredHtml() {
  return `<div style="text-align: center; padding: 32px; color: var(--text-color);">
    <div style="font-weight: 600; margin-bottom: 8px;">Matplotlib is required for chart rendering</div>
    <div style="opacity: 0.8; font-size: 0.8rem;">Install it with: <code style="background: var(--input-bg); padding: 2px 6px; border-radius: 4px;">pip install matplotlib</code></div>
    <div style="opacity: 0.6; font-size: 0.75rem; margin-top: 6px;">Then restart the backend server.</div>
  </div>`;
}

async function _fetchInlineCharts(panel, results) {
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

  if (panel.currentSmoothing !== 'none') {
    spl = applySmoothing(frequencies, spl, panel.currentSmoothing);
    di = applySmoothing(diFrequencies, di, panel.currentSmoothing);
    impedanceReal = applySmoothing(impedanceFrequencies, impedanceReal, panel.currentSmoothing);
    impedanceImag = applySmoothing(impedanceFrequencies, impedanceImag, panel.currentSmoothing);
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

  const containerIds = {
    frequency_response: 'inline-chart-frequency_response',
    directivity_index: 'inline-chart-directivity_index',
    impedance: 'inline-chart-impedance',
  };

  try {
    const response = await fetch('http://localhost:8000/api/render-charts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      for (const id of Object.values(containerIds)) {
        const el = document.getElementById(id);
        if (el) el.innerHTML = _matplotlibRequiredHtml();
      }
      return;
    }

    const data = await response.json();
    const charts = data.charts || {};

    for (const [key, containerId] of Object.entries(containerIds)) {
      const el = document.getElementById(containerId);
      if (!el) continue;
      const imgData = charts[key];
      if (imgData) {
        el.innerHTML = `<img src="${imgData}" alt="${key}" style="width: 100%; border-radius: 4px;" />`;
      } else {
        el.innerHTML = '<div style="text-align: center; padding: 20px; color: var(--text-color); opacity: 0.7; font-size: 0.85rem;">No data available</div>';
      }
    }
  } catch (err) {
    console.warn('[inline-charts] Fetch failed:', err.message);
    for (const id of Object.values(containerIds)) {
      const el = document.getElementById(id);
      if (el) el.innerHTML = _matplotlibRequiredHtml();
    }
  }
}

async function _fetchDirectivityPlot(frequencies, directivity, referenceLevel = -6) {
  const container = document.getElementById('directivity-plot-container');
  if (!container) {
    console.warn('[directivity-plot] #directivity-plot-container not found in DOM');
    return;
  }
  const hasPlaneData = ['horizontal', 'vertical', 'diagonal']
    .some((plane) => Array.isArray(directivity?.[plane]) && directivity[plane].length > 0);
  if (!frequencies?.length || !hasPlaneData) {
    console.warn('[directivity-plot] Missing frequencies or directivity plane data');
    _setPlotMessage(container, 'No directivity data available.');
    return;
  }

  try {
    const response = await fetch('http://localhost:8000/api/render-directivity', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ frequencies, directivity, reference_level: referenceLevel }),
    });

    if (!response.ok) {
      const detail = await response.text().catch(() => '');
      console.warn(`[directivity-plot] Server returned ${response.status}: ${detail}`);
      _setPlotMessage(container, 'Directivity plot unavailable (server error).');
      return;
    }

    const data = await response.json();
    if (data.image) {
      container.innerHTML = `<img src="${data.image}" alt="Directivity Plot" style="width: 100%; border-radius: 4px;" />`;
    } else {
      console.warn('[directivity-plot] Response missing image field');
      _setPlotMessage(container, 'Directivity plot unavailable (empty response).');
    }
  } catch (err) {
    console.warn('[directivity-plot] Fetch failed (server unavailable):', err.message);
    container.innerHTML = `<div style="text-align: center; padding: 32px; color: var(--text-color);">
      <div style="font-weight: 600; margin-bottom: 8px;">Matplotlib is required for directivity rendering</div>
      <div style="opacity: 0.8; font-size: 0.8rem;">Install it with: <code style="background: var(--input-bg); padding: 2px 6px; border-radius: 4px;">pip install matplotlib</code></div>
      <div style="opacity: 0.6; font-size: 0.75rem; margin-top: 6px;">Then restart the backend server.</div>
    </div>`;
  }
}
