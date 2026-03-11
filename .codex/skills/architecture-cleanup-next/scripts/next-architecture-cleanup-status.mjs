import fs from 'node:fs';
import path from 'node:path';
import { execSync } from 'node:child_process';

const repoRoot = process.cwd();
const planPath = path.join(repoRoot, 'docs', 'ARCHITECTURE_CLEANUP_PLAN.md');
const args = new Set(process.argv.slice(2));
const asJson = args.has('--json');

function fail(message) {
  console.error(message);
  process.exit(1);
}

if (!fs.existsSync(planPath)) {
  fail(`Missing plan file: ${planPath}`);
}

const plan = fs.readFileSync(planPath, 'utf8');
const currentPhaseMatch = plan.match(/- Current phase:\s*(Phase\s+\d+)\s*\(([^)]+)\)/i);
if (!currentPhaseMatch) {
  fail('Could not find current phase in docs/ARCHITECTURE_CLEANUP_PLAN.md');
}

const currentPhase = currentPhaseMatch[1].trim();
const currentPhaseStatus = currentPhaseMatch[2].trim();
const phaseNumber = currentPhase.match(/\d+/)?.[0];
if (!phaseNumber) {
  fail(`Could not parse phase number from ${currentPhase}`);
}

const phaseHeaderRegex = new RegExp(`^## ${currentPhase.replace(/[-/\\^$*+?.()|[\]{}]/g, '\\$&')}:\\s*(.+)$`, 'm');
const phaseHeaderMatch = plan.match(phaseHeaderRegex);
if (!phaseHeaderMatch) {
  fail(`Could not find section header for ${currentPhase}`);
}
const phaseTitle = phaseHeaderMatch[1].trim();
const phaseStart = phaseHeaderMatch.index ?? 0;
const remainingPlan = plan.slice(phaseStart);
const nextPhaseMatch = remainingPlan.slice(1).match(/^## Phase\s+\d+:/m);
const phaseSection = nextPhaseMatch ? remainingPlan.slice(0, nextPhaseMatch.index + 1) : remainingPlan;

function extractSectionBody(sectionText, headingPrefix) {
  const lines = sectionText.split('\n');
  const headingIndex = lines.findIndex((line) => line.startsWith(`### ${headingPrefix}`));
  if (headingIndex === -1) return '';
  const body = [];
  for (let index = headingIndex + 1; index < lines.length; index += 1) {
    const line = lines[index];
    if (line.startsWith('### ') || line.startsWith('## ')) break;
    body.push(line);
  }
  return body.join('\n');
}

function extractStructuredList(sectionBody) {
  const items = [];
  let current = null;

  for (const rawLine of sectionBody.split('\n')) {
    const line = rawLine.trimEnd();
    const numbered = line.match(/^\s*(\d+)\.\s+(.*)$/);
    const nestedBullet = line.match(/^\s+-\s+(.*)$/);
    const plainContinuation = line.trim();

    if (numbered) {
      if (current) items.push(current.trim());
      current = numbered[2].trim();
      continue;
    }

    if (nestedBullet && current) {
      current = `${current} ${nestedBullet[1].trim()}`;
      continue;
    }

    if (plainContinuation && current && !plainContinuation.endsWith(':')) {
      current = `${current} ${plainContinuation}`;
    }
  }

  if (current) items.push(current.trim());
  return items;
}

const phaseTasks = extractStructuredList(extractSectionBody(phaseSection, 'Tasks'));
const implementationNotes = extractStructuredList(extractSectionBody(phaseSection, 'Implementation Notes'));

function runGit(command) {
  try {
    return execSync(command, {
      cwd: repoRoot,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim();
  } catch {
    return '';
  }
}

const recentCommits = runGit('git log --oneline -n 5')
  .split('\n')
  .map((line) => line.trim())
  .filter(Boolean);

function inferReasoning(title, tasks, notes) {
  const haystack = `${title}\n${tasks.join('\n')}\n${notes.join('\n')}`.toLowerCase();
  if (/public api|cross-module|contract|compatibility|circular|orchestration rewrite|backend glue|re-export/.test(haystack)) {
    return 'high';
  }
  if (/refactor|extract|controller|service|centralize|module|workspace|state/.test(haystack)) {
    return 'medium';
  }
  return 'low';
}

const defaultReasoning = inferReasoning(phaseTitle, phaseTasks, implementationNotes);
const prompt = [
  `Do the next unfinished slice in docs/ARCHITECTURE_CLEANUP_PLAN.md.`,
  `Ground on the current phase summary and the recent commits below.`,
  `Use Codex 5.3 subagents and choose reasoning effort from slice complexity (${defaultReasoning} by default, adjust if the chosen slice is smaller or larger).`,
  `Pick the smallest coherent unfinished slice, run targeted tests first, update affected docs including the plan notes, and commit the completed slice.`
].join(' ');

const payload = {
  currentPhase,
  currentPhaseStatus,
  phaseNumber: Number(phaseNumber),
  phaseTitle,
  recentCommits,
  phaseTasks,
  implementationNotes,
  defaultReasoning,
  prompt,
};

if (asJson) {
  process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
  process.exit(0);
}

console.log(`# Architecture Cleanup Status`);
console.log(``);
console.log(`Current phase: ${currentPhase} (${currentPhaseStatus})`);
console.log(`Title: ${phaseTitle}`);
console.log(`Default reasoning: ${defaultReasoning}`);
console.log(``);
console.log(`Recent commits:`);
for (const commit of recentCommits) console.log(`- ${commit}`);
console.log(``);
console.log(`Current phase tasks:`);
for (const task of phaseTasks) console.log(`- ${task}`);
console.log(``);
console.log(`Implementation notes:`);
for (const note of implementationNotes) console.log(`- ${note}`);
console.log(``);
console.log(`Suggested fresh-session prompt:`);
console.log(prompt);
