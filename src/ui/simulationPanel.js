/**
 * Simulation Panel UI Module
 * 
 * Handles the BEM simulation interface including:
 * - Connection status monitoring
 * - Simulation controls
 * - Progress tracking
 * - Results display coordination
 */

import { BemSolver } from '../solver/index.js';
import { AppEvents } from '../events.js';
import { generateBemMesh } from '../solver/bemMeshGenerator.js';
import validationManager from '../validation/index.js';

export class SimulationPanel {
    constructor() {
        this.solver = new BemSolver();
        this.currentJobId = null;
        this.pollInterval = null;
        this.pendingMeshResolve = null;
        this.lastResults = null;

        this.setupEventListeners();
        this.setupMeshListener();
        this.checkSolverConnection();
    }

    setupMeshListener() {
        // Listen for mesh data from main app
        AppEvents.on('simulation:mesh-ready', (meshData) => {
            if (this.pendingMeshResolve) {
                this.pendingMeshResolve(meshData);
                this.pendingMeshResolve = null;
            }
        });
    }

    setupEventListeners() {
        // Tab switching
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const tab = e.target.dataset.tab;
                this.switchTab(tab);
            });
        });

        // Run simulation button
        const runBtn = document.getElementById('run-simulation-btn');
        if (runBtn) {
            runBtn.addEventListener('click', () => this.runSimulation());
        }

        // Export results button
        const exportBtn = document.getElementById('export-results-btn');
        if (exportBtn) {
            exportBtn.addEventListener('click', () => this.exportResults());
        }
    }

    switchTab(tabName) {
        // Update tab buttons
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.tab === tabName);
        });

        // Update tab content
        document.querySelectorAll('.tab-content').forEach(content => {
            content.classList.toggle('active', content.id === `${tabName}-tab`);
        });
    }

    async checkSolverConnection() {
        const statusDot = document.getElementById('solver-status');
        const statusText = document.getElementById('solver-status-text');

        try {
            const isConnected = await this.solver.checkConnection();

            if (isConnected) {
                statusDot.className = 'status-dot connected';
                statusText.textContent = 'Connected to BEM solver';
                document.getElementById('run-simulation-btn').disabled = false;
            } else {
                statusDot.className = 'status-dot disconnected';
                statusText.textContent = 'BEM solver not available';
                document.getElementById('run-simulation-btn').disabled = true;
            }
        } catch (error) {
            statusDot.className = 'status-dot disconnected';
            statusText.textContent = 'BEM solver not available (using mock data)';
            // Don't disable button - allow mock simulation
            document.getElementById('run-simulation-btn').disabled = false;
        }

        // Check again in 10 seconds
        setTimeout(() => this.checkSolverConnection(), 10000);
    }

    async runSimulation() {
        const runBtn = document.getElementById('run-simulation-btn');
        const progressDiv = document.getElementById('simulation-progress');
        const progressFill = document.getElementById('progress-fill');
        const progressText = document.getElementById('progress-text');
        const resultsContainer = document.getElementById('results-container');

        // Get simulation settings
        const config = {
            frequencyStart: parseInt(document.getElementById('freq-start').value),
            frequencyEnd: parseInt(document.getElementById('freq-end').value),
            numFrequencies: parseInt(document.getElementById('freq-steps').value),
            simulationType: document.getElementById('sim-type').value
        };

        // Validate settings
        if (config.frequencyStart >= config.frequencyEnd) {
            alert('Start frequency must be less than end frequency');
            return;
        }

        // Show progress
        runBtn.disabled = true;
        progressDiv.style.display = 'block';
        resultsContainer.style.display = 'none';
        progressFill.style.width = '0%';
        progressText.textContent = 'Preparing mesh...';

        try {
            // Get current mesh data
            const meshData = await this.prepareMeshForSimulation();

            progressFill.style.width = '20%';
            progressText.textContent = 'Submitting to BEM solver...';

            // Check if real solver is available
            const isConnected = await this.solver.checkConnection();

            if (isConnected) {
                // Submit to real solver
                this.currentJobId = await this.solver.submitSimulation(config, meshData);

                progressFill.style.width = '30%';
                progressText.textContent = 'Simulation running...';

                // Poll for results
                this.pollSimulationStatus();
            } else {
                // Use mock solver for demonstration
                progressFill.style.width = '50%';
                progressText.textContent = 'Running mock simulation...';

                await this.runMockSimulation(config);

                progressFill.style.width = '100%';
                progressText.textContent = 'Complete!';

                setTimeout(() => {
                    progressDiv.style.display = 'none';
                    this.displayResults();
                    runBtn.disabled = false;
                }, 1000);
            }
        } catch (error) {
            console.error('Simulation error:', error);
            progressText.textContent = `Error: ${error.message}`;
            runBtn.disabled = false;

            setTimeout(() => {
                progressDiv.style.display = 'none';
            }, 3000);
        }
    }

    async prepareMeshForSimulation() {
        // Request mesh from main app and wait for response
        return new Promise((resolve, reject) => {
            // Set up timeout
            const timeout = setTimeout(() => {
                this.pendingMeshResolve = null;
                reject(new Error('Timeout waiting for mesh data'));
            }, 5000);

            // Store resolve function to be called when mesh arrives
            this.pendingMeshResolve = (meshData) => {
                clearTimeout(timeout);

                if (!meshData || !meshData.vertices || meshData.vertices.length === 0) {
                    reject(new Error('No horn geometry available. Please generate a horn first.'));
                    return;
                }

                // Generate BEM-ready mesh with throat surface and boundary tags
                try {
                    const bemMesh = generateBemMesh(meshData);
                    resolve(bemMesh);
                } catch (error) {
                    console.error('[Simulation] BEM mesh generation failed:', error);
                    reject(new Error(`Mesh preparation failed: ${error.message}`));
                }
            };

            // Request mesh from main app
            AppEvents.emit('simulation:mesh-requested');
        });
    }

    async runMockSimulation(config) {
        // Simulate processing time
        return new Promise(resolve => {
            setTimeout(resolve, 2000);
        });
    }

    pollSimulationStatus() {
        const progressFill = document.getElementById('progress-fill');
        const progressText = document.getElementById('progress-text');
        const runBtn = document.getElementById('run-simulation-btn');
        const progressDiv = document.getElementById('simulation-progress');

        this.pollInterval = setInterval(async () => {
            try {
                const status = await this.solver.getJobStatus(this.currentJobId);

                if (status.status === 'running') {
                    const progress = Math.min(95, 30 + (status.progress * 65));
                    progressFill.style.width = `${progress}%`;
                    progressText.textContent = `Simulating... ${Math.round(status.progress * 100)}%`;
                } else if (status.status === 'complete') {
                    clearInterval(this.pollInterval);
                    progressFill.style.width = '100%';
                    progressText.textContent = 'Complete!';

                    // Fetch and display results
                    const results = await this.solver.getResults(this.currentJobId);
                    this.lastResults = results;
                    this.displayResults(results);

                    setTimeout(() => {
                        progressDiv.style.display = 'none';
                        runBtn.disabled = false;
                    }, 1000);
                } else if (status.status === 'error') {
                    clearInterval(this.pollInterval);
                    progressText.textContent = `Error: ${status.message || 'Simulation failed'}`;
                    runBtn.disabled = false;
                }
            } catch (error) {
                clearInterval(this.pollInterval);
                console.error('Status polling error:', error);
                progressText.textContent = 'Error checking status';
                runBtn.disabled = false;
            }
        }, 1000);
    }

    displayResults(results = null) {
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
            chartsDiv.innerHTML = this.renderBemResults(results);
        }

        // Enable export button
        document.getElementById('export-results-btn').disabled = false;
    }

    /**
     * Render BEM simulation results as SVG charts
     */
    renderBemResults(results) {
        const splData = results.spl_on_axis || {};
        const frequencies = splData.frequencies || [];
        const splValues = splData.spl || [];
        const diData = results.di || {};

        // Generate frequency response chart
        const freqChart = this.renderFrequencyResponseChart(frequencies, splValues);

        // Generate directivity index chart
        const diChart = this.renderDirectivityIndexChart(
            diData.frequencies || frequencies,
            diData.di || []
        );

        // Generate impedance chart
        const impedanceData = results.impedance || {};
        const impedanceChart = this.renderImpedanceChart(
            impedanceData.frequencies || frequencies,
            impedanceData.real || [],
            impedanceData.imaginary || []
        );

        // Run validation on results
        const validationReport = validationManager.runFullValidation(results);
        const validationHtml = this.renderValidationReport(validationReport);

        return `
            <div class="chart-container">
                <div class="chart-title">Frequency Response (BEM)</div>
                ${freqChart}
            </div>
            <div class="chart-container">
                <div class="chart-title">Directivity Index (BEM)</div>
                ${diChart}
            </div>
            <div class="chart-container">
                <div class="chart-title">Acoustic Impedance (BEM)</div>
                ${impedanceChart}
            </div>
            ${validationHtml}
        `;
    }

    /**
     * Render frequency response SVG chart
     */
    renderFrequencyResponseChart(frequencies, splValues) {
        if (!frequencies.length || !splValues.length) {
            return '<p style="color: var(--text-color);">No frequency response data available</p>';
        }

        const width = 300;
        const height = 180;
        const padding = { left: 45, right: 20, top: 20, bottom: 30 };
        const chartWidth = width - padding.left - padding.right;
        const chartHeight = height - padding.top - padding.bottom;

        // Calculate scales
        const minFreq = Math.min(...frequencies);
        const maxFreq = Math.max(...frequencies);
        const minSpl = Math.min(...splValues) - 5;
        const maxSpl = Math.max(...splValues) + 5;

        // Use log scale for frequency
        const logMinFreq = Math.log10(minFreq);
        const logMaxFreq = Math.log10(maxFreq);

        // Generate path points
        const points = frequencies.map((freq, i) => {
            const logFreq = Math.log10(freq);
            const x = padding.left + ((logFreq - logMinFreq) / (logMaxFreq - logMinFreq)) * chartWidth;
            const y = padding.top + (1 - (splValues[i] - minSpl) / (maxSpl - minSpl)) * chartHeight;
            return `${x.toFixed(1)},${y.toFixed(1)}`;
        }).join(' ');

        // Generate grid lines
        const gridLines = [];
        const freqTicks = [100, 1000, 10000];
        freqTicks.forEach(freq => {
            if (freq >= minFreq && freq <= maxFreq) {
                const logFreq = Math.log10(freq);
                const x = padding.left + ((logFreq - logMinFreq) / (logMaxFreq - logMinFreq)) * chartWidth;
                gridLines.push(`<line x1="${x}" y1="${padding.top}" x2="${x}" y2="${height - padding.bottom}" stroke="var(--border-color)" stroke-width="0.5" stroke-dasharray="2,2"/>`);
                gridLines.push(`<text x="${x}" y="${height - 10}" text-anchor="middle" fill="var(--text-color)" font-size="10">${freq >= 1000 ? (freq/1000) + 'k' : freq}</text>`);
            }
        });

        return `
            <svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet">
                <!-- Axes -->
                <line x1="${padding.left}" y1="${height - padding.bottom}" x2="${width - padding.right}" y2="${height - padding.bottom}" stroke="var(--border-color)" stroke-width="1"/>
                <line x1="${padding.left}" y1="${padding.top}" x2="${padding.left}" y2="${height - padding.bottom}" stroke="var(--border-color)" stroke-width="1"/>

                <!-- Grid lines -->
                ${gridLines.join('\n')}

                <!-- Y-axis labels -->
                <text x="10" y="${padding.top + 5}" fill="var(--text-color)" font-size="9">${maxSpl.toFixed(0)}</text>
                <text x="10" y="${height - padding.bottom}" fill="var(--text-color)" font-size="9">${minSpl.toFixed(0)}</text>

                <!-- Data line -->
                <polyline points="${points}" fill="none" stroke="var(--accent-color)" stroke-width="2"/>

                <!-- Axis labels -->
                <text x="${width/2}" y="${height - 2}" text-anchor="middle" fill="var(--text-color)" font-size="10">Frequency (Hz)</text>
                <text x="8" y="${height/2}" text-anchor="middle" fill="var(--text-color)" font-size="10" transform="rotate(-90, 8, ${height/2})">SPL (dB)</text>
            </svg>
        `;
    }

    /**
     * Render directivity index SVG chart
     */
    renderDirectivityIndexChart(frequencies, diValues) {
        if (!frequencies.length || !diValues.length) {
            return '<p style="color: var(--text-color);">No directivity data available</p>';
        }

        const width = 300;
        const height = 180;
        const padding = { left: 45, right: 20, top: 20, bottom: 30 };
        const chartWidth = width - padding.left - padding.right;
        const chartHeight = height - padding.top - padding.bottom;

        const minFreq = Math.min(...frequencies);
        const maxFreq = Math.max(...frequencies);
        const minDi = Math.min(0, Math.min(...diValues) - 2);
        const maxDi = Math.max(...diValues) + 2;

        const logMinFreq = Math.log10(minFreq);
        const logMaxFreq = Math.log10(maxFreq);

        const points = frequencies.map((freq, i) => {
            const logFreq = Math.log10(freq);
            const x = padding.left + ((logFreq - logMinFreq) / (logMaxFreq - logMinFreq)) * chartWidth;
            const y = padding.top + (1 - (diValues[i] - minDi) / (maxDi - minDi)) * chartHeight;
            return `${x.toFixed(1)},${y.toFixed(1)}`;
        }).join(' ');

        return `
            <svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet">
                <line x1="${padding.left}" y1="${height - padding.bottom}" x2="${width - padding.right}" y2="${height - padding.bottom}" stroke="var(--border-color)" stroke-width="1"/>
                <line x1="${padding.left}" y1="${padding.top}" x2="${padding.left}" y2="${height - padding.bottom}" stroke="var(--border-color)" stroke-width="1"/>

                <text x="10" y="${padding.top + 5}" fill="var(--text-color)" font-size="9">${maxDi.toFixed(0)}</text>
                <text x="10" y="${height - padding.bottom}" fill="var(--text-color)" font-size="9">${minDi.toFixed(0)}</text>

                <polyline points="${points}" fill="none" stroke="#4CAF50" stroke-width="2"/>

                <text x="${width/2}" y="${height - 2}" text-anchor="middle" fill="var(--text-color)" font-size="10">Frequency (Hz)</text>
                <text x="8" y="${height/2}" text-anchor="middle" fill="var(--text-color)" font-size="10" transform="rotate(-90, 8, ${height/2})">DI (dB)</text>
            </svg>
        `;
    }

    /**
     * Render impedance SVG chart
     */
    renderImpedanceChart(frequencies, realValues, imagValues) {
        if (!frequencies.length || !realValues.length) {
            return '<p style="color: var(--text-color);">No impedance data available</p>';
        }

        const width = 300;
        const height = 180;
        const padding = { left: 45, right: 20, top: 20, bottom: 30 };
        const chartWidth = width - padding.left - padding.right;
        const chartHeight = height - padding.top - padding.bottom;

        const minFreq = Math.min(...frequencies);
        const maxFreq = Math.max(...frequencies);
        const allValues = [...realValues, ...imagValues];
        const minZ = Math.min(...allValues) - 50;
        const maxZ = Math.max(...allValues) + 50;

        const logMinFreq = Math.log10(minFreq);
        const logMaxFreq = Math.log10(maxFreq);

        const realPoints = frequencies.map((freq, i) => {
            const logFreq = Math.log10(freq);
            const x = padding.left + ((logFreq - logMinFreq) / (logMaxFreq - logMinFreq)) * chartWidth;
            const y = padding.top + (1 - (realValues[i] - minZ) / (maxZ - minZ)) * chartHeight;
            return `${x.toFixed(1)},${y.toFixed(1)}`;
        }).join(' ');

        const imagPoints = frequencies.map((freq, i) => {
            const logFreq = Math.log10(freq);
            const x = padding.left + ((logFreq - logMinFreq) / (logMaxFreq - logMinFreq)) * chartWidth;
            const y = padding.top + (1 - (imagValues[i] - minZ) / (maxZ - minZ)) * chartHeight;
            return `${x.toFixed(1)},${y.toFixed(1)}`;
        }).join(' ');

        return `
            <svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet">
                <line x1="${padding.left}" y1="${height - padding.bottom}" x2="${width - padding.right}" y2="${height - padding.bottom}" stroke="var(--border-color)" stroke-width="1"/>
                <line x1="${padding.left}" y1="${padding.top}" x2="${padding.left}" y2="${height - padding.bottom}" stroke="var(--border-color)" stroke-width="1"/>

                <text x="10" y="${padding.top + 5}" fill="var(--text-color)" font-size="9">${maxZ.toFixed(0)}</text>
                <text x="10" y="${height - padding.bottom}" fill="var(--text-color)" font-size="9">${minZ.toFixed(0)}</text>

                <!-- Real part (blue) -->
                <polyline points="${realPoints}" fill="none" stroke="#2196F3" stroke-width="2"/>
                <!-- Imaginary part (orange) -->
                <polyline points="${imagPoints}" fill="none" stroke="#FF9800" stroke-width="2"/>

                <!-- Legend -->
                <line x1="${width - 80}" y1="12" x2="${width - 65}" y2="12" stroke="#2196F3" stroke-width="2"/>
                <text x="${width - 62}" y="15" fill="var(--text-color)" font-size="8">Re(Z)</text>
                <line x1="${width - 80}" y1="24" x2="${width - 65}" y2="24" stroke="#FF9800" stroke-width="2"/>
                <text x="${width - 62}" y="27" fill="var(--text-color)" font-size="8">Im(Z)</text>

                <text x="${width/2}" y="${height - 2}" text-anchor="middle" fill="var(--text-color)" font-size="10">Frequency (Hz)</text>
                <text x="8" y="${height/2}" text-anchor="middle" fill="var(--text-color)" font-size="10" transform="rotate(-90, 8, ${height/2})">Z (Ω)</text>
            </svg>
        `;
    }

    /**
     * Render validation report as HTML
     */
    renderValidationReport(report) {
        const statusColor = report.overallPassed ? '#4CAF50' : '#f44336';
        const statusIcon = report.overallPassed ? '✓' : '✗';
        const statusText = report.overallPassed ? 'PASSED' : 'ISSUES FOUND';

        let checksHtml = '';

        for (const [sectionName, section] of Object.entries(report.sections)) {
            if (!section.checks) continue;

            const sectionIcon = section.passed ? '✓' : (section.severity === 'error' ? '✗' : '⚠');
            const sectionColor = section.passed ? '#4CAF50' : (section.severity === 'error' ? '#f44336' : '#ff9800');

            checksHtml += `
                <div style="margin-bottom: 12px;">
                    <div style="font-weight: 600; color: ${sectionColor}; margin-bottom: 4px;">
                        ${sectionIcon} ${sectionName.replace(/([A-Z])/g, ' $1').trim()}
                    </div>
                    <div style="font-size: 0.8rem; opacity: 0.9; margin-left: 16px;">
                        ${section.checks.map(check => {
                            const icon = check.passed ? '✓' : (check.severity === 'error' ? '✗' : '⚠');
                            const color = check.passed ? '#4CAF50' : (check.severity === 'error' ? '#f44336' : '#ff9800');
                            return `<div style="color: ${color}; margin: 2px 0;">${icon} ${check.message}</div>`;
                        }).join('')}
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

    exportResults() {
        // TODO: Implement results export
        alert('Results export functionality coming soon');
    }
}
