import validationManager from '../../validation/index.js';
import { applySmoothing } from '../../results/smoothing.js';
import {
  renderFrequencyResponseChart,
  renderDirectivityIndexChart,
  renderImpedanceChart,
  renderPolarDirectivityHeatmap
} from './charts.js';

export function displayResults(panel, results = null) {
  const resultsContainer = document.getElementById('results-container');
  const chartsDiv = document.getElementById('results-charts');

  resultsContainer.style.display = 'block';

  if (!results) {
    // Display mock results
    chartsDiv.innerHTML = `
                <div class="chart-container">
                    <div class="chart-title">Frequency Response (Mock Data)</div>
                    <p style="color: var(--text-color); opacity: 0.7; font-size: 0.85rem;">
                        Mock simulation complete. Connect to Python BEM backend for real results.
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
    // Display real BEM results
    chartsDiv.innerHTML = renderBemResults(panel, results);
  }

  // Enable export button
  document.getElementById('export-results-btn').disabled = false;
}

export function renderBemResults(panel, results) {
  const splData = results.spl_on_axis || {};
  const frequencies = splData.frequencies || [];
  let splValues = splData.spl || [];
  const diData = results.di || {};

  // Apply smoothing to SPL data
  if (panel.currentSmoothing !== 'none') {
    splValues = applySmoothing(frequencies, splValues, panel.currentSmoothing);
  }

  // Generate frequency response chart
  const freqChart = renderFrequencyResponseChart(frequencies, splValues);

  // Apply smoothing to directivity index
  let diValues = diData.di || [];
  const diFrequencies = diData.frequencies || frequencies;
  if (panel.currentSmoothing !== 'none') {
    diValues = applySmoothing(diFrequencies, diValues, panel.currentSmoothing);
  }

  // Generate directivity index chart
  const diChart = renderDirectivityIndexChart(diFrequencies, diValues);

  // Apply smoothing to impedance data
  const impedanceData = results.impedance || {};
  const impedanceFrequencies = impedanceData.frequencies || frequencies;
  let impedanceReal = impedanceData.real || [];
  let impedanceImag = impedanceData.imaginary || [];

  if (panel.currentSmoothing !== 'none') {
    impedanceReal = applySmoothing(impedanceFrequencies, impedanceReal, panel.currentSmoothing);
    impedanceImag = applySmoothing(impedanceFrequencies, impedanceImag, panel.currentSmoothing);
  }

  // Generate impedance chart
  const impedanceChart = renderImpedanceChart(impedanceFrequencies, impedanceReal, impedanceImag);

  // Generate polar directivity heatmap (like reference image)
  const directivityData = results.directivity || {};
  const polarHeatmap = renderPolarDirectivityHeatmap(frequencies, directivityData);

  // Run validation on results
  const validationReport = validationManager.runFullValidation(results);
  const validationHtml = renderValidationReport(validationReport);

  // Render backend metadata (removed per user request)
  const metadataHtml = '';

  // Smoothing indicator
  const smoothingLabel =
    panel.currentSmoothing !== 'none'
      ? ` <span style="color: #4CAF50; font-size: 0.85rem;">[${panel.currentSmoothing} smoothed]</span>`
      : '';

  return `
            <div class="chart-container">
                <div class="chart-title">Frequency Response (BEM)${smoothingLabel}</div>
                ${freqChart}
            </div>
            <div class="chart-container">
                <div class="chart-title">Directivity Index (BEM)${smoothingLabel}</div>
                ${diChart}
            </div>
            <div class="chart-container">
                <div class="chart-title">Acoustic Impedance (BEM)${smoothingLabel}</div>
                ${impedanceChart}
            </div>
            <div class="chart-container" style="width: 100%;">
                <div class="chart-title">Polar Directivity Map (ABEC.Polars)</div>
                ${polarHeatmap}
            </div>
            ${metadataHtml}
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
