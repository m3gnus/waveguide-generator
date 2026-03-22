import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

const args = process.argv.slice(2);

function fail(message) {
  console.error(message);
  process.exit(1);
}

function readArgValue(flag) {
  const index = args.indexOf(flag);
  if (index === -1) return null;
  return args[index + 1] ?? null;
}

function hasFlag(flag) {
  return args.includes(flag);
}

function readPrompt() {
  const promptFile = readArgValue('--prompt-file');
  if (promptFile) {
    const absolutePath = path.resolve(promptFile);
    if (!fs.existsSync(absolutePath)) fail(`Missing prompt file: ${absolutePath}`);
    return fs.readFileSync(absolutePath, 'utf8').trim();
  }

  if (!process.stdin.isTTY) {
    const stdin = fs.readFileSync(0, 'utf8').trim();
    if (stdin) return stdin;
  }

  const inlinePrompt = readArgValue('--prompt');
  if (inlinePrompt) return inlinePrompt.trim();
  fail('Provide --prompt, --prompt-file, or stdin.');
}

function parseReasoning(raw) {
  const value = (raw ?? 'medium').trim().toLowerCase();
  if (!['low', 'medium', 'high'].includes(value)) {
    fail(`Unsupported reasoning value: ${raw}`);
  }
  return value;
}

function selectExecutor(executor, reasoning) {
  if (executor === 'auto') return reasoning === 'high' ? 'codex' : 'glm-5';
  if (executor === 'glm-5' || executor === 'codex') return executor;
  fail(`Unsupported executor value: ${executor}`);
}

function buildConfig() {
  const repoDir = path.resolve(readArgValue('--dir') ?? process.cwd());
  const reasoning = parseReasoning(readArgValue('--reasoning'));
  const executor = selectExecutor((readArgValue('--executor') ?? 'auto').trim().toLowerCase(), reasoning);
  const prompt = readPrompt();
  const format = (readArgValue('--format') ?? 'json').trim().toLowerCase();
  const glmModel = readArgValue('--glm-model') ?? 'zai-coding-plan/glm-5';
  const codexModel = readArgValue('--codex-model') ?? 'gpt-5.3-codex';
  const run = hasFlag('--run');
  const verificationChecklist = [
    'Inspect the working tree and diff instead of trusting the worker summary.',
    'Run the narrowest relevant tests locally after the worker returns.',
    'Confirm the edited files still match the selected backlog slice.',
    'Verify affected docs, including docs/backlog.md, reflect what actually landed.',
  ];

  if (reasoning === 'high') {
    verificationChecklist.push('Run broader regression coverage because the slice touches a contract, entry point, or cross-module seam.');
  }

  return {
    repoDir,
    reasoning,
    executor,
    prompt,
    format,
    run,
    models: {
      'glm-5': glmModel,
      codex: codexModel,
    },
    verificationChecklist,
  };
}

function buildGlmCommand(config) {
  return [
    'opencode',
    'run',
    '--dir',
    config.repoDir,
    '--model',
    config.models['glm-5'],
    '--format',
    config.format,
    config.prompt,
  ];
}

function runGlm(config) {
  const command = buildGlmCommand(config);
  const [bin, ...argv] = command;
  const result = spawnSync(bin, argv, {
    cwd: config.repoDir,
    encoding: 'utf8',
    maxBuffer: 10 * 1024 * 1024,
  });

  if (result.error) {
    fail(`Failed to start opencode: ${result.error.message}`);
  }

  if (result.status !== 0) {
    process.stderr.write(result.stderr ?? '');
    process.stdout.write(result.stdout ?? '');
    process.exit(result.status ?? 1);
  }

  process.stdout.write(result.stdout ?? '');
}

const config = buildConfig();

if (config.executor === 'glm-5' && config.run) {
  runGlm(config);
  process.exit(0);
}

const payload = {
  executor: config.executor,
  reasoning: config.reasoning,
  repoDir: config.repoDir,
  verificationChecklist: config.verificationChecklist,
  prompt: config.prompt,
  glm: config.executor === 'glm-5'
    ? {
        model: config.models['glm-5'],
        command: buildGlmCommand(config),
        runnable: true,
      }
    : null,
  codex: config.executor === 'codex'
    ? {
        model: config.models.codex,
        runnable: false,
        handoff: {
          model: config.models.codex,
          reasoning_effort: config.reasoning,
          message: config.prompt,
        },
      }
    : null,
};

process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
