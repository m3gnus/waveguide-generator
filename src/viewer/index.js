import * as THREE from '../../node_modules/three/build/three.module.js';

// --- MATERIALS ---
export const ZebraShader = {
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

export const Materials = {
    zebra: new THREE.ShaderMaterial({
        ...ZebraShader,
        side: THREE.DoubleSide
    }),
    standard: new THREE.MeshPhysicalMaterial({
        color: 0xcccccc,
        metalness: 0.5,
        roughness: 0.3,
        transmission: 0,
        transparent: true,
        opacity: 0.9,
        side: THREE.DoubleSide
    }),
    wireframe: new THREE.MeshPhysicalMaterial({
        color: 0xcccccc,
        metalness: 0.5,
        roughness: 0.3,
        side: THREE.DoubleSide,
        wireframe: true
    }),
    curvature: new THREE.MeshPhongMaterial({
        vertexColors: true,
        side: THREE.DoubleSide
    })
};

// --- SCENE ---
export function createScene() {
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0d0d0d);

    // Lights
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.5);
    scene.add(ambientLight);

    const dirLight = new THREE.DirectionalLight(0xffffff, 1);
    dirLight.position.set(100, 200, 100);
    scene.add(dirLight);

    const topLight = new THREE.DirectionalLight(0x00ffcc, 0.5);
    topLight.position.set(-100, 500, -100);
    scene.add(topLight);

    // Grid - Horizontal ground plane
    const grid = new THREE.GridHelper(1000, 20, 0x333333, 0x222222);
    scene.add(grid);

    // Helpers
    const axes = new THREE.AxesHelper(100);
    scene.add(axes);

    return scene;
}

// --- CAMERAS ---
export function createPerspectiveCamera(aspect) {
    const camera = new THREE.PerspectiveCamera(25, aspect, 0.1, 10000);
    camera.position.set(600, 600, 600);
    return camera;
}

export function createOrthoCamera(aspect, size = 300) {
    return new THREE.OrthographicCamera(
        -size * aspect, size * aspect,
        size, -size,
        0.1, 10000
    );
}
