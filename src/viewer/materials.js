import * as THREE from 'three';

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
