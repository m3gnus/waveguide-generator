import { solverNames, type SolverName } from '../solver/types';
import { runBench, type BenchState, type CorpusVariant } from '../solver/bench';
declare global { interface Window { __sketchBench: BenchState } }
window.__sketchBench = { done: false, results: [], startedAt: '', userAgent: navigator.userAgent, notes: [] };
const style = document.createElement('style');
style.textContent = 'body{font:14px system-ui;margin:2rem;color:#17202a}table{border-collapse:collapse}th,td{border:1px solid #bbb;padding:.5rem;text-align:right}caption{text-align:left;padding:1rem 0}pre{white-space:pre-wrap}button{padding:.5rem}';
document.head.append(style);
const run = document.querySelector<HTMLButtonElement>('#run')!, download = document.querySelector<HTMLButtonElement>('#download')!;
function render(state: BenchState) {
  window.__sketchBench = state;
  const tbody = document.querySelector('tbody')!; tbody.replaceChildren();
  for (const r of state.results) {
    const tr = document.createElement('tr');
    for (const value of [r.solver, r.n, r.variant, r.status, r.coldStartMs, r.setupMs, r.frames, r.totalMs?.p50, r.totalMs?.p95,
      r.totalMs?.p99, r.totalMs?.max, r.diagnosisMs, r.failedFrames, r.branchFlips, r.memory?.source,
      r.memory?.before, r.memory?.after, r.memory?.cycles]) {
      const td = document.createElement('td'); td.textContent = value === undefined ? '' : typeof value === 'number'
        ? Number.isInteger(value) ? String(value) : value.toFixed(3) : String(value); tr.append(td);
    }
    tbody.append(tr);
  }
  document.querySelector('#status')!.textContent = state.done ? 'Done; inspect timeout/error rows before comparing.' : 'Running…';
  document.querySelector('#details')!.textContent = JSON.stringify({ notes: state.notes,
    details: state.results.map(r => ({ solver: r.solver, n: r.n, variant: r.variant, frameSamples: r.frameSamples, reason: r.reason, branchCases: r.branchCases })) }, null, 2);
}
run.onclick = async () => {
  const params = new URLSearchParams(location.search), solver = params.get('solver'), n = params.get('n'), variant = params.get('variant');
  if ((solver && !solverNames.includes(solver as SolverName)) || (n && !['8', '60', '256'].includes(n))
    || (variant && !['underconstrained', 'constrained'].includes(variant))) {
    document.querySelector('#status')!.textContent = 'Invalid solver, n or variant filter'; return;
  }
  run.disabled = true;
  try { await runBench(render, () => new Worker(new URL('./worker.ts', import.meta.url), { type: 'module' }),
    { solver: solver as SolverName || undefined, n: n ? Number(n) : undefined, variant: variant as CorpusVariant || undefined }); }
  finally { run.disabled = false; }
};
download.onclick = () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(window.__sketchBench, null, 2)], { type: 'application/json' }));
  const a = document.createElement('a'); a.href = url; a.download = 'sketch-bench.json'; a.click(); URL.revokeObjectURL(url);
};
// Automation can navigate with ?autorun=1, then wait for window.__sketchBench.done.
if (new URLSearchParams(location.search).has('autorun')) run.click();
