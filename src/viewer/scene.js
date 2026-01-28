import * as THREE from 'three';

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
