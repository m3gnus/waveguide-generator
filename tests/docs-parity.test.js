import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

function read(path) {
  return fs.readFileSync(new URL(path, import.meta.url), 'utf8');
}

const MESHER_PIN = 'ce7fbf2ce6b53dd92f2945c37405cf59fb34c78b';
const METAL_BEM_PIN = '06726228cc410296d4105f73a249b60b73681c9d';
const BEMPP_BEM_PIN = '4638578290eb0a56d0f81018b8806f0746ceb442';

test('maintained docs stay aligned with the Metal-or-Bempp solver/runtime contract', () => {
  const readme = read('../README.md');
  const projectDoc = read('../docs/PROJECT_DOCUMENTATION.md');
  const serverReadme = read('../server/README.md');

  const maintainedDocs = [
    ['README.md', readme],
    ['docs/PROJECT_DOCUMENTATION.md', projectDoc],
    ['server/README.md', serverReadme],
  ];

  for (const [label, text] of maintainedDocs) {
    assert.match(text, /hornlab-metal-bem/i, `${label} should document hornlab-metal-bem`);
    assert.match(text, /hornlab-bempp-bem|Bempp/i, `${label} should document Bempp`);
    assert.match(text, /Apple Silicon/, `${label} should state the Apple Silicon requirement`);
    assert.match(text, /OpenCL.*optional|optional OpenCL|OpenCL is optional/i, `${label} should document OpenCL as optional`);
    assert.match(text, /numba/i, `${label} should document the Bempp numba fallback`);
    assert.match(
      text,
      new RegExp(MESHER_PIN),
      `${label} should keep the pinned HornLab mesher commit`
    );
    assert.match(
      text,
      new RegExp(METAL_BEM_PIN),
      `${label} should keep the pinned hornlab-metal-bem commit`
    );
    assert.match(
      text,
      new RegExp(BEMPP_BEM_PIN),
      `${label} should keep the pinned hornlab-bempp-bem commit`
    );
    assert.doesNotMatch(
      text,
      /server\/solver\/solve\.py|opencl-cpu-env|opencl_gpu|opencl_cpu/i,
      `${label} should not document deleted in-repo solver/OpenCL runtime paths`
    );
    assert.doesNotMatch(
      text,
      /burton[- ]miller/i,
      `${label} should not document the removed Burton-Miller override`
    );
  }

  assert.match(
    serverReadme,
    /auto.*Metal.*Bempp|Metal.*Bempp/i,
    'server/README.md should state Auto can use Metal or Bempp'
  );
  assert.match(
    projectDoc,
    /solver_backend.*auto.*metal.*bempp/i,
    'PROJECT_DOCUMENTATION.md should document all public solver_backend values'
  );
});
