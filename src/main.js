import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { STLExporter } from 'three/addons/exporters/STLExporter.js';

import {
    parseExpression,
    buildHornMesh
} from './geometry/index.js';

import { MWGConfigParser } from './config/index.js';
import { generateMWGConfigContent, exportProfilesCSV, exportGmshGeo } from './export/index.js';
import { saveFile } from './ui/fileOps.js';
import {
    createScene,
    createPerspectiveCamera,
    createOrthoCamera,
    ZebraShader
} from './viewer/index.js';

import { GlobalState } from './state.js';
import { ParamPanel } from './ui/paramPanel.js';
import { SimulationPanel } from './ui/simulationPanel.js';
import { AppEvents } from './events.js';

// Import change logging system
import ChangeLog, {
    setAgent,
    startSession,
    getLogs,
    getRecentLogs,
    getSummary,
    subscribe as subscribeToLogs,
    exportLogs,
    printLogs
} from './logging/index.js';

// Expose logging API globally for AI agents and debugging
window.ChangeLog = ChangeLog;
window.setAgent = setAgent;
window.startSession = startSession;
window.getLogs = getLogs;
window.getRecentLogs = getRecentLogs;
window.getLogSummary = getSummary;
window.exportLogs = exportLogs;
window.printLogs = printLogs;

class App {
    constructor() {
        this.container = document.getElementById('canvas-container');
        this.stats = document.getElementById('stats');
        this.renderRequested = false;

        // Initialize change logging
        this.initializeLogging();

        // Init UI
        this.paramPanel = new ParamPanel('param-container');
        this.simulationPanel = new SimulationPanel();

        this.setupScene();
        this.setupEventListeners();
        this.setupPanelSizing();

        // Initial Render
        this.onStateUpdate(GlobalState.get());

        // Subscribe to state updates
        AppEvents.on('state:updated', (state) => {
            this.onStateUpdate(state);
        });

        // Subscribe to simulation events
        AppEvents.on('simulation:mesh-requested', () => {
            this.provideMeshForSimulation();
        });

        AppEvents.on('ui:tab-changed', () => {
            this.schedulePanelAutoSize();
        });
    }

    /**
     * Initialize the change logging system
     */
    initializeLogging() {
        // Start a new session
        startSession('app-init');

        // Set initial agent to 'user' (default)
        setAgent('user', { source: 'app-init' });

        // Subscribe to log events for console output (optional - for debugging)
        subscribeToLogs((entry) => {
            // Skip system events for cleaner output
            if (entry.category === 'system') return;

            const time = entry.timestamp.toISOString().slice(11, 19);
            console.log(
                `%c[${time}] %c[${entry.agent}] %c${entry.event}`,
                'color: gray',
                'color: cyan',
                'color: white',
                entry.data
            );
        });

        console.log('%c[ChangeLog] Logging system initialized', 'color: green; font-weight: bold');
        console.log('%c[ChangeLog] Use window.printLogs() to view recent changes', 'color: green');
        console.log('%c[ChangeLog] Use window.setAgent("agent-name") to identify the current agent', 'color: green');
    }

    setupScene() {
        this.scene = createScene();

        this.cameraMode = 'perspective';
        const aspect = this.container.clientWidth / this.container.clientHeight;
        this.camera = createPerspectiveCamera(aspect);

        this.renderer = new THREE.WebGLRenderer({ antialias: true });
        this.renderer.setSize(this.container.clientWidth, this.container.clientHeight);
        this.renderer.setPixelRatio(window.devicePixelRatio);
        this.container.appendChild(this.renderer.domElement);

        this.controls = new OrbitControls(this.camera, this.renderer.domElement);
        this.controls.enableDamping = true;

        window.addEventListener('resize', () => this.onResize());

        this.animate();
    }

