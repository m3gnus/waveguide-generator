import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const repoRoot = process.cwd();
const scriptPath = path.join(repoRoot, '.codex', 'skills', 'backlog-next', 'scripts', 'next-backlog-status.mjs');
const workerScriptPath = path.join(repoRoot, '.codex', 'skills', 'backlog-next', 'scripts', 'run-backlog-worker.mjs');

function writeFile(filePath, content) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, content);
}

function initRepo(tempDir, backlogMarkdown) {
  writeFile(path.join(tempDir, 'docs', 'backlog.md'), backlogMarkdown);
  execFileSync('git', ['init'], { cwd: tempDir });
  execFileSync('git', ['config', 'user.name', 'Codex Test'], { cwd: tempDir });
  execFileSync('git', ['config', 'user.email', 'codex@example.com'], { cwd: tempDir });
  execFileSync('git', ['add', 'docs/backlog.md'], { cwd: tempDir });
  execFileSync('git', ['commit', '-m', 'Add backlog fixture'], { cwd: tempDir });
}

function readStatus(tempDir) {
  const raw = execFileSync(process.execPath, [scriptPath, '--json'], {
    cwd: tempDir,
    encoding: 'utf8',
  });
  return JSON.parse(raw);
}

test('backlog helper routes low/medium slices to glm-5 and emits verification checklist', () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'backlog-next-glm-'));
  initRepo(
    tempDir,
    `# Backlog

## Current Baseline
- Solver path is stable.

## Active Backlog
### P1. Export cleanup
- [ ] Extract export orchestration into a dedicated module.
`,
  );

  const status = readStatus(tempDir);

  assert.equal(status.defaultReasoning, 'medium');
  assert.equal(status.defaultExecutor, 'glm-5');
  assert.deepEqual(status.executorPolicy, {
    low: 'glm-5',
    medium: 'glm-5',
    high: 'codex',
  });
  assert.ok(status.verificationChecklist.includes('Inspect the working tree and diff instead of trusting the worker summary.'));
});

test('backlog helper routes high-complexity slices to codex', () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'backlog-next-codex-'));
  initRepo(
    tempDir,
    `# Backlog

## Current Baseline
- Shared contract is stable.

## Active Backlog
### P1. API contract work
- [ ] Complete the cross-module public API contract rewrite for export orchestration.
`,
  );

  const status = readStatus(tempDir);

  assert.equal(status.defaultReasoning, 'high');
  assert.equal(status.defaultExecutor, 'codex');
  assert.match(status.prompt, /GLM-5 via opencode for low\/medium slices and Codex for high-complexity slices/);
});

test('worker helper selects glm-5 for medium slices and emits an opencode command', () => {
  const raw = execFileSync(
    process.execPath,
    [
      workerScriptPath,
      '--executor',
      'auto',
      '--reasoning',
      'medium',
      '--prompt',
      'Implement the selected slice.',
    ],
    {
      cwd: repoRoot,
      encoding: 'utf8',
    },
  );

  const payload = JSON.parse(raw);
  assert.equal(payload.executor, 'glm-5');
  assert.equal(payload.glm.model, 'zai-coding-plan/glm-5');
  assert.deepEqual(payload.glm.command.slice(0, 6), [
    'opencode',
    'run',
    '--dir',
    repoRoot,
    '--model',
    'zai-coding-plan/glm-5',
  ]);
  assert.equal(payload.codex, null);
});

test('worker helper selects codex for high-complexity slices and emits a handoff payload', () => {
  const raw = execFileSync(
    process.execPath,
    [
      workerScriptPath,
      '--executor',
      'auto',
      '--reasoning',
      'high',
      '--prompt',
      'Implement the selected slice.',
    ],
    {
      cwd: repoRoot,
      encoding: 'utf8',
    },
  );

  const payload = JSON.parse(raw);
  assert.equal(payload.executor, 'codex');
  assert.equal(payload.glm, null);
  assert.equal(payload.codex.model, 'gpt-5.3-codex');
  assert.equal(payload.codex.handoff.reasoning_effort, 'high');
  assert.equal(payload.codex.handoff.message, 'Implement the selected slice.');
});
