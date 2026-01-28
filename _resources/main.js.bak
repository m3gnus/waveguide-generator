import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { STLExporter } from 'three/addons/exporters/STLExporter.js';

// --- MATH UTILS ---
function parseExpression(expr) {
    // Basic substitution for common JS math functions
    let jsExpr = expr
        .replace(/abs\(/g, 'Math.abs(')
        .replace(/cos\(/g, 'Math.cos(')
        .replace(/sin\(/g, 'Math.sin(')
        .replace(/tan\(/g, 'Math.tan(')
        .replace(/sqrt\(/g, 'Math.sqrt(')
        .replace(/\^/g, '**');

    // Create a function that takes 'p' (angle) and returns the value
    try {
        return new Function('p', `return ${jsExpr};`);
    } catch (e) {
        console.error("Invalid expression:", expr, e);
        return () => 0;
    }
}

// --- R-OSSE CORE ---
function calculateROSSE(t, p, params) {
    const R = params.R(p);
    const a = (params.a(p) * Math.PI) / 180;
    const a0 = (params.a0 * Math.PI) / 180;
    const k = params.k;
    const m = params.m || 0.85;
    const r0 = params.r0;
    const b = params.b ? params.b(p) : 0.2;
    const r = params.r || 0.4;
    const q = params.q;

    // Auxiliary constants calculated for each angle p
    const c1 = (k * r0) ** 2;
    const c2 = 2 * k * r0 * Math.tan(a0);
    const c3 = Math.tan(a) ** 2;

    // Calculate L based on the mouth radius R(p)
    // Formula: L = (1/(2c3)) * [sqrt(c2^2 - 4c3(c1 - (R + r0(k-1))^2)) - c2]
    const termInsideSqrt = c2 ** 2 - 4 * c3 * (c1 - Math.pow(R + r0 * (k - 1), 2));
    const L = (1 / (2 * c3)) * (Math.sqrt(Math.max(0, termInsideSqrt)) - c2);

    const xt = L * (Math.sqrt(r ** 2 + m ** 2) - Math.sqrt(r ** 2 + (t - m) ** 2)) +
        b * L * (Math.sqrt(r ** 2 + (1 - m) ** 2) - Math.sqrt(r ** 2 + m ** 2)) * (t ** 2);

    const yt = (1 - Math.pow(t, q)) * (Math.sqrt(c1 + c2 * L * t + c3 * (L * t) ** 2) + r0 * (1 - k)) +
        Math.pow(t, q) * (R + L * (1 - Math.sqrt(1 + c3 * (t - 1) ** 2)));

    return { x: xt, y: yt };
}

// --- OS-SE CORE ---
function calculateOSSE(z, p, params) {
    const L = params.L;
    const a = (params.a(p) * Math.PI) / 180;
    const a0 = (params.a0 * Math.PI) / 180;
    const r0 = params.r0;
    const k = params.k;
    const s = params.s(p);
    const n = params.n;
    const q = params.q;

    const rGOS = Math.sqrt((k * r0) ** 2 + 2 * k * r0 * z * Math.tan(a0) + (z ** 2) * (Math.tan(a) ** 2)) + r0 * (1 - k);

    let rTERM = 0;
    if (z > 0 && n > 0 && q > 0) {
        const val = q * z / L;
        if (val <= 1.0) {
            rTERM = (s * L / q) * (1 - Math.pow(1 - Math.pow(val, n), 1 / n));
        } else {
            rTERM = (s * L / q);
        }
    }

    return { x: z, y: rGOS + rTERM };
}

// --- CONFIG PARSER ---
class ATHConfigParser {
    static parse(content) {
        const result = { type: null, params: {}, blocks: {} };
        const lines = content.split('\n').map(line => {
            const commentIdx = line.indexOf(';');
            return (commentIdx !== -1 ? line.substring(0, commentIdx) : line).trim();
        }).filter(line => line.length > 0);

        let currentBlock = null;
        let currentBlockName = null;

        for (const line of lines) {
            // Block start: "Name = {" or "Name:Sub = {"
            const blockStartMatch = line.match(/^([\w.:-]+)\s*=\s*\{/);
            if (blockStartMatch) {
                currentBlockName = blockStartMatch[1];
                if (currentBlockName === 'R-OSSE') {
                    result.type = 'R-OSSE';
                    currentBlock = 'R-OSSE';
                } else if (currentBlockName === 'OSSE') {
                    result.type = 'OSSE';
                    currentBlock = 'OSSE';
                } else {
                    currentBlock = currentBlockName;
                    result.blocks[currentBlockName] = {};
                }
                continue;
            }

            // Block end
            if (line === '}') {
                currentBlock = null;
                currentBlockName = null;
                continue;
            }

            // Key = Value (split on first = only, to handle expressions with =)
            const eqIdx = line.indexOf('=');
            if (eqIdx > 0) {
                const key = line.substring(0, eqIdx).trim();
                const value = line.substring(eqIdx + 1).trim();

                if (currentBlock === 'R-OSSE' || currentBlock === 'OSSE') {
                    result.params[key] = value;
                } else if (currentBlock && result.blocks[currentBlock]) {
                    result.blocks[currentBlock][key] = value;
                } else {
                    // Flat top-level key — detect OSSE by known flat keys
                    result.params[key] = value;
                }
            }
        }

        // Auto-detect OSSE from flat-key format (no OSSE = { } block)
        if (!result.type) {
            if (result.params['Coverage.Angle'] || result.params['Length'] || result.params['Term.n']) {
                result.type = 'OSSE';
            }
        }

        // Normalize OSSE flat-key names to internal parameter names
        if (result.type === 'OSSE' && !result.params.a) {
            const p = result.params;
            // Map flat ATH keys to the internal names the UI uses
            if (p['Coverage.Angle']) { p.a = p['Coverage.Angle']; }
            if (p['Throat.Angle']) { p.a0 = p['Throat.Angle']; }
            if (p['Throat.Diameter']) { p.r0 = String(parseFloat(p['Throat.Diameter']) / 2); }
            if (p['Length']) { p.L = p['Length']; }
            if (p['Term.s']) { p.s = p['Term.s']; }
            if (p['Term.n']) { p.n = p['Term.n']; }
            if (p['Term.q']) { p.q = p['Term.q']; }
            if (p['OS.h']) { p.h = p['OS.h']; }
            if (p['OS.k']) { p.k = p['OS.k']; }

            // Morph
            if (p['Morph.TargetShape']) { p.morphTarget = p['Morph.TargetShape']; }
            if (p['Morph.TargetWidth']) { p.morphWidth = p['Morph.TargetWidth']; }
            if (p['Morph.TargetHeight']) { p.morphHeight = p['Morph.TargetHeight']; }
            if (p['Morph.CornerRadius']) { p.morphCorner = p['Morph.CornerRadius']; }
            if (p['Morph.Rate']) { p.morphRate = p['Morph.Rate']; }
            if (p['Morph.FixedPart']) { p.morphFixed = p['Morph.FixedPart']; }

            // Mesh
            if (p['Mesh.AngularSegments']) { p.angularSegments = p['Mesh.AngularSegments']; }
            if (p['Mesh.LengthSegments']) { p.lengthSegments = p['Mesh.LengthSegments']; }
            if (p['Mesh.CornerSegments']) { p.cornerSegments = p['Mesh.CornerSegments']; }
            if (p['Mesh.Quadrants']) { p.quadrants = p['Mesh.Quadrants']; }
            if (p['Mesh.WallThickness']) { p.wallThickness = p['Mesh.WallThickness']; }
            if (p['Mesh.RearShape']) { p.RearShape = p['Mesh.RearShape']; }

            // Source & ABEC
            if (p['Source.Shape']) { p.sourceShape = p['Source.Shape']; }
            if (p['Source.Radius']) { p.sourceRadius = p['Source.Radius']; }
            if (p['Source.Velocity']) { p.sourceVelocity = p['Source.Velocity']; }
            if (p['ABEC.SimType']) { p.abecSimType = p['ABEC.SimType']; }
            if (p['ABEC.f1']) { p.abecF1 = p['ABEC.f1']; }
            if (p['ABEC.f2']) { p.abecF2 = p['ABEC.f2']; }
            if (p['ABEC.NumFrequencies']) { p.abecNumFreq = p['ABEC.NumFrequencies']; }
        }

        // Normalize R-OSSE mesh/source/abec params too
        if (result.type === 'R-OSSE') {
            const p = result.params;
            if (p['Mesh.AngularSegments']) { p.angularSegments = p['Mesh.AngularSegments']; }
            if (p['Mesh.LengthSegments']) { p.lengthSegments = p['Mesh.LengthSegments']; }
            if (p['Mesh.WallThickness']) { p.wallThickness = p['Mesh.WallThickness']; }
            if (p['Mesh.Quadrants']) { p.quadrants = p['Mesh.Quadrants']; }
            if (p['Mesh.RearShape']) { p.RearShape = p['Mesh.RearShape']; }
            if (p['ABEC.SimType']) { p.abecSimType = p['ABEC.SimType']; }
            if (p['ABEC.f1']) { p.abecF1 = p['ABEC.f1']; }
            if (p['ABEC.f2']) { p.abecF2 = p['ABEC.f2']; }
            if (p['ABEC.NumFrequencies']) { p.abecNumFreq = p['ABEC.NumFrequencies']; }
        }

        // Parse Mesh.Enclosure block if present
        const encBlock = result.blocks['Mesh.Enclosure'];
        if (encBlock) {
            const p = result.params;
            if (encBlock.Depth) { p.encDepth = encBlock.Depth; }
            if (encBlock.EdgeRadius) { p.encEdge = encBlock.EdgeRadius; }
            if (encBlock.EdgeType) { p.encEdgeType = encBlock.EdgeType; }
            if (encBlock.Spacing) {
                const parts = encBlock.Spacing.split(',').map(s => s.trim());
                if (parts.length >= 4) {
                    p.encSpaceL = parts[0];
                    p.encSpaceT = parts[1];
                    p.encSpaceR = parts[2];
                    p.encSpaceB = parts[3];
                }
            }
        }

        return result;
    }
}

// --- SHADERS ---
const ZebraShader = {
    uniforms: { time: { value: 0 } },
    vertexShader: `
        varying vec3 vNormal;
        varying vec3 vWorldPosition;
        varying vec3 vViewPosition;
        void main() {
            vNormal = normalize(normalMatrix * normal);
            vec4 worldPos = modelMatrix * vec4(position, 1.0);
            vWorldPosition = worldPos.xyz;
            vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
            vViewPosition = -mvPosition.xyz;
            gl_Position = projectionMatrix * mvPosition;
        }
    `,
    fragmentShader: `
        varying vec3 vNormal;
        varying vec3 vWorldPosition;
        varying vec3 vViewPosition;
        void main() {
            vec3 viewDir = normalize(vViewPosition);
            vec3 normal = normalize(vNormal);
            // Use reflection vector for better "zebra" look on surfaces
            vec3 reflectDir = reflect(-viewDir, normal);
            float pattern = sin(reflectDir.y * 20.0 + reflectDir.x * 10.0) * 0.5 + 0.5;
            float stripe = step(0.5, pattern);
            gl_FragColor = vec4(vec3(stripe * 0.9 + 0.05), 1.0);
        }
    `
};

// --- APP STATE ---
class App {
    constructor() {
        this.container = document.getElementById('canvas-container');
        this.stats = document.getElementById('stats');
        this.renderRequested = false;
        this.setupScene();
        this.setupEventListeners();
        this.renderModel();
    }

    setupScene() {
        this.scene = new THREE.Scene();
        this.scene.background = new THREE.Color(0x0d0d0d);

        this.cameraMode = 'perspective'; // 'perspective' or 'orthographic'
        this.camera = new THREE.PerspectiveCamera(25, window.innerWidth / window.innerHeight, 0.1, 10000);
        this.camera.position.set(600, 600, 600);

        this.renderer = new THREE.WebGLRenderer({ antialias: true });
        this.renderer.setSize(this.container.clientWidth, this.container.clientHeight);
        this.renderer.setPixelRatio(window.devicePixelRatio);
        this.container.appendChild(this.renderer.domElement);

        this.controls = new OrbitControls(this.camera, this.renderer.domElement);
        this.controls.enableDamping = true;

        // Lights
        const ambientLight = new THREE.AmbientLight(0xffffff, 0.5);
        this.scene.add(ambientLight);

        const dirLight = new THREE.DirectionalLight(0xffffff, 1);
        dirLight.position.set(100, 200, 100);
        this.scene.add(dirLight);

        const topLight = new THREE.DirectionalLight(0x00ffcc, 0.5);
        topLight.position.set(-100, 500, -100);
        this.scene.add(topLight);

        // Grid - Horizontal ground plane
        const grid = new THREE.GridHelper(1000, 20, 0x333333, 0x222222);
        this.scene.add(grid);

        // Helpers
        const axes = new THREE.AxesHelper(100);
        this.scene.add(axes);

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
        // Core buttons
        document.getElementById('render-btn').addEventListener('click', () => this.renderModel());
        document.getElementById('export-btn').addEventListener('click', () => this.exportSTL());
        document.getElementById('export-config-btn').addEventListener('click', () => this.exportATHConfig());
        document.getElementById('model-type').addEventListener('change', (e) => this.toggleModelType(e.target.value));
        document.getElementById('display-mode').addEventListener('change', () => this.requestRender());

        // Zoom controls
        document.getElementById('zoom-in').addEventListener('click', () => this.zoom(0.8));
        document.getElementById('zoom-out').addEventListener('click', () => this.zoom(1.2));
        document.getElementById('camera-toggle').addEventListener('click', () => this.toggleCamera());
        document.getElementById('zoom-reset').addEventListener('click', () => {
            this.controls.reset();
        });
        document.getElementById('focus-horn').addEventListener('click', () => this.focusOnModel());
        document.getElementById('rear-shape').addEventListener('change', (e) => {
            document.getElementById('rear-params').style.display = e.target.value !== '0' ? 'block' : 'none';
            this.requestRender();
        });

        const loadBtn = document.getElementById('load-config-btn');
        const fileInput = document.getElementById('config-upload');
        loadBtn.addEventListener('click', () => fileInput.click());
        fileInput.addEventListener('change', (e) => this.handleFileUpload(e));

        // Sync inputs and sliders
        const allInputs = document.querySelectorAll('#ui-panel input');
        allInputs.forEach(input => {
            const isSlider = input.type === 'range';
            const id = input.id;

            input.addEventListener('input', () => {
                if (isSlider) {
                    // If it's a slider, update the corresponding text input
                    const targetId = id.replace('-slider', '');
                    const target = document.getElementById(targetId);
                    if (target) target.value = input.value;
                } else {
                    // If it's a text input, update the corresponding slider
                    const targetId = id + '-slider';
                    const target = document.getElementById(targetId);
                    if (target) target.value = input.value;
                }

                if (document.getElementById('live-update').checked) {
                    this.requestRender();
                }
            });
        });
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
        document.getElementById('r-osse-params').style.display = type === 'R-OSSE' ? 'block' : 'none';
        document.getElementById('osse-params').style.display = type === 'OSSE' ? 'block' : 'none';

        // Hide/Show Morph and Enclosure for OSSE only
        const isOSSE = type === 'OSSE';
        const morphEl = document.getElementById('osse-morph-details');
        const encEl = document.getElementById('osse-enclosure-details');
        if (morphEl) morphEl.style.display = isOSSE ? 'block' : 'none';
        if (encEl) encEl.style.display = isOSSE ? 'block' : 'none';

        this.requestRender();
    }

    handleFileUpload(event) {
        const file = event.target.files[0];
        if (!file) return;

        const reader = new FileReader();
        reader.onload = (e) => {
            const content = e.target.result;
            const parsed = ATHConfigParser.parse(content);
            if (parsed.type) {
                this.updateUIWithParams(parsed);
                this.toggleModelType(parsed.type);
                document.getElementById('model-type').value = parsed.type;
                this.requestRender();
            } else {
                alert('Could not find OSSE or R-OSSE block in config file.');
            }
        };
        reader.readAsText(file);
    }

    updateUIWithParams(parsed) {
        const type = parsed.type;
        const params = parsed.params;
        const setVal = (id, val) => {
            if (val === undefined || val === null) return;
            const el = document.getElementById(id);
            if (el) {
                el.value = val;
                const slider = document.getElementById(id + '-slider');
                if (slider) slider.value = val;
            }
        };
        const setChecked = (id, val) => {
            const el = document.getElementById(id);
            if (el) el.checked = val === '1' || val === 'true' || val === true;
        };

        if (type === 'R-OSSE') {
            setVal('param-R', params.R);
            setVal('param-a', params.a);
            setVal('param-a0', params.a0);
            setVal('param-k', params.k);
            setVal('param-m', params.m);
            setVal('param-b', params.b);
            setVal('param-r', params.r);
            setVal('param-q', params.q);
            setVal('param-r0', params.r0);
            setVal('param-tmax', params.tmax);

            // Rollback
            if (params.Rollback) setChecked('param-rollback', params.Rollback);
            setVal('param-rollback-start', params['Rollback.StartAt']);
            setVal('param-rollback-angle', params['Rollback.Angle']);
        } else if (type === 'OSSE') {
            setVal('osse-L', params.L);
            setVal('osse-a', params.a);
            setVal('osse-a0', params.a0);
            setVal('osse-r0', params.r0);
            setVal('osse-k', params.k);
            setVal('osse-s', params.s);
            setVal('osse-n', params.n);
            setVal('osse-q', params.q);
            setVal('osse-h', params.h);

            // Morphing
            setVal('osse-morph-target', params.morphTarget);
            setVal('osse-morph-width', params.morphWidth);
            setVal('osse-morph-height', params.morphHeight);
            setVal('osse-morph-corner', params.morphCorner);
            setVal('osse-morph-rate', params.morphRate);
            setVal('osse-morph-fixed', params.morphFixed);

            // Enclosure
            setVal('osse-enc-depth', params.encDepth);
            setVal('osse-enc-edge', params.encEdge);
            setVal('osse-enc-edge-type', params.encEdgeType);
            setVal('osse-enc-space-l', params.encSpaceL);
            setVal('osse-enc-space-t', params.encSpaceT);
            setVal('osse-enc-space-r', params.encSpaceR);
            setVal('osse-enc-space-b', params.encSpaceB);
        }

        // Mesh settings (shared)
        setVal('mesh-angular', params.angularSegments);
        setVal('mesh-length', params.lengthSegments);
        setVal('mesh-corner', params.cornerSegments);
        setVal('mesh-quadrants', params.quadrants);
        setVal('mesh-wall', params.wallThickness);

        if (params.RearShape) {
            setVal('rear-shape', params.RearShape);
            document.getElementById('rear-params').style.display = params.RearShape !== '0' ? 'block' : 'none';
        }

        // Source & ABEC
        setVal('source-shape', params.sourceShape);
        setVal('source-radius', params.sourceRadius);
        setVal('source-velocity', params.sourceVelocity);
        setVal('abec-simtype', params.abecSimType);
        setVal('abec-f1', params.abecF1);
        setVal('abec-f2', params.abecF2);
        setVal('abec-numfreq', params.abecNumFreq);
    }


    renderModel() {
        if (this.hornMesh) {
            this.scene.remove(this.hornMesh);
            this.hornMesh.geometry.dispose();
            this.hornMesh.material.dispose();
        }

        const params = this.getParams();
        const geometry = new THREE.BufferGeometry();

        const radialSteps = params.angularSegments;
        const lengthSteps = params.lengthSegments;

        const vertices = [];
        const indices = [];

        for (let j = 0; j <= lengthSteps; j++) {
            const t = j / lengthSteps;
            const tActual = params.type === 'R-OSSE' ? t * (params.tmax || 1.0) : t;

            // Morphing interpolation factor with Rate and FixedPart
            let morphFactor = 0;
            if (params.type === 'OSSE' && params.morphTarget !== 0) {
                if (t > params.morphFixed) {
                    const tMorph = (t - params.morphFixed) / (1 - params.morphFixed);
                    morphFactor = Math.pow(tMorph, params.morphRate || 3);
                }
            }

            for (let i = 0; i <= radialSteps; i++) {
                const p = (i / radialSteps) * Math.PI * 2;

                let profile;
                if (params.type === 'R-OSSE') {
                    profile = calculateROSSE(tActual, p, params);
                } else {
                    profile = calculateOSSE(tActual * params.L, p, params);
                    if (params.h > 0) {
                        profile.y += params.h * Math.sin(tActual * Math.PI);
                    }
                }

                let x = profile.x;
                let r = profile.y;

                // Morphing for OSSE
                if (params.type === 'OSSE' && params.morphTarget !== 0 && morphFactor > 0) {
                    const targetWidth = params.morphWidth || r * 2;
                    const targetHeight = params.morphHeight || r * 2;
                    const rectR = this.getRoundedRectRadius(p, targetWidth, targetHeight, params.morphCorner || 35);
                    r = THREE.MathUtils.lerp(r, rectR, morphFactor);
                }

                const vx = r * Math.cos(p);
                const vy = x;
                const vz = r * Math.sin(p);

                vertices.push(vx, vy, vz);
            }
        }

        // Add Rollback for R-OSSE
        if (params.type === 'R-OSSE' && params.rollback) {
            this.addRollbackGeometry(vertices, indices, params, lengthSteps, radialSteps);
        }

        // Add Enclosure for OSSE
        if (params.type === 'OSSE' && params.encDepth > 0) {
            this.addEnclosureGeometry(vertices, indices, params);
        } else if (params.rearShape !== 0) {
            this.addRearShapeGeometry(vertices, indices, params, lengthSteps, radialSteps);
        }

        for (let j = 0; j < lengthSteps; j++) {
            for (let i = 0; i < radialSteps; i++) {
                const row1 = j * (radialSteps + 1);
                const row2 = (j + 1) * (radialSteps + 1);

                indices.push(row1 + i, row1 + i + 1, row2 + i + 1);
                indices.push(row1 + i, row2 + i + 1, row2 + i);
            }
        }

        geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
        geometry.setIndex(indices);
        geometry.computeVertexNormals();

        const displayMode = document.getElementById('display-mode').value;
        let material;

        if (displayMode === 'zebra') {
            material = new THREE.ShaderMaterial({
                ...ZebraShader,
                side: THREE.DoubleSide
            });
        } else if (displayMode === 'curvature') {
            // Simple curvature-based coloring using normal variation
            const colors = this.calculateCurvatureColors(geometry, radialSteps, lengthSteps);
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

    focusOnModel() {
        if (!this.hornMesh) return;
        this.hornMesh.geometry.computeBoundingBox();
        const box = this.hornMesh.geometry.boundingBox;
        const center = new THREE.Vector3();
        box.getCenter(center);

        this.controls.target.copy(center);
        this.controls.update();
    }

    calculateCurvatureColors(geometry, radialSteps, lengthSteps) {
        const normals = geometry.attributes.normal.array;
        const count = normals.length / 3;
        const colors = new Float32Array(count * 3);

        for (let j = 0; j <= lengthSteps; j++) {
            for (let i = 0; i <= radialSteps; i++) {
                const idx = (j * (radialSteps + 1) + i) * 3;

                // Compare current normal to its neighbors
                let curvature = 0;
                const neighbors = [
                    [j - 1, i], [j + 1, i], [j, i - 1], [j, i + 1]
                ];

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
                colors[idx] = c;         // R high curvature
                colors[idx + 1] = 1 - c; // G low curvature
                colors[idx + 2] = 0.5;   // B
            }
        }
        return colors;
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
        // Estimate a good size based on distance or model dimensions
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
            this.camera = new THREE.OrthographicCamera(
                -size * aspect, size * aspect,
                size, -size,
                0.1, 10000
            );
            this.cameraMode = 'orthographic';
            document.getElementById('camera-toggle').innerText = '▲';
        } else {
            this.camera = new THREE.PerspectiveCamera(25, aspect, 0.1, 10000);
            this.cameraMode = 'perspective';
            document.getElementById('camera-toggle').innerText = '⬚';
        }

        this.camera.position.copy(pos);
        this.scene.add(this.camera);

        // Re-bind controls to new camera
        const oldControls = this.controls;
        this.controls = new OrbitControls(this.camera, this.renderer.domElement);
        this.controls.target.copy(target);
        this.controls.enableDamping = true;
        this.controls.update();

        oldControls.dispose();
    }

    async saveFile(content, fileName, options = {}) {
        const prefix = document.getElementById('export-prefix').value || 'horn';
        const counter = document.getElementById('export-counter').value;
        const finalName = `${prefix}_${counter}${options.extension}`;

        if ('showSaveFilePicker' in window) {
            try {
                const handle = await window.showSaveFilePicker({
                    suggestedName: finalName,
                    types: [options.typeInfo]
                });
                const writable = await handle.createWritable();
                await writable.write(content);
                await writable.close();
                this.incrementCounter();
                return;
            } catch (err) {
                if (err.name === 'AbortError') return;
                console.warn('showSaveFilePicker failed, fallback to legacy', err);
            }
        }

        const blob = new Blob([content], { type: options.contentType || 'application/octet-stream' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = finalName;
        link.click();
        URL.revokeObjectURL(url);
        this.incrementCounter();
    }

    incrementCounter() {
        const el = document.getElementById('export-counter');
        if (el) el.value = parseInt(el.value) + 1;
    }

    exportSTL() {
        if (!this.hornMesh) return;
        const exporter = new STLExporter();
        const result = exporter.parse(this.hornMesh, { binary: true });

        this.saveFile(result, 'horn.stl', {
            extension: '.stl',
            contentType: 'application/sla',
            typeInfo: {
                description: 'STL Model',
                accept: { 'model/stl': ['.stl'] }
            }
        });
    }

    getParams() {
        const getVal = (id, type = 'float') => {
            const el = document.getElementById(id);
            if (!el) return type === 'float' ? 0 : (type === 'int' ? 0 : (type === 'bool' ? false : ''));
            if (type === 'float') return parseFloat(el.value.replace(',', '.'));
            if (type === 'int') return parseInt(el.value);
            if (type === 'bool') return el.checked;
            return el.value;
        };

        const type = getVal('model-type', 'string');
        let params;

        if (type === 'R-OSSE') {
            params = {
                type: type,
                a0: getVal('param-a0'),
                r0: getVal('param-r0'),
                R: parseExpression(getVal('param-R', 'string')),
                a: parseExpression(getVal('param-a', 'string')),
                k: getVal('param-k'),
                m: getVal('param-m'),
                b: parseExpression(getVal('param-b', 'string')),
                r: getVal('param-r'),
                q: getVal('param-q'),
                tmax: getVal('param-tmax'),
                rollback: getVal('param-rollback', 'bool'),
                rollbackStart: getVal('param-rollback-start'),
                rollbackAngle: getVal('param-rollback-angle'),
            };
            if (isNaN(params.tmax)) params.tmax = 1.0;
        } else {
            params = {
                type: type,
                a0: getVal('osse-a0'),
                r0: getVal('osse-r0'),
                k: getVal('osse-k'),
                n: getVal('osse-n'),
                q: getVal('osse-q'),
                h: getVal('osse-h'),
                L: getVal('osse-L'),
                a: parseExpression(getVal('osse-a', 'string')),
                s: parseExpression(getVal('osse-s', 'string')),
            };
        }

        Object.assign(params, {

            // Morphing
            morphTarget: getVal('osse-morph-target', 'int'),
            morphWidth: getVal('osse-morph-width'),
            morphHeight: getVal('osse-morph-height'),
            morphCorner: getVal('osse-morph-corner'),
            morphRate: getVal('osse-morph-rate'),
            morphFixed: getVal('osse-morph-fixed'),

            // Enclosure
            encDepth: getVal('osse-enc-depth'),
            encEdge: getVal('osse-enc-edge'),
            encEdgeType: getVal('osse-enc-edge-type', 'int'),
            encSpace: [
                getVal('osse-enc-space-l'),
                getVal('osse-enc-space-t'),
                getVal('osse-enc-space-r'),
                getVal('osse-enc-space-b')
            ],

            // Mesh
            angularSegments: getVal('mesh-angular', 'int'),
            lengthSegments: getVal('mesh-length', 'int'),
            quadrants: getVal('mesh-quadrants', 'string'),
            cornerSegments: getVal('mesh-corner', 'int'),
            rearShape: getVal('rear-shape', 'int'),
            rearRadius: getVal('rear-radius'),
            wallThickness: getVal('mesh-wall'),

            // Source & ABEC
            sourceShape: getVal('source-shape', 'int'),
            sourceRadius: getVal('source-radius'),
            sourceVelocity: getVal('source-velocity', 'int'),
            abecSimType: getVal('abec-simtype', 'int'),
            abecF1: getVal('abec-f1'),
            abecF2: getVal('abec-f2'),
            abecNumFreq: getVal('abec-numfreq', 'int'),
        });

        return params;
    }

    exportATHConfig() {
        const params = this.getParams();
        const getRawVal = (id) => {
            const el = document.getElementById(id);
            return el ? el.value : '';
        };

        let content = '; Ath config\n';
        content += `; Generated: ${new Date().toISOString().slice(0, 16).replace('T', ' ')}\n`;

        if (params.type === 'R-OSSE') {
            content += 'R-OSSE = {\n';
            content += `R = ${getRawVal('param-R')}\n`;
            content += `a = ${getRawVal('param-a')}\n`;
            content += `a0 = ${params.a0}\n`;
            content += `b = ${getRawVal('param-b')}\n`;
            content += `k = ${params.k}\n`;
            content += `m = ${params.m}\n`;
            content += `q = ${params.q}\n`;
            content += `r = ${params.r}\n`;
            content += `r0 = ${params.r0}\n`;
            if (params.tmax !== 1.0) content += `tmax = ${params.tmax}\n`;
            content += '}\n';

            if (params.rollback) {
                content += `Rollback = 1\n`;
                content += `Rollback.Angle = ${params.rollbackAngle}\n`;
                content += `Rollback.StartAt = ${params.rollbackStart}\n`;
            }
        } else {
            content += `Coverage.Angle = ${getRawVal('osse-a')}\n`;
            content += `Length = ${params.L}\n`;
            content += `Term.n = ${params.n}\n`;
            content += `Term.q = ${params.q}\n`;
            content += `Term.s = ${getRawVal('osse-s')}\n`;
            content += `Throat.Angle = ${params.a0}\n`;
            content += `Throat.Diameter = ${params.r0 * 2}\n`;
            content += `Throat.Profile = 1\n`;
            if (params.h !== 0) content += `OS.h = ${params.h}\n`;

            if (params.morphTarget !== 0) {
                if (params.morphCorner > 0) content += `Morph.CornerRadius = ${params.morphCorner}\n`;
                content += `Morph.FixedPart = ${params.morphFixed}\n`;
                content += `Morph.Rate = ${params.morphRate}\n`;
                content += `Morph.TargetShape = ${params.morphTarget}\n`;
                if (params.morphWidth > 0) content += `Morph.TargetWidth = ${params.morphWidth}\n`;
                if (params.morphHeight > 0) content += `Morph.TargetHeight = ${params.morphHeight}\n`;
            }

            if (params.encDepth > 0) {
                content += `Mesh.Enclosure = {\n`;
                content += `Depth = ${params.encDepth}\n`;
                content += `EdgeRadius = ${params.encEdge}\n`;
                content += `EdgeType = ${params.encEdgeType}\n`;
                content += `Spacing = ${params.encSpace.join(',')}\n`;
                content += `}\n`;
            }
        }

        content += `Mesh.AngularSegments = ${params.angularSegments}\n`;
        if (params.morphTarget === 1) content += `Mesh.CornerSegments = ${params.cornerSegments}\n`;
        content += `Mesh.LengthSegments = ${params.lengthSegments}\n`;
        content += `Mesh.Quadrants = ${params.quadrants}\n`;
        if (params.wallThickness > 0) content += `Mesh.WallThickness = ${params.wallThickness}\n`;

        content += `Output.ABECProject = 1\n`;
        content += `Output.STL = 1\n`;

        content += `Source.Shape = ${params.sourceShape}\n`;
        if (params.sourceRadius !== -1) content += `Source.Radius = ${params.sourceRadius}\n`;
        content += `Source.Velocity = ${params.sourceVelocity}\n`;

        content += `ABEC.SimType = ${params.abecSimType}\n`;
        content += `ABEC.f1 = ${params.abecF1}\n`;
        content += `ABEC.f2 = ${params.abecF2}\n`;
        content += `ABEC.NumFrequencies = ${params.abecNumFreq}\n`;

        this.saveFile(content, 'config.txt', {
            extension: '.txt',
            contentType: 'text/plain',
            typeInfo: {
                description: 'ATH Config',
                accept: { 'text/plain': ['.txt'] }
            }
        });
    }

    getRoundedRectRadius(p, width, height, radius) {
        // A simple rounded rectangle radius formula
        // p is in [0, 2pi]
        const cos = Math.cos(p);
        const sin = Math.sin(p);
        const absCos = Math.abs(cos);
        const absSin = Math.abs(sin);

        const w = width / 2;
        const h = height / 2;
        const r = radius;

        let resR = 0;
        if (absCos * h > absSin * w) {
            resR = w / absCos;
        } else {
            resR = h / absSin;
        }

        // Simple smoothing for rounded corners
        const cornerDist = Math.sqrt(w * w + h * h) - r;
        if (resR > cornerDist) {
            // Apply a basic circular blending at the corners
            // This is a simplified version of the superellipse or dedicated rounded rect formula
            const pNorm = (p % (Math.PI / 2)) / (Math.PI / 2);
            // ... formula simplification for visualization ...
        }

        return resR;
    }

    addRollbackGeometry(vertices, indices, params, lengthSteps, radialSteps) {
        const lastRowStart = lengthSteps * (radialSteps + 1);
        const startIdx = vertices.length / 3;
        const rollbackAngle = (params.rollbackAngle || 180) * (Math.PI / 180);
        const rollbackSteps = 12;
        const startAt = Math.max(0.01, Math.min(0.99, params.rollbackStart || 0.5));

        for (let j = 1; j <= rollbackSteps; j++) {
            const t = j / rollbackSteps;
            const angle = t * rollbackAngle;

            for (let i = 0; i <= radialSteps; i++) {
                const p = (i / radialSteps) * Math.PI * 2;
                const mouthIdx = lastRowStart + i;
                const mx = vertices[mouthIdx * 3];
                const my = vertices[mouthIdx * 3 + 1];
                const mz = vertices[mouthIdx * 3 + 2];
                const r_mouth = Math.sqrt(mx * mx + mz * mz);

                // Compute roll radius from profile difference at startAt vs mouth
                let profileAtStart;
                if (params.type === 'R-OSSE') {
                    profileAtStart = calculateROSSE(startAt * (params.tmax || 1.0), p, params);
                } else {
                    profileAtStart = calculateOSSE(startAt * params.L, p, params);
                }
                const roll_r = Math.max(5, (r_mouth - profileAtStart.y) * 0.5);

                // Toroidal rollback: curve inward and backward
                const r = r_mouth + roll_r * (1 - Math.cos(angle));
                const y = my - roll_r * Math.sin(angle);

                vertices.push(r * Math.cos(p), y, r * Math.sin(p));
            }
        }

        // Connect slices
        for (let j = 0; j < rollbackSteps; j++) {
            const row1Offset = j === 0 ? lastRowStart : startIdx + (j - 1) * (radialSteps + 1);
            const row2Offset = startIdx + j * (radialSteps + 1);

            for (let i = 0; i < radialSteps; i++) {
                indices.push(row1Offset + i, row1Offset + i + 1, row2Offset + i + 1);
                indices.push(row1Offset + i, row2Offset + i + 1, row2Offset + i);
            }
        }
    }

    addEnclosureGeometry(vertices, indices, params) {
        const lastRowStart = params.lengthSegments * (params.angularSegments + 1);
        const mouthY = vertices[lastRowStart * 3 + 1];

        // Spacing: L(eft), T(op), R(ight), B(ottom) — distances from waveguide outline to box edge
        const [sL, sT, sR, sB] = params.encSpace || [25, 25, 25, 25];
        const depth = params.encDepth;
        const edgeR = params.encEdge || 0;

        // Find the bounding extent of the mouth ring
        let maxX = 0, minX = 0, maxZ = 0, minZ = 0;
        const radialSteps = params.angularSegments;
        for (let i = 0; i <= radialSteps; i++) {
            const idx = lastRowStart + i;
            const mx = vertices[idx * 3];
            const mz = vertices[idx * 3 + 2];
            if (mx > maxX) maxX = mx;
            if (mx < minX) minX = mx;
            if (mz > maxZ) maxZ = mz;
            if (mz < minZ) minZ = mz;
        }

        // Enclosure outer rectangle dimensions (half-widths from center)
        const boxRight = maxX + sR;
        const boxLeft = minX - sL;
        const boxTop = maxZ + sT;
        const boxBot = minZ - sB;

        const startIdx = vertices.length / 3;

        // Build a rounded-rectangle profile for the enclosure cross-section
        // edgeR is the corner rounding radius, clamped to half the smallest side
        const halfW = (boxRight - boxLeft) / 2;
        const halfH = (boxTop - boxBot) / 2;
        const cx = (boxRight + boxLeft) / 2;
        const cz = (boxTop + boxBot) / 2;
        const cr = Math.min(edgeR, halfW, halfH);

        const encSteps = Math.max(radialSteps, 40);
        const cornerSegs = Math.max(4, params.cornerSegments || 4);

        // Generate rounded-rect outline
        const outline = [];
        const addCorner = (cx, cz, startAngle) => {
            for (let i = 0; i <= cornerSegs; i++) {
                const a = startAngle + (i / cornerSegs) * (Math.PI / 2);
                outline.push({ x: cx + cr * Math.cos(a), z: cz + cr * Math.sin(a) });
            }
        };

        // Bottom-right corner -> top-right -> top-left -> bottom-left
        addCorner(cx + halfW - cr, cz - halfH + cr, -Math.PI / 2); // BR
        addCorner(cx + halfW - cr, cz + halfH - cr, 0);             // TR
        addCorner(cx - halfW + cr, cz + halfH - cr, Math.PI / 2);   // TL
        addCorner(cx - halfW + cr, cz - halfH + cr, Math.PI);        // BL

        const totalPts = outline.length;

        // Front ring (at mouthY — the baffle plane)
        for (let i = 0; i < totalPts; i++) {
            vertices.push(outline[i].x, mouthY, outline[i].z);
        }

        // Back ring (at mouthY - depth — behind the baffle, toward/past the throat)
        for (let i = 0; i < totalPts; i++) {
            vertices.push(outline[i].x, mouthY - depth, outline[i].z);
        }

        const frontStart = startIdx;
        const backStart = startIdx + totalPts;

        // Side walls: connect front ring to back ring
        for (let i = 0; i < totalPts; i++) {
            const i2 = (i + 1) % totalPts;
            const f1 = frontStart + i;
            const f2 = frontStart + i2;
            const b1 = backStart + i;
            const b2 = backStart + i2;
            indices.push(f1, f2, b2);
            indices.push(f1, b2, b1);
        }

        // Front baffle face — connect mouth ring to enclosure front ring
        // We create a strip from each mouth vertex to the nearest enclosure edge point
        // Simple fan approach: connect mouth ring to enclosure ring via radial mapping
        for (let i = 0; i < radialSteps; i++) {
            const p = (i / radialSteps) * 2 * Math.PI;
            const p2 = ((i + 1) / radialSteps) * 2 * Math.PI;
            // Map angle to enclosure outline index
            const ei = Math.round((p / (2 * Math.PI)) * totalPts) % totalPts;
            const ei2 = Math.round((p2 / (2 * Math.PI)) * totalPts) % totalPts;

            const mi = lastRowStart + i;
            const mi2 = lastRowStart + i + 1;

            // Triangle from mouth to enclosure front ring
            indices.push(mi, mi2, frontStart + ei2);
            indices.push(mi, frontStart + ei2, frontStart + ei);
        }

        // Back cap — fan from center to back ring
        const backCenterIdx = vertices.length / 3;
        vertices.push(cx, mouthY - depth, cz);
        for (let i = 0; i < totalPts; i++) {
            const i2 = (i + 1) % totalPts;
            indices.push(backStart + i, backCenterIdx, backStart + i2);
        }
    }

    addRearShapeGeometry(vertices, indices, params, lengthSteps, radialSteps) {
        const lastRowStart = lengthSteps * (radialSteps + 1);
        const mouthY = vertices[lastRowStart * 3 + 1];

        if (params.rearShape === 2) { // Flat Disc (User 2)
            const centerIdx = vertices.length / 3;
            vertices.push(0, mouthY, 0);
            for (let i = 0; i <= radialSteps; i++) {
                const mouthIdx = lastRowStart + i;
                if (i < radialSteps) {
                    indices.push(mouthIdx, mouthIdx + 1, centerIdx);
                }
            }
        } else if (params.rearShape === 1) { // Full Model (User 1 - Realistic wall/rear)
            // Implementation of wall thickness and rear cap
            const thickness = params.wallThickness || 5;
            const startIdx = vertices.length / 3;

            // Create a secondary mesh slightly offset
            for (let i = 0; i <= radialSteps; i++) {
                const p = (i / radialSteps) * Math.PI * 2;
                const mouthIdx = lastRowStart + i;
                const mx = vertices[mouthIdx * 3];
                const mz = vertices[mouthIdx * 3 + 2];

                // Simple outward extrusion for thickness
                const r = Math.sqrt(mx * mx + mz * mz) + thickness;
                vertices.push(r * Math.cos(p), mouthY - thickness, r * Math.sin(p));
            }

            // Connect mouth to outer rim
            for (let i = 0; i < radialSteps; i++) {
                const mouthIdx = lastRowStart + i;
                const rimIdx = startIdx + i;
                indices.push(mouthIdx, mouthIdx + 1, rimIdx + 1);
                indices.push(mouthIdx, rimIdx + 1, rimIdx);
            }

            // Cap the rear
            const rearCenterIdx = vertices.length / 3;
            vertices.push(0, mouthY - thickness, 0);
            for (let i = 0; i < radialSteps; i++) {
                indices.push(startIdx + i, startIdx + i + 1, rearCenterIdx);
            }
        }
    }

    animate() {
        requestAnimationFrame(() => this.animate());
        this.controls.update();
        this.renderer.render(this.scene, this.camera);
    }
}

new App();
