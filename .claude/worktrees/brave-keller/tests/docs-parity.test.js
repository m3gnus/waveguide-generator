import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

function read(path) {
  return fs.readFileSync(new URL(path, import.meta.url), 'utf8');
}

test('maintained docs stay aligned with the supported solver/runtime contract', () => {
  const readme = read('../README.md');
  const projectDoc = read('../docs/PROJECT_DOCUMENTATION.md');
  const serverReadme = read('../server/README.md');

  const maintainedTechnicalDocs = [
    ['docs/PROJECT_DOCUMENTATION.md', projectDoc],
    ['server/README.md', serverReadme]
  ];

  for (const [label, text] of maintainedTechnicalDocs) {
    assert.match(text, /opencl_cpu/, `${label} should mention opencl_cpu`);
    assert.match(text, /opencl_gpu/, `${label} should mention opencl_gpu`);
  }

  assert.match(readme, /there is no legacy `bempp_api` fallback path/i);
  assert.doesNotMatch(readme, /legacy `bempp_api` fallback:\s*`>=0\.3,<0\.4`/i);
  assert.match(serverReadme, /no numba fallback/i);
  assert.match(projectDoc, /no legacy `bempp_api` compatibility lane remains/i);
});
