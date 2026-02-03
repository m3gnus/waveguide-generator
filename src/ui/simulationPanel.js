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
import { applySmoothing } from '../processing/smoothing.js';

export class SimulationPanel {
    constructor() {
        this.solver = new BemSolver();
        this.currentJobId = null;
        this.pollInterval = null;
        this.pendingMeshResolve = null;
        this.lastResults = null;
        this.currentSmoothing = 'none';

        this.setupEventListeners();
        this.setupMeshListener();
        this.setupSmoothingListener();
        this.setupKeyboardShortcuts();
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

    setupSmoothingListener() {
        const smoothingSelect = document.getElementById('smoothing-select');
        if (smoothingSelect) {
            smoothingSelect.addEventListener('change', (e) => {
                this.currentSmoothing = e.target.value;
                if (this.lastResults) {
                    this.displayResults(this.lastResults);
                }
            });
        }
    }

    setupKeyboardShortcuts() {
        document.addEventListener('keydown', (e) => {
            // Check if Ctrl (or Cmd on Mac) + Shift is pressed
            const isModifier = (e.ctrlKey || e.metaKey) && e.shiftKey;

            if (isModifier) {
                let smoothingType = null;

                switch(e.key) {
                    case '1': smoothingType = '1/1'; break;
                    case '2': smoothingType = '1/2'; break;
                    case '3': smoothingType = '1/3'; break;
                    case '6': smoothingType = '1/6'; break;
                    case '7': smoothingType = '1/12'; break;
                    case '8': smoothingType = '1/24'; break;
                    case '9': smoothingType = '1/48'; break;
                    case 'X':
                    case 'x': smoothingType = 'variable'; break;
                    case 'Y':
                    case 'y': smoothingType = 'psychoacoustic'; break;
                    case 'Z':
                    case 'z': smoothingType = 'erb'; break;
                }

                if (smoothingType) {
                    e.preventDefault();

                    // Toggle: if already selected, remove smoothing
                    if (this.currentSmoothing === smoothingType) {
                        smoothingType = 'none';
                    }

                    this.currentSmoothing = smoothingType;
                    const smoothingSelect = document.getElementById('smoothing-select');
                    if (smoothingSelect) {
                        smoothingSelect.value = smoothingType;
                    }

                    if (this.lastResults) {
                        this.displayResults(this.lastResults);
                    }
                }
            }

            // Ctrl+0 to remove smoothing
            if ((e.ctrlKey || e.metaKey) && e.key === '0' && !e.shiftKey) {
                e.preventDefault();
                this.currentSmoothing = 'none';
                const smoothingSelect = document.getElementById('smoothing-select');
                if (smoothingSelect) {
                    smoothingSelect.value = 'none';
                }

                if (this.lastResults) {
                    this.displayResults(this.lastResults);
                }
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

        AppEvents.emit('ui:tab-changed', { tab: tabName });
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

        // Get polar directivity configuration
        const angleRangeStr = document.getElementById('polar-angle-range').value;
        const angleRangeParts = angleRangeStr.split(',').map(s => parseFloat(s.trim()));

        config.polarConfig = {
            angle_range: angleRangeParts.length === 3 ? angleRangeParts : [0, 180, 37],
            norm_angle: parseFloat(document.getElementById('polar-norm-angle').value),
            distance: parseFloat(document.getElementById('polar-distance').value),
            inclination: parseFloat(document.getElementById('polar-inclination').value)
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
        let splValues = splData.spl || [];
        const diData = results.di || {};

        // Apply smoothing to SPL data
        if (this.currentSmoothing !== 'none') {
            splValues = applySmoothing(frequencies, splValues, this.currentSmoothing);
        }

        // Generate frequency response chart
        const freqChart = this.renderFrequencyResponseChart(frequencies, splValues);

        // Apply smoothing to directivity index
        let diValues = diData.di || [];
        const diFrequencies = diData.frequencies || frequencies;
        if (this.currentSmoothing !== 'none') {
            diValues = applySmoothing(diFrequencies, diValues, this.currentSmoothing);
        }

        // Generate directivity index chart
        const diChart = this.renderDirectivityIndexChart(diFrequencies, diValues);

        // Apply smoothing to impedance data
        const impedanceData = results.impedance || {};
        const impedanceFrequencies = impedanceData.frequencies || frequencies;
        let impedanceReal = impedanceData.real || [];
        let impedanceImag = impedanceData.imaginary || [];

        if (this.currentSmoothing !== 'none') {
            impedanceReal = applySmoothing(impedanceFrequencies, impedanceReal, this.currentSmoothing);
            impedanceImag = applySmoothing(impedanceFrequencies, impedanceImag, this.currentSmoothing);
        }

        // Generate impedance chart
        const impedanceChart = this.renderImpedanceChart(
            impedanceFrequencies,
            impedanceReal,
            impedanceImag
        );

        // Generate polar directivity heatmap (like reference image)
        const directivityData = results.directivity || {};
        const polarHeatmap = this.renderPolarDirectivityHeatmap(
            frequencies,
            directivityData
        );

        // Run validation on results
        const validationReport = validationManager.runFullValidation(results);
        const validationHtml = this.renderValidationReport(validationReport);

        // Smoothing indicator
        const smoothingLabel = this.currentSmoothing !== 'none'
            ? ` <span style="color: #4CAF50; font-size: 0.85rem;">[${this.currentSmoothing} smoothed]</span>`
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
     * Render polar directivity heatmap (like ADAM Audio reference)
     * Shows SPL variation across frequency and angle as a 2D color map
     */
    renderPolarDirectivityHeatmap(frequencies, directivityData) {
        if (!frequencies.length || !directivityData.horizontal) {
            return '<p style="color: var(--text-color);">No directivity map data available</p>';
        }

        const width = 600;
        const height = 400;
        const padding = { left: 60, right: 80, top: 40, bottom: 50 };
        const chartWidth = width - padding.left - padding.right;
        const chartHeight = height - padding.top - padding.bottom;

        // Get horizontal directivity patterns (array of [angle, spl_db] pairs for each frequency)
        const patterns = directivityData.horizontal;
        if (!patterns || patterns.length === 0) {
            return '<p style="color: var(--text-color);">No polar directivity data available</p>';
        }

        // Extract angle range from first pattern
        const firstPattern = patterns[0];
        const angles = firstPattern.map(point => point[0]);
        const minAngle = Math.min(...angles);
        const maxAngle = Math.max(...angles);

        // Use log scale for frequency
        const minFreq = Math.min(...frequencies);
        const maxFreq = Math.max(...frequencies);
        const logMinFreq = Math.log10(minFreq);
        const logMaxFreq = Math.log10(maxFreq);

        // Color scale: red (3dB) -> orange -> yellow -> green -> cyan -> blue (-30dB)
        const getColor = (dbValue) => {
            // Normalize to 0-1 range (3dB to -30dB)
            const normalized = Math.max(0, Math.min(1, (3 - dbValue) / 33));

            // Color gradient: red -> orange -> yellow -> green -> cyan -> blue
            if (normalized < 0.16) {
                // Red to orange
                const t = normalized / 0.16;
                return `rgb(${255}, ${Math.round(69 + t * 96)}, 0)`;
            } else if (normalized < 0.33) {
                // Orange to yellow
                const t = (normalized - 0.16) / 0.17;
                return `rgb(${255}, ${Math.round(165 + t * 90)}, 0)`;
            } else if (normalized < 0.50) {
                // Yellow to green
                const t = (normalized - 0.33) / 0.17;
                return `rgb(${Math.round(255 - t * 80)}, 255, 0)`;
            } else if (normalized < 0.67) {
                // Green to cyan
                const t = (normalized - 0.50) / 0.17;
                return `rgb(0, 255, ${Math.round(t * 255)})`;
            } else if (normalized < 0.83) {
                // Cyan to blue
                const t = (normalized - 0.67) / 0.16;
                return `rgb(0, ${Math.round(255 - t * 255)}, 255)`;
            } else {
                // Blue to dark blue
                const t = (normalized - 0.83) / 0.17;
                return `rgb(0, 0, ${Math.round(255 - t * 155)})`;
            }
        };

        // Generate heatmap rectangles
        const rects = [];
        const numFreqBands = patterns.length;
        const numAngleBands = angles.length - 1;

        for (let fi = 0; fi < numFreqBands; fi++) {
            const pattern = patterns[fi];
            const freq = frequencies[Math.floor(fi * frequencies.length / numFreqBands)];
            const logFreq = Math.log10(freq);

            for (let ai = 0; ai < numAngleBands; ai++) {
                const angle1 = pattern[ai][0];
                const angle2 = pattern[ai + 1][0];
                const splDb = (pattern[ai][1] + pattern[ai + 1][1]) / 2;

                // Calculate rectangle position
                const x1 = padding.left + ((logFreq - logMinFreq) / (logMaxFreq - logMinFreq)) * chartWidth;
                const y1 = padding.top + ((angle1 - minAngle) / (maxAngle - minAngle)) * chartHeight;
                const x2 = fi < numFreqBands - 1
                    ? padding.left + ((Math.log10(frequencies[Math.floor((fi + 1) * frequencies.length / numFreqBands)]) - logMinFreq) / (logMaxFreq - logMinFreq)) * chartWidth
                    : width - padding.right;
                const y2 = padding.top + ((angle2 - minAngle) / (maxAngle - minAngle)) * chartHeight;

                const color = getColor(splDb);
                rects.push(`<rect x="${x1}" y="${y1}" width="${x2 - x1}" height="${y2 - y1}" fill="${color}" stroke="none"/>`);
            }
        }

        // Generate frequency tick marks (log scale)
        const freqTicks = [100, 200, 500, 1000, 2000, 5000, 10000, 20000];
        const freqTickMarks = freqTicks
            .filter(f => f >= minFreq && f <= maxFreq)
            .map(freq => {
                const logFreq = Math.log10(freq);
                const x = padding.left + ((logFreq - logMinFreq) / (logMaxFreq - logMinFreq)) * chartWidth;
                const label = freq >= 1000 ? `${freq / 1000}k` : freq;
                return `
                    <line x1="${x}" y1="${height - padding.bottom}" x2="${x}" y2="${height - padding.bottom + 5}" stroke="var(--text-color)" stroke-width="1"/>
                    <text x="${x}" y="${height - padding.bottom + 18}" text-anchor="middle" fill="var(--text-color)" font-size="10">${label}</text>
                `;
            }).join('');

        // Generate angle tick marks
        const angleTicks = [-180, -120, -60, 0, 60, 120, 180].filter(a => a >= minAngle && a <= maxAngle);
        const angleTickMarks = angleTicks.map(angle => {
            const y = padding.top + ((angle - minAngle) / (maxAngle - minAngle)) * chartHeight;
            return `
                <line x1="${padding.left - 5}" y1="${y}" x2="${padding.left}" y2="${y}" stroke="var(--text-color)" stroke-width="1"/>
                <text x="${padding.left - 10}" y="${y + 3}" text-anchor="end" fill="var(--text-color)" font-size="10">${angle}°</text>
            `;
        }).join('');

        // Generate color scale legend
        const legendX = width - padding.right + 20;
        const legendWidth = 20;
        const legendHeight = chartHeight;
        const legendSteps = 20;
        const legendRects = [];
        for (let i = 0; i < legendSteps; i++) {
            const dbValue = 3 - (i / legendSteps) * 33;
            const y = padding.top + (i / legendSteps) * legendHeight;
            const h = legendHeight / legendSteps;
            const color = getColor(dbValue);
            legendRects.push(`<rect x="${legendX}" y="${y}" width="${legendWidth}" height="${h}" fill="${color}" stroke="none"/>`);
        }

        // Legend labels
        const legendLabels = [3, 0, -6, -12, -18, -24, -30];
        const legendLabelMarks = legendLabels.map(db => {
            const normalized = (3 - db) / 33;
            const y = padding.top + normalized * legendHeight;
            return `
                <line x1="${legendX}" y1="${y}" x2="${legendX - 3}" y2="${y}" stroke="var(--text-color)" stroke-width="1"/>
                <text x="${legendX + legendWidth + 5}" y="${y + 3}" fill="var(--text-color)" font-size="9">${db}</text>
            `;
        }).join('');

        return `
            <svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet" style="background: #1a1a1a;">
                <!-- Heatmap -->
                ${rects.join('\n')}

                <!-- Border -->
                <rect x="${padding.left}" y="${padding.top}" width="${chartWidth}" height="${chartHeight}" fill="none" stroke="var(--border-color)" stroke-width="1"/>

                <!-- Axes -->
                ${freqTickMarks}
                ${angleTickMarks}

                <!-- Color scale legend -->
                ${legendRects.join('\n')}
                <rect x="${legendX}" y="${padding.top}" width="${legendWidth}" height="${legendHeight}" fill="none" stroke="var(--border-color)" stroke-width="1"/>
                ${legendLabelMarks}

                <!-- Axis labels -->
                <text x="${padding.left + chartWidth / 2}" y="${height - 5}" text-anchor="middle" fill="var(--text-color)" font-size="12" font-weight="600">Frequency [kHz]</text>
                <text x="15" y="${padding.top + chartHeight / 2}" text-anchor="middle" fill="var(--text-color)" font-size="12" font-weight="600" transform="rotate(-90, 15, ${padding.top + chartHeight / 2})">Angle [°]</text>
                <text x="${legendX + legendWidth + 35}" y="${padding.top + legendHeight / 2}" text-anchor="middle" fill="var(--text-color)" font-size="10" font-weight="600" transform="rotate(90, ${legendX + legendWidth + 35}, ${padding.top + legendHeight / 2})">dB rel 0°</text>

                <!-- Title -->
                <text x="${width / 2}" y="20" text-anchor="middle" fill="var(--text-color)" font-size="14" font-weight="600">Vertical Directivity</text>
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
        if (!this.lastResults) {
            alert('No simulation results available to export');
            return;
        }

        // Create export options dialog
        const exportType = prompt(
            'Export format:\n' +
            '1 - PNG image of all charts\n' +
            '2 - CSV data (frequency response)\n' +
            '3 - JSON data (all results)\n' +
            '4 - Text report\n\n' +
            'Enter number (1-4):',
            '1'
        );

        switch(exportType) {
            case '1':
                this.exportAsImage();
                break;
            case '2':
                this.exportAsCSV();
                break;
            case '3':
                this.exportAsJSON();
                break;
            case '4':
                this.exportAsText();
                break;
            default:
                if (exportType !== null) {
                    alert('Invalid selection. Please enter 1, 2, 3, or 4.');
                }
        }
    }

    /**
     * Export results as PNG image
     */
    exportAsImage() {
        const resultsCharts = document.getElementById('results-charts');
        if (!resultsCharts) {
            alert('No charts to export');
            return;
        }

        // Use html2canvas or similar library would be ideal, but for now use SVG export
        const svgs = resultsCharts.querySelectorAll('svg');
        if (svgs.length === 0) {
            alert('No charts available to export');
            return;
        }

        // Create a canvas to combine all SVGs
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');

        // Set canvas size (approximate)
        canvas.width = 1200;
        canvas.height = 400 * svgs.length;

        // White background
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        alert('PNG export requires html2canvas library. Exporting SVG data instead.');

        // Export first SVG as example
        const svgData = new XMLSerializer().serializeToString(svgs[0]);
        const blob = new Blob([svgData], { type: 'image/svg+xml' });
        const url = URL.createObjectURL(blob);

        const link = document.createElement('a');
        link.href = url;
        link.download = `bem_results_${Date.now()}.svg`;
        link.click();

        URL.revokeObjectURL(url);
    }

    /**
     * Export results as CSV
     */
    exportAsCSV() {
        const results = this.lastResults;
        const splData = results.spl_on_axis || {};
        const frequencies = splData.frequencies || [];
        const splValues = splData.spl || [];
        const diData = results.di || {};
        const impedanceData = results.impedance || {};

        // Apply current smoothing
        let smoothedSPL = splValues;
        let smoothedDI = diData.di || [];
        let smoothedImpReal = impedanceData.real || [];
        let smoothedImpImag = impedanceData.imaginary || [];

        if (this.currentSmoothing !== 'none') {
            smoothedSPL = applySmoothing(frequencies, splValues, this.currentSmoothing);
            smoothedDI = applySmoothing(frequencies, smoothedDI, this.currentSmoothing);
            smoothedImpReal = applySmoothing(frequencies, smoothedImpReal, this.currentSmoothing);
            smoothedImpImag = applySmoothing(frequencies, smoothedImpImag, this.currentSmoothing);
        }

        // Build CSV content
        let csv = 'Frequency (Hz),SPL (dB),DI (dB),Impedance Real (Ω),Impedance Imag (Ω)\n';

        for (let i = 0; i < frequencies.length; i++) {
            csv += `${frequencies[i]},${smoothedSPL[i] || ''},${smoothedDI[i] || ''},${smoothedImpReal[i] || ''},${smoothedImpImag[i] || ''}\n`;
        }

        // Add smoothing info as comment
        if (this.currentSmoothing !== 'none') {
            csv = `# Smoothing: ${this.currentSmoothing}\n` + csv;
        }

        // Download
        const blob = new Blob([csv], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `bem_results_${Date.now()}.csv`;
        link.click();
        URL.revokeObjectURL(url);
    }

    /**
     * Export results as JSON
     */
    exportAsJSON() {
        const exportData = {
            timestamp: new Date().toISOString(),
            smoothing: this.currentSmoothing,
            results: this.lastResults
        };

        const json = JSON.stringify(exportData, null, 2);
        const blob = new Blob([json], { type: 'application/json' });
        const url = URL.createObjectURL(blob);

        const link = document.createElement('a');
        link.href = url;
        link.download = `bem_results_${Date.now()}.json`;
        link.click();

        URL.revokeObjectURL(url);
    }

    /**
     * Export results as text report
     */
    exportAsText() {
        const results = this.lastResults;
        const splData = results.spl_on_axis || {};
        const frequencies = splData.frequencies || [];
        const splValues = splData.spl || [];
        const diData = results.di || {};
        const impedanceData = results.impedance || {};

        let report = 'BEM SIMULATION RESULTS\n';
        report += '=====================\n\n';
        report += `Generated: ${new Date().toISOString()}\n`;
        report += `Smoothing: ${this.currentSmoothing}\n`;
        report += `Frequency range: ${Math.min(...frequencies).toFixed(0)} - ${Math.max(...frequencies).toFixed(0)} Hz\n`;
        report += `Number of points: ${frequencies.length}\n\n`;

        // Summary statistics
        if (splValues.length > 0) {
            const avgSPL = splValues.reduce((a, b) => a + b, 0) / splValues.length;
            const minSPL = Math.min(...splValues);
            const maxSPL = Math.max(...splValues);

            report += 'FREQUENCY RESPONSE SUMMARY\n';
            report += '--------------------------\n';
            report += `Average SPL: ${avgSPL.toFixed(2)} dB\n`;
            report += `SPL Range: ${minSPL.toFixed(2)} to ${maxSPL.toFixed(2)} dB\n`;
            report += `Variation: ${(maxSPL - minSPL).toFixed(2)} dB\n\n`;
        }

        if (diData.di && diData.di.length > 0) {
            const avgDI = diData.di.reduce((a, b) => a + b, 0) / diData.di.length;
            const minDI = Math.min(...diData.di);
            const maxDI = Math.max(...diData.di);

            report += 'DIRECTIVITY INDEX SUMMARY\n';
            report += '-------------------------\n';
            report += `Average DI: ${avgDI.toFixed(2)} dB\n`;
            report += `DI Range: ${minDI.toFixed(2)} to ${maxDI.toFixed(2)} dB\n\n`;
        }

        if (impedanceData.real && impedanceData.real.length > 0) {
            const avgZ = impedanceData.real.reduce((a, b) => a + b, 0) / impedanceData.real.length;

            report += 'IMPEDANCE SUMMARY\n';
            report += '-----------------\n';
            report += `Average Real Part: ${avgZ.toFixed(2)} Ω\n\n`;
        }

        report += '\n\nDETAILED DATA\n';
        report += '=============\n\n';
        report += 'Freq(Hz)  SPL(dB)  DI(dB)  Z_Real(Ω)  Z_Imag(Ω)\n';
        report += '--------  -------  ------  ---------  ---------\n';

        for (let i = 0; i < Math.min(frequencies.length, 50); i++) {
            report += `${frequencies[i].toString().padEnd(8)}  `;
            report += `${(splValues[i] || 0).toFixed(2).padEnd(7)}  `;
            report += `${((diData.di && diData.di[i]) || 0).toFixed(2).padEnd(6)}  `;
            report += `${((impedanceData.real && impedanceData.real[i]) || 0).toFixed(2).padEnd(9)}  `;
            report += `${((impedanceData.imaginary && impedanceData.imaginary[i]) || 0).toFixed(2)}\n`;
        }

        if (frequencies.length > 50) {
            report += `\n... (${frequencies.length - 50} more rows) ...\n`;
        }

        // Download
        const blob = new Blob([report], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `bem_report_${Date.now()}.txt`;
        link.click();
        URL.revokeObjectURL(url);
    }
}
