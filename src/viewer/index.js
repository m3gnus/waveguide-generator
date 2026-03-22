import * as THREE from "three";

// --- MATERIALS ---
export const ZebraShader = {
  uniforms: { time: { value: 0 } },
  vertexShader: `
        varying vec3 vNormal;
        varying vec3 vViewPosition;
        void main() {
            vNormal = normalize(normalMatrix * normal);
            vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
            vViewPosition = -mvPosition.xyz;
            gl_Position = projectionMatrix * mvPosition;
        }
    `,
  fragmentShader: `
        varying vec3 vNormal;
        varying vec3 vViewPosition;
        void main() {
            vec3 viewDir = normalize(vViewPosition);
            vec3 normal = normalize(vNormal);
            vec3 reflectDir = reflect(-viewDir, normal);
            float pattern = sin(reflectDir.y * 20.0) * 0.5 + 0.5;
            float fw = fwidth(pattern);
            float stripe = smoothstep(0.5 - fw, 0.5 + fw, pattern);
            gl_FragColor = vec4(vec3(stripe * 0.9 + 0.05), 1.0);
        }
    `,
};

export const DISPLAY_MODES = [
  { key: "clay",      icon: "\u25FC", label: "Clay" },
  { key: "solidwire", icon: "\u229E", label: "Solid + Wire" },
  { key: "edges",     icon: "\u2B21", label: "Shaded + Edges" },
  { key: "wireframe", icon: "\u25B3", label: "Wireframe" },
  { key: "xray",      icon: "\u25C7", label: "X-Ray" },
  { key: "zebra",     icon: "\u2248", label: "Zebra" },
  { key: "curvature", icon: "\u25D0", label: "Curvature" },
];

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

  // Lights — only ambient in scene; directional lights live on the camera
  // so shading shifts as the user orbits (making curvature visible from any angle).
  const ambientLight = new THREE.AmbientLight(0xffffff, 0.2);
  scene.add(ambientLight);

  // Grid
  const grid = new THREE.GridHelper(1000, 20, colors.gridA, colors.gridB);
  scene.add(grid);

  // Helpers
  const axes = new THREE.AxesHelper(100);
  scene.add(axes);

  return scene;
}

// --- CAMERA LIGHTS ---
// Call after creating a camera; lights are added as children so they stay
// fixed relative to the screen while the model rotates beneath them.
export function attachCameraLights(camera, colors) {
  const dirLight = new THREE.DirectionalLight(0xffffff, 1.4);
  dirLight.position.set(-1, 2, 2); // upper-left in camera space
  camera.add(dirLight);

  const fillLight = new THREE.DirectionalLight(colors.topLight, 0.5);
  fillLight.position.set(2, -1, 1); // lower-right fill
  camera.add(fillLight);

  return { dirLight, fillLight };
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
