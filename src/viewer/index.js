import * as THREE from "three";

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
    `,
};

export const Materials = {
  zebra: new THREE.ShaderMaterial({
    ...ZebraShader,
    side: THREE.DoubleSide,
  }),
  standard: new THREE.MeshPhysicalMaterial({
    color: 0xcccccc,
    metalness: 0.5,
    roughness: 0.3,
    transmission: 0,
    transparent: true,
    opacity: 0.9,
    side: THREE.DoubleSide,
  }),
  wireframe: new THREE.MeshPhysicalMaterial({
    color: 0xcccccc,
    metalness: 0.5,
    roughness: 0.3,
    side: THREE.DoubleSide,
    wireframe: true,
  }),
  curvature: new THREE.MeshPhongMaterial({
    vertexColors: true,
    side: THREE.DoubleSide,
  }),
};

// --- THEME HELPERS ---
function getCssVar(name, fallback) {
  const val = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  return val || fallback;
}

export function getSceneThemeColors() {
  const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const bg = getCssVar("--scene-bg", dark ? "#0a1018" : "#f2ede4");
  const gridA = getCssVar("--scene-grid-a", dark ? "#192538" : "#d8d2c4");
  const gridB = getCssVar("--scene-grid-b", dark ? "#111b2d" : "#e6e1d8");
  const topLight = getCssVar("--scene-top-light", dark ? "#5b8fe8" : "#e8a83a");
  return {
    bg: new THREE.Color(bg),
    gridA: new THREE.Color(gridA),
    gridB: new THREE.Color(gridB),
    topLight: new THREE.Color(topLight),
  };
}

// --- SCENE ---
export function createScene() {
  const scene = new THREE.Scene();
  const colors = getSceneThemeColors();
  scene.background = colors.bg;

  // Lights
  const ambientLight = new THREE.AmbientLight(0xffffff, 0.55);
  scene.add(ambientLight);

  const dirLight = new THREE.DirectionalLight(0xffffff, 1.0);
  dirLight.position.set(100, 200, 100);
  scene.add(dirLight);

  const topLight = new THREE.DirectionalLight(colors.topLight, 0.45);
  topLight.position.set(-100, 500, -100);
  scene.add(topLight);

  // Grid
  const grid = new THREE.GridHelper(1000, 20, colors.gridA, colors.gridB);
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
    -size * aspect,
    size * aspect,
    size,
    -size,
    0.1,
    10000,
  );
}
