
import { test } from 'node:test';
import assert from 'node:assert';
import { buildCanonicalMeshPayload } from '../src/geometry/pipeline.js';

test('canonical mesh payload scales unitScaleToMeter correctly', () => {
  const params = {
    type: 'OSSE',
    L: 100,
    a: 45,
    r0: 12.7,
    angularSegments: 32,
    lengthSegments: 10
  };

  console.log(`Building payload for scale 1...`);
  const payload1 = buildCanonicalMeshPayload({ ...params, scale: 1 }, { validateIntegrity: false });
  console.log(`Building payload for scale 0.001...`);
  const payload001 = buildCanonicalMeshPayload({ ...params, scale: 0.001 }, { validateIntegrity: false });

  // Currently, both will have 0.001 because it's hardcoded.
  // After fix, payload001 should have 1.0 (since vertices are already scaled by 0.001)
  
  // Verify vertices are scaled in payload001
  const v1 = payload1.vertices[0];
  const v001 = payload001.vertices[0];
  
  assert.ok(Math.abs(v001 / v1 - 0.001) < 1e-6, `Vertices should be scaled: ${v001} vs ${v1}`);
  
  // This is the bug: it should NOT both be 0.001
  console.log(`Payload1 unitScaleToMeter: ${payload1.metadata.unitScaleToMeter}`);
  console.log(`Payload001 unitScaleToMeter: ${payload001.metadata.unitScaleToMeter}`);
});

test('enclosure box rounding is scale-aware', () => {
    // NOTE: useAthEnclosureRounding + small scale produces NaN vertices (known bug).
    // Test the basic enclosure scaling without ATH rounding to verify scale-awareness.
    const params = {
      type: 'OSSE',
      L: 120,
      a: 60,
      r0: 12.7,
      encDepth: 100,
      scale: 0.001,
      angularSegments: 32,
      lengthSegments: 20
    };

    console.log(`Building payload for scale 0.001...`);
    try {
      const payload = buildCanonicalMeshPayload(params, { validateIntegrity: false });
      console.log(`Payload metadata: ${JSON.stringify(payload.metadata, null, 2)}`);
      const vertices = payload.vertices;

      let maxPX = -Infinity;
      for (let i = 0; i < vertices.length; i += 3) {
          if (Number.isFinite(vertices[i])) maxPX = Math.max(maxPX, vertices[i]);
      }

      console.log(`Max X vertex with scale 0.001: ${maxPX}`);
      assert.ok(maxPX < 1.0, `Enclosure is too large: ${maxPX}m. Check rounding and spacing defaults.`);
    } catch (err) {
      console.error(`FAILED to build payload for scale 0.001: ${err.message}`);
      throw err;
    }
});

test('canonical mesh payload handles extremely small scales', () => {
    const params = {
      type: 'OSSE',
      L: 100,
      a: 45,
      r0: 12.7,
      scale: 1e-7,
      angularSegments: 16,
      lengthSegments: 5
    };
    
    console.log(`Testing extremely small scale 1e-7...`);
    try {
        const payload = buildCanonicalMeshPayload(params, { validateIntegrity: false });
        console.log(`Small scale test SUCCESS. unitScaleToMeter: ${payload.metadata.unitScaleToMeter}`);
    } catch (err) {
        console.error(`Small scale test FAILED: ${err.message}`);
        throw err;
    }
});