    onResize() {
        const width = this.container.clientWidth;
        const height = this.container.clientHeight;
        const aspect = width / height;

        if (this.cameraMode === 'perspective') {
            this.camera.aspect = aspect;
        } else {
            const size = this.getOrthoSize();
            this.camera.left = -size * aspect;
            this.camera.right = size * aspect;
            this.camera.top = size;
            this.camera.bottom = -size;
        }

        this.camera.updateProjectionMatrix();
        this.renderer.setSize(width, height);
    }

    setupEventListeners() {
        // Bind all button events using a helper method
        this.bindButtonEvents();

        // Undo/Redo keys
        document.addEventListener('keydown', (e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'z') {
                e.preventDefault();
                if (e.shiftKey) {
                    GlobalState.redo();
                } else {
                    GlobalState.undo();
                }
            }
        });
    }

    setupPanelSizing() {
        this.uiPanel = document.getElementById('ui-panel');
        this.uiPanelResizer = document.getElementById('ui-panel-resizer');
        if (!this.uiPanel || !this.uiPanelResizer) return;

        const rootStyles = getComputedStyle(document.documentElement);
        this.panelDefaultWidth = parseFloat(rootStyles.getPropertyValue('--panel-default-width')) || 350;
        this.panelMinWidth = parseFloat(rootStyles.getPropertyValue('--panel-min-width')) || 280;
        this.panelMaxWidth = parseFloat(rootStyles.getPropertyValue('--panel-max-width')) || 520;
        this.userResizedPanel = false;
        this.panelAutoSizeFrame = null;

        this.uiPanel.style.width = `${this.panelDefaultWidth}px`;

        this.uiPanelResizer.addEventListener('pointerdown', (event) => {
            event.preventDefault();
            this.isResizingPanel = true;
            this.userResizedPanel = true;
            this.panelResizeStartX = event.clientX;
            this.panelResizeStartWidth = this.uiPanel.getBoundingClientRect().width;
            this.uiPanelResizer.setPointerCapture(event.pointerId);
            document.body.style.cursor = 'col-resize';
        });

        this.uiPanelResizer.addEventListener('pointermove', (event) => {
            if (!this.isResizingPanel) return;
            const delta = event.clientX - this.panelResizeStartX;
            this.setPanelWidth(this.panelResizeStartWidth + delta);
        });

        const stopResize = (event) => {
            if (!this.isResizingPanel) return;
            this.isResizingPanel = false;
            if (event?.pointerId !== undefined) {
                this.uiPanelResizer.releasePointerCapture(event.pointerId);
            }
            document.body.style.cursor = '';
        };

        this.uiPanelResizer.addEventListener('pointerup', stopResize);
        this.uiPanelResizer.addEventListener('pointercancel', stopResize);

        this.uiPanel.addEventListener('input', () => this.schedulePanelAutoSize());
        this.uiPanel.addEventListener('toggle', () => this.schedulePanelAutoSize(), true);
        window.addEventListener('resize', () => this.schedulePanelAutoSize());

        this.schedulePanelAutoSize();
    }

    clampPanelWidth(width) {
        const max = Math.min(this.panelMaxWidth, window.innerWidth * 0.7);
        return Math.max(this.panelMinWidth, Math.min(max, width));
    }

    setPanelWidth(width) {
        if (!this.uiPanel) return;
        const clamped = this.clampPanelWidth(width);
        const current = this.uiPanel.getBoundingClientRect().width;
        if (Math.abs(clamped - current) < 1) return;
        this.uiPanel.style.width = `${clamped}px`;
        this.onResize();
    }

    schedulePanelAutoSize() {
        if (this.userResizedPanel || !this.uiPanel) return;
        if (this.panelAutoSizeFrame) {
            cancelAnimationFrame(this.panelAutoSizeFrame);
        }
        this.panelAutoSizeFrame = requestAnimationFrame(() => {
            this.panelAutoSizeFrame = null;
            this.autoSizePanel();
        });
    }

    autoSizePanel() {
        if (this.userResizedPanel || !this.uiPanel) return;
        const activeTab = this.uiPanel.querySelector('.tab-content.active');
        const contentWidth = Math.max(
            this.uiPanel.scrollWidth,
            activeTab ? activeTab.scrollWidth : 0
        );
        const target = this.clampPanelWidth(contentWidth);
        this.setPanelWidth(target);
    }

    /**
     * Bind event listeners to UI buttons
     * Checks for element existence before attaching listeners
     */
    bindButtonEvents() {
        const buttonBindings = [
            { id: 'render-btn', handler: () => this.requestRender(), type: 'click' },
            { id: 'export-btn', handler: () => this.exportSTL(), type: 'click' },
            { id: 'export-config-btn', handler: () => this.exportMWGConfig(), type: 'click' },
            { id: 'display-mode', handler: () => this.requestRender(), type: 'change' },
            { id: 'zoom-in', handler: () => this.zoom(0.8), type: 'click' },
            { id: 'zoom-out', handler: () => this.zoom(1.2), type: 'click' },
            { id: 'camera-toggle', handler: () => this.toggleCamera(), type: 'click' },
            {
                id: 'zoom-reset', handler: () => {
                    if (this.controls) this.controls.reset();
                }, type: 'click'
            },
            { id: 'focus-horn', handler: () => this.focusOnModel(), type: 'click' },
            { id: 'export-csv-btn', handler: () => this.exportProfileCSV(), type: 'click' },
            { id: 'export-geo-btn', handler: () => this.exportGmshGeo(), type: 'click' }
        ];

        buttonBindings.forEach(({ id, handler, type }) => {
            const element = document.getElementById(id);
            if (element) {
                element.addEventListener(type, handler);
                console.log(`Bound ${type} listener to ${id}`);
            } else {
                console.warn(`Element ${id} not found in DOM - ${type} listener not attached`);
            }
        });

        // Special handling for file upload
        const loadBtn = document.getElementById('load-config-btn');
        const fileInput = document.getElementById('config-upload');
        if (loadBtn && fileInput) {
            loadBtn.addEventListener('click', () => fileInput.click());
            fileInput.addEventListener('change', (e) => this.handleFileUpload(e));
            console.log('Bound file upload handlers');
        } else {
            if (!loadBtn) console.warn('Element load-config-btn not found');
            if (!fileInput) console.warn('Element config-upload not found');
        }
    }

    onStateUpdate(state) {
        // 1. Rebuild Param UI
        this.paramPanel.createFullPanel();
        this.schedulePanelAutoSize();

        // 2. Render
        if (document.getElementById('live-update')?.checked !== false) {
            this.requestRender();
        }
    }

    requestRender() {
        if (!this.renderRequested) {
            this.renderRequested = true;
            requestAnimationFrame(() => {
                this.renderModel();
                this.renderRequested = false;
            });
        }
    }

    toggleModelType(type) {
        // Legacy support function called by legacy listeners if any.
        this.requestRender();
    }

    handleFileUpload(event) {
        const file = event.target.files[0];
        if (!file) return;

        const reader = new FileReader();
        reader.onload = (e) => {
            const content = e.target.result;
            const parsed = MWGConfigParser.parse(content);
            if (parsed.type) {
                // Convert string values to proper types
                const typedParams = {};
                for (const [key, value] of Object.entries(parsed.params)) {
                    if (value === undefined || value === null) continue;

                    // Check if it's a number
                    const num = parseFloat(value);
                    if (!isNaN(num) && String(num) === String(value).trim()) {
                        typedParams[key] = num;
                    } else {
                        // Keep as string (expressions, etc.)
                        typedParams[key] = String(value);
                    }
                }

                GlobalState.update(typedParams, parsed.type);
            } else {
                alert('Could not find OSSE or R-OSSE block in config file.');
            }
        };
        reader.readAsText(file);
    }

    renderModel() {
        if (this.hornMesh) {
            this.scene.remove(this.hornMesh);
            this.hornMesh.geometry.dispose();
            this.hornMesh.material.dispose();
        }

        const state = GlobalState.get();
        const preparedParams = { ...state.params };

        // Evaluate expressions
        const type = state.type;

        for (const key of Object.keys(preparedParams)) {
            const val = preparedParams[key];
            if (typeof val === 'string' && (val.includes('sin') || val.includes('cos') || val.includes('p') || Number.isNaN(parseFloat(val)))) {
                preparedParams[key] = parseExpression(val);
            }
        }

        preparedParams.type = type;

        const { vertices, indices } = buildHornMesh(preparedParams);

        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
        geometry.setIndex(indices);
        geometry.computeVertexNormals();

        const displayMode = document.getElementById('display-mode')?.value || 'standard';
        let material;

        if (displayMode === 'zebra') {
            material = new THREE.ShaderMaterial({
                ...ZebraShader,
                side: THREE.DoubleSide
            });
        } else if (displayMode === 'curvature') {
            // Need segments for curvature
            const ang = preparedParams.angularSegments || 80;
            const len = preparedParams.lengthSegments || 20;
            const colors = this.calculateCurvatureColors(geometry, ang, len);
            geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
            material = new THREE.MeshPhongMaterial({ vertexColors: true, side: THREE.DoubleSide });
        } else {
            material = new THREE.MeshPhysicalMaterial({
                color: 0xcccccc,
                metalness: 0.5,
                roughness: 0.3,
                transmission: 0,
                transparent: true,
                opacity: 0.9,
                side: THREE.DoubleSide,
                wireframe: displayMode === 'grid'
            });
        }

        this.hornMesh = new THREE.Mesh(geometry, material);
        this.scene.add(this.hornMesh);

        this.stats.innerText = `Vertices: ${vertices.length / 3} | Triangles: ${indices.length / 3}`;
    }

    calculateCurvatureColors(geometry, radialSteps, lengthSteps) {
        const normals = geometry.attributes.normal.array;
        const count = normals.length / 3;
        const colors = new Float32Array(count * 3);

        for (let j = 0; j <= lengthSteps; j++) {
            for (let i = 0; i <= radialSteps; i++) {
                const idx = (j * (radialSteps + 1) + i) * 3;
                let curvature = 0;
                const neighbors = [[j - 1, i], [j + 1, i], [j, i - 1], [j, i + 1]];
                const nx = normals[idx];
                const ny = normals[idx + 1];
                const nz = normals[idx + 2];
                let sampleCount = 0;
                neighbors.forEach(([nj, ni]) => {
                    if (nj >= 0 && nj <= lengthSteps && ni >= 0 && ni <= radialSteps) {
                        const nIdx = (nj * (radialSteps + 1) + ni) * 3;
                        const d = 1.0 - (nx * normals[nIdx] + ny * normals[nIdx + 1] + nz * normals[nIdx + 2]);
                        curvature += d;
                        sampleCount++;
                    }
                });
                const c = Math.min(1.0, (curvature / sampleCount) * 50.0);
                colors[idx] = c;
                colors[idx + 1] = 1 - c;
                colors[idx + 2] = 0.5;
            }
        }
        return colors;
    }

    focusOnModel() {
        if (!this.hornMesh) return;
        this.hornMesh.geometry.computeBoundingBox();
        const box = this.hornMesh.geometry.boundingBox;
        const center = new THREE.Vector3();
        box.getCenter(center);
        this.controls.target.copy(center);
        this.controls.update();
    }

    zoom(factor) {
        if (this.cameraMode === 'perspective') {
            this.camera.position.multiplyScalar(factor);
        } else {
            this.camera.zoom /= factor;
            this.camera.updateProjectionMatrix();
        }
        this.controls.update();
    }

    getOrthoSize() {
        return 300;
    }

    toggleCamera() {
        const width = this.container.clientWidth;
        const height = this.container.clientHeight;
        const aspect = width / height;
        const pos = this.camera.position.clone();
        const target = this.controls.target.clone();

        if (this.cameraMode === 'perspective') {
            const size = this.getOrthoSize();
            this.camera = createOrthoCamera(aspect, size);
            this.cameraMode = 'orthographic';
            document.getElementById('camera-toggle').innerText = '▲';
        } else {
            this.camera = createPerspectiveCamera(aspect);
            this.cameraMode = 'perspective';
            document.getElementById('camera-toggle').innerText = '⬚';
        }

        this.camera.position.copy(pos);
        this.scene.add(this.camera);

        const oldControls = this.controls;
        this.controls = new OrbitControls(this.camera, this.renderer.domElement);
        this.controls.target.copy(target);
        this.controls.enableDamping = true;
        this.controls.update();
        oldControls.dispose();
    }

    exportSTL() {
        if (!this.hornMesh) return;
        const exporter = new STLExporter();
        const result = exporter.parse(this.hornMesh, { binary: true });

        saveFile(result, 'horn.stl', {
            extension: '.stl',
            contentType: 'application/sla',
            typeInfo: { description: 'STL Model', accept: { 'model/stl': ['.stl'] } }
        });
    }

    exportMWGConfig() {
        const state = GlobalState.get();
        const exportParams = { type: state.type, ...state.params };
        const content = generateMWGConfigContent(exportParams);
        saveFile(content, 'config.txt', {
            extension: '.txt',
            contentType: 'text/plain',
            typeInfo: { description: 'MWG Config', accept: { 'text/plain': ['.txt'] } }
        });
    }

    exportProfileCSV() {
        if (!this.hornMesh) {
            alert('Please generate a horn model first');
            return;
        }

        const vertices = this.hornMesh.geometry.attributes.position.array;
        const state = GlobalState.get();
        const csv = exportProfilesCSV(vertices, state.params);

        saveFile(csv, 'profiles.csv', {
            extension: '.csv',
            contentType: 'text/csv',
            typeInfo: { description: 'Profile Coordinates', accept: { 'text/csv': ['.csv'] } }
        });
    }

    exportGmshGeo() {
        if (!this.hornMesh) {
            alert('Please generate a horn model first');
            return;
        }

        const vertices = this.hornMesh.geometry.attributes.position.array;
        const state = GlobalState.get();
        const geo = exportGmshGeo(vertices, state.params);

        saveFile(geo, 'mesh.geo', {
            extension: '.geo',
            contentType: 'text/plain',
            typeInfo: { description: 'Gmsh Geometry', accept: { 'text/plain': ['.geo'] } }
        });
    }

    provideMeshForSimulation() {
        if (!this.hornMesh) {
            console.warn('No mesh available for simulation');
            AppEvents.emit('simulation:mesh-ready', null);
            return null;
        }

        // Provide mesh data to simulation panel
        const geometry = this.hornMesh.geometry;
        const vertices = geometry.attributes.position.array;

        // Check if geometry has an index buffer
        if (!geometry.index) {
            console.error('[Simulation] Geometry has no index buffer - mesh may be non-indexed');
            AppEvents.emit('simulation:mesh-ready', null);
            return null;
        }

        const indices = geometry.index.array;
        const state = GlobalState.get();

        // Validate mesh data before sending
        const vertexCount = vertices.length / 3;
        const maxIndex = Math.max(...indices);
        if (maxIndex >= vertexCount) {
            console.error(`[Simulation] Invalid mesh: max index ${maxIndex} >= vertex count ${vertexCount}`);
            console.error('[Simulation] This indicates the mesh was corrupted during Three.js processing');
            AppEvents.emit('simulation:mesh-ready', null);
            return null;
        }

        console.log(`[Simulation] Mesh validated: ${vertexCount} vertices, ${indices.length / 3} triangles`);

        AppEvents.emit('simulation:mesh-ready', {
            vertices: Array.from(vertices),
            indices: Array.from(indices),
            vertexCount: vertexCount,
            triangleCount: indices.length / 3,
            params: state.params,
            type: state.type
        });
    }

    animate() {
        requestAnimationFrame(() => this.animate());
        this.controls.update();
        this.renderer.render(this.scene, this.camera);
    }
}

new App();
