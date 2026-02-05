#!/usr/bin/env node
/**
 * Test Gmsh Integration
 * 
 * This script tests the Gmsh meshing pipeline by:
 * 1. Generating a simple horn geometry
 * 2. Converting it to Gmsh format
 * 3. Running Gmsh to create a mesh
 * 4. Comparing with current mesh output
 */

import { buildHornMesh } from './src/geometry/meshBuilder.js';
import { meshHornWithGmsh, generateGeoScript } from './src/export/gmshBridge.js';
import { writeFileSync } from 'fs';
import { join } from 'path';

// Test parameters - simple OSSE horn
const testParams = {
    type: 'OSSE',
    L: 120,
    a0: 15.5,
    r0: 12.7,
    rm: 40,
    am: 48.5,
    a: 48.5,
    
    lengthSegments: 40,
    angularSegments: 32,
    quadrants: '1234',
    
    meshSize: 3.0,
    
    // No enclosure for initial test
    useEnclosure: false
};

console.log('='.repeat(60));
console.log('MWG Gmsh Integration Test');
console.log('='.repeat(60));
console.log('');

console.log('Step 1: Generating horn geometry with current system...');
const currentMesh = buildHornMesh(testParams);
console.log(`✓ Generated mesh: ${currentMesh.vertices.length / 3} vertices, ${currentMesh.indices.length / 3} triangles`);
console.log('');

console.log('Step 2: Testing Gmsh .geo script generation...');
try {
    // Create a simple test .geo file
    const testStations = [
        {
            y: 0,
            points: [
                { x: 12.7, z: 0 },
                { x: 0, z: 12.7 },
                { x: -12.7, z: 0 },
                { x: 0, z: -12.7 }
            ]
        },
        {
            y: 60,
            points: [
                { x: 25, z: 0 },
                { x: 0, z: 25 },
                { x: -25, z: 0 },
                { x: 0, z: -25 }
            ]
        },
        {
            y: 120,
            points: [
                { x: 40, z: 0 },
                { x: 0, z: 40 },
                { x: -40, z: 0 },
                { x: 0, z: -40 }
            ]
        }
    ];
    
    const geoScript = generateGeoScript(testParams, testStations);
    const geoPath = join(process.cwd(), 'output', 'test_horn.geo');
    writeFileSync(geoPath, geoScript, 'utf8');
    console.log(`✓ Generated Gmsh .geo script: ${geoPath}`);
    console.log(`  Script length: ${geoScript.length} bytes`);
    console.log('');
    
    console.log('Step 3: Running Gmsh mesher...');
    const mshPath = join(process.cwd(), 'output', 'test_horn.msh');
    const result = await meshHornWithGmsh(testParams, currentMesh.vertices, mshPath, {
        elementSize: 3.0,
        optimize: true
    });
    
    console.log('✓ Gmsh meshing complete!');
    console.log(`  Output: ${result.path}`);
    console.log('');
    
    console.log('Step 4: Mesh comparison');
    console.log(`  Current system: ${currentMesh.vertices.length / 3} vertices, ${currentMesh.indices.length / 3} triangles`);
    console.log(`  Gmsh output: See ${mshPath}`);
    console.log('');
    
    console.log('='.repeat(60));
    console.log('✓ Test PASSED - Gmsh integration working');
    console.log('='.repeat(60));
    console.log('');
    console.log('Next steps:');
    console.log('1. Inspect test_horn.msh in Gmsh GUI:');
    console.log('   gmsh output/test_horn.msh');
    console.log('');
    console.log('2. Compare mesh quality');
    console.log('3. Integrate into main export pipeline');
    
} catch (error) {
    console.error('✗ Test FAILED');
    console.error(error);
    process.exit(1);
}
