import * as THREE from 'three';

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
