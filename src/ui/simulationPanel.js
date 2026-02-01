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

export class SimulationPanel {
    constructor() {
        this.solver = new BemSolver();
        this.currentJobId = null;
        this.pollInterval = null;

        this.setupEventListeners();
        this.checkSolverConnection();
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
        // Get the current horn mesh from the scene
        // This would be exported from the main app
        AppEvents.emit('simulation:mesh-requested');

        // For now, return a placeholder
        return {
            vertices: [],
            indices: [],
            format: 'stl'
        };
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
            // Display real results
            chartsDiv.innerHTML = '<p>Real BEM results would be displayed here</p>';
            // TODO: Implement real chart rendering
        }

        // Enable export button
        document.getElementById('export-results-btn').disabled = false;
    }

    exportResults() {
        // TODO: Implement results export
        alert('Results export functionality coming soon');
    }
}
