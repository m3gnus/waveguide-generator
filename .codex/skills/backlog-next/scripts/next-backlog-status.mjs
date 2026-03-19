import fs from 'node:fs';
import path from 'node:path';
import { execSync } from 'node:child_process';

const repoRoot = process.cwd();
const backlogPath = path.join(repoRoot, 'docs', 'backlog.md');
const args = new Set(process.argv.slice(2));
const asJson = args.has('--json');

function fail(message) {
  console.error(message);
  process.exit(1);
}

if (!fs.existsSync(backlogPath)) {
  fail(`Missing backlog file: ${backlogPath}`);
}

const backlog = fs.readFileSync(backlogPath, 'utf8');

function extractSectionBody(sectionText, headingPrefix) {
  const lines = sectionText.split('\n');
  const headingIndex = lines.findIndex((line) => line.startsWith(`## ${headingPrefix}`));
  if (headingIndex === -1) return '';
  const body = [];
  for (let index = headingIndex + 1; index < lines.length; index += 1) {
    const line = lines[index];
    if (line.startsWith('## ')) break;
    body.push(line);
  }
  return body.join('\n');
}

function extractBulletList(sectionBody) {
  const items = [];
  for (const rawLine of sectionBody.split('\n')) {
    const line = rawLine.trim();
    const bullet = line.match(/^- (.+)$/);
    if (bullet) items.push(bullet[1].trim());
  }
  return items;
}

function extractActiveBacklogSections(markdown) {
  const activeBody = extractSectionBody(markdown, 'Active Backlog');
  const lines = activeBody.split('\n');
  const sections = [];
  let current = null;

  for (const rawLine of lines) {
    const heading = rawLine.match(/^###\s+(.+)$/);
    const item = rawLine.match(/^- \[ \]\s+(.+)$/);
    const text = rawLine.trim();

    if (heading) {
      if (current) sections.push(current);
      current = { title: heading[1].trim(), items: [] };
      continue;
    }

    if (item && current) {
      current.items.push(item[1].trim());
      continue;
    }

    if (current && text && !text.startsWith('- [x]') && !text.startsWith('- [ ]') && current.items.length > 0) {
      const lastIndex = current.items.length - 1;
      current.items[lastIndex] = `${current.items[lastIndex]} ${text}`.trim();
    }
  }

  if (current) sections.push(current);
  return sections.filter((section) => section.items.length > 0);
}

const backlogSections = extractActiveBacklogSections(backlog);
const baselineNotes = extractBulletList(extractSectionBody(backlog, 'Current Baseline'));

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

function selectExecutor(reasoning) {
  return reasoning === 'high' ? 'codex' : 'glm-5';
}

function buildVerificationChecklist(reasoning) {
  const checklist = [
    'Inspect the working tree and diff instead of trusting the worker summary.',
    'Run the narrowest relevant tests locally after the worker returns.',
    'Confirm the edited files still match the selected backlog slice.',
    'Verify affected docs, including docs/backlog.md, reflect what actually landed.',
  ];

  if (reasoning === 'high') {
    checklist.push('Run broader regression coverage because the slice touches a contract, entry point, or cross-module seam.');
  }

  return checklist;
}

const backlogEmpty = backlogSections.length === 0;
const currentPriority = backlogEmpty ? null : backlogSections[0].title;
const currentPriorityTitle = backlogEmpty ? null : backlogSections[0].title;
const openItems = backlogEmpty ? [] : backlogSections[0].items;
const defaultReasoning = backlogEmpty ? 'low' : inferReasoning(currentPriorityTitle, openItems, baselineNotes);
const defaultExecutor = backlogEmpty ? 'glm-5' : selectExecutor(defaultReasoning);
const executorPolicy = {
  low: 'glm-5',
  medium: 'glm-5',
  high: 'codex',
};
const verificationChecklist = buildVerificationChecklist(defaultReasoning);
const prompt = backlogEmpty
  ? 'The active backlog in docs/backlog.md is empty. Reopen it only when a new requirement, blocker, or tracked follow-up needs implementation.'
  : [
      `Continue the current backlog in docs/backlog.md by executing one unfinished slice after another.`,
      `Ground on the active backlog priority and the recent commits below, and rerun this status check after each committed slice.`,
      `Use GLM-5 via opencode for low/medium slices and Codex for high-complexity slices (${defaultReasoning} => ${defaultExecutor} by default for the current priority).`,
      `Treat GLM as an implementation worker only: inspect the diff yourself, rerun targeted tests locally, escalate to broader tests or Codex when the slice touches contracts or fails verification.`,
      `Pick the smallest coherent unfinished slice, run targeted tests first, update affected docs including docs/backlog.md when priorities change, commit the completed and verified slice, then continue until the backlog is empty or blocked.`
    ].join(' ');

const payload = {
  backlogPath: 'docs/backlog.md',
  backlogEmpty,
  currentPriority,
  currentPriorityTitle,
  recentCommits,
  openItems,
  baselineNotes,
  defaultReasoning,
  defaultExecutor,
  executorPolicy,
  verificationChecklist,
  prompt,
};

if (asJson) {
  process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
  process.exit(0);
}

if (backlogEmpty) {
  console.log(`# Backlog Status`);
  console.log(``);
  console.log(`Current priority: none`);
  console.log(`Default reasoning: ${defaultReasoning}`);
  console.log(`Default executor: ${defaultExecutor}`);
  console.log(``);
  console.log(`Recent commits:`);
  for (const commit of recentCommits) console.log(`- ${commit}`);
  console.log(``);
  console.log(`Open items:`);
  console.log(`- none`);
  console.log(``);
  console.log(`Baseline notes:`);
  for (const note of baselineNotes) console.log(`- ${note}`);
  console.log(``);
  console.log(`Verification checklist:`);
  for (const step of verificationChecklist) console.log(`- ${step}`);
  console.log(``);
  console.log(`Suggested fresh-session prompt:`);
  console.log(prompt);
  process.exit(0);
}

console.log(`# Backlog Status`);
console.log(``);
console.log(`Current priority: ${currentPriority}`);
console.log(`Default reasoning: ${defaultReasoning}`);
console.log(`Default executor: ${defaultExecutor}`);
console.log(``);
console.log(`Recent commits:`);
for (const commit of recentCommits) console.log(`- ${commit}`);
console.log(``);
console.log(`Open items:`);
for (const task of openItems) console.log(`- ${task}`);
console.log(``);
console.log(`Baseline notes:`);
for (const note of baselineNotes) console.log(`- ${note}`);
console.log(``);
console.log(`Verification checklist:`);
for (const step of verificationChecklist) console.log(`- ${step}`);
console.log(``);
console.log(`Suggested fresh-session prompt:`);
console.log(prompt);
