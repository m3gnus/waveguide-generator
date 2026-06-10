import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

function read(path) {
  return fs.readFileSync(new URL(path, import.meta.url), 'utf8');
}

const MESHER_PIN = '2eb7b85e16952b2854ae0cadb661b87c4ad02313';
const METAL_BEM_PIN = '59528f5a0993ff4718d9037baae5fac008705b0c';

test('maintained docs stay aligned with the metal-only solver/runtime contract', () => {
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
    assert.match(text, /Apple Silicon/, `${label} should state the Apple Silicon requirement`);
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
    assert.doesNotMatch(
      text,
      /bempp-cl|pyopencl|pocl|opencl_gpu|opencl_cpu/i,
      `${label} should not document removed bempp/OpenCL install paths`
    );
    assert.doesNotMatch(
      text,
      /burton[- ]miller/i,
      `${label} should not document the removed Burton-Miller override`
    );
  }

  assert.match(
    serverReadme,
    /only solve backend/i,
    'server/README.md should state hornlab-metal-bem is the only solve backend'
  );
  assert.match(
    projectDoc,
    /only solve backend/i,
    'PROJECT_DOCUMENTATION.md should state hornlab-metal-bem is the only solve backend'
  );
});
