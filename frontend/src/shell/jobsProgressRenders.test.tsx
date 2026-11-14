/**
 * What a running solve's progress events cost the rest of the interface.
 *
 * Mounts the real shell panels (top bar, parameters, viewport, results, jobs,
 * status bar) under the jobs coordinator with a realistic job list -- many
 * finished runs and one running solve -- and drives progress and stage events
 * through the jobs socket's own event path. Two measures:
 *  - each panel sits in a React Profiler: commits that reached the panel's
 *    subtree, and the time React spent rendering it;
 *  - a devtools-hook fiber walk counts how often each named component's own
 *    body ran, so a panel's body is told apart from a small child in it.
 *
 * The benchmark case prints its table and runs only with WG_BENCH set:
 *   WG_BENCH=1 npx vitest run src/shell/jobsProgressRenders.test.tsx --silent=false --reporter=verbose
 *
 * The body count relies on React internals, not public API: a stand-in
 * `__REACT_DEVTOOLS_GLOBAL_HOOK__` (inject/onCommitFiberRoot), the fiber
 * fields tag/type/flags/alternate/child/sibling, the function fiber tags
 * (0 function, 11 forwardRef, 15 simple memo; 14 memo wrapper skipped) and
 * the PerformedWork flag (1). A React major upgrade may need it adjusted.
 */
import { act, Profiler, type ProfilerOnRenderCallback, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Installed before react-dom loads, the way React DevTools attaches. A fiber
// ran its body in a commit when it is new to this tree (not carried over
// untouched from the previous commit) and React marked it PerformedWork.
const bodies = vi.hoisted(() => {
  type Fiber = { tag: number; type: unknown; flags: number; alternate: Fiber | null; child: Fiber | null; sibling: Fiber | null };
  const counts = new Map<string, number>();
  let previous = new Set<Fiber>();
  // Function, forwardRef and simple-memo bodies; a memo() wrapper (14) has its body as a child.
  const FUNCTION_TAGS = new Set([0, 11, 15]);
  const PERFORMED_WORK = 1;
  const nameOf = (fiber: Fiber): string | null => {
    if (!FUNCTION_TAGS.has(fiber.tag)) return null;
    const type = fiber.type as { name?: string; displayName?: string; render?: { name?: string } } | null;
    return type?.displayName || type?.name || type?.render?.name || null;
  };
  (globalThis as Record<string, unknown>).__REACT_DEVTOOLS_GLOBAL_HOOK__ = {
    supportsFiber: true,
    renderers: new Map(),
    inject: () => 1,
    checkDCE: () => undefined,
    onScheduleFiberRoot: () => undefined,
    onCommitFiberUnmount: () => undefined,
    onPostCommitFiberRoot: () => undefined,
    onCommitFiberRoot(_renderer: number, root: { current: Fiber }) {
      const seen = new Set<Fiber>();
      const stack: Fiber[] = [root.current];
      while (stack.length) {
        const fiber = stack.pop()!;
        seen.add(fiber);
        const name = nameOf(fiber);
        if (name && !previous.has(fiber) && (fiber.alternate === null || (fiber.flags & PERFORMED_WORK))) {
          counts.set(name, (counts.get(name) ?? 0) + 1);
        }
        if (fiber.sibling) stack.push(fiber.sibling);
        if (fiber.child) stack.push(fiber.child);
      }
      previous = seen;
    },
  };
  return { counts, of: (name: string) => counts.get(name) ?? 0, reset: () => counts.clear() };
});

import type { DecodedFrame } from '../api/frame';
import type { PreviewSnapshot } from '../api/previewSocket';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import { compareSelection, provisionalResults, resultsCache } from '../api/results';
import { preferencesStore } from '../prefs/preferences';
import { resetDesignStore, serializeDesign, useDesignStore } from '../stores/design';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';

vi.mock('@react-three/fiber', () => ({ Canvas: () => null, useFrame: vi.fn(), useThree: vi.fn() }));
vi.mock('@react-three/drei', () => ({ GizmoHelper: () => null, GizmoViewport: () => null, OrbitControls: () => null }));

const frame: DecodedFrame = {
  header: {
    v: 1, kind: 'preview', epoch: 4, seq: 9, designRevision: 1, lod: 'fine', sections: [],
    surfaces: [{ role: 'horn.inner', positions: 'horn.positions', normals: 'horn.normals', indices: 'horn.indices', shading: 'smooth', normalMethod: 'analytic-parametric' }],
  },
  sections: {
    'horn.positions': new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]),
    'horn.normals': new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]),
    'horn.indices': new Uint32Array([0, 1, 2]),
  },
};
const previewSnapshot: PreviewSnapshot = {
  connection: 'connected', epoch: 4, frame, displayedRevision: 1, lastValidRevision: 1,
  stale: false, dropped: 0, error: null, errorFields: null, errorRevision: null,
};
vi.mock('../api/previewSocket', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/previewSocket')>(),
  previewSocket: {
    subscribe: () => () => undefined,
    getSnapshot: () => previewSnapshot,
    refresh: vi.fn(),
    setCurvatureWanted: vi.fn(),
  },
}));

import { resetWebGLProbe } from '../viewport/ViewportCanvas';
import { Viewport } from '../viewport/Viewport';
import { JobsCoordinator } from './JobsCoordinator';
import { TopBar } from './TopBar';
import { ResultsPanel } from './ResultsPanel';
import { JobsPanel } from './JobsPanel';
import { StatusBar } from './StatusBar';
import { ParamPanel } from '../design/ParamPanel';

/** Set WG_BENCH to run the benchmark case (the frontend is typed without Node's globals). */
const BENCH = Boolean((globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env.WG_BENCH);
const FINISHED = 40;
const EVENTS = 200;
const FREQUENCIES = EVENTS / 2;
const RUNNING = 'running-job';

function job(id: string, index: number, status: JobItem['status']): JobItem {
  const at = new Date(Date.UTC(2026, 7, 8, 0, index)).toISOString();
  return {
    id, run_number: index + 1, parent_job_id: null,
    archive_folder: 'server-folder', archive_run: `${index + 1}_run-${id}`,
    label: id, status, progress: status === 'complete' ? 1 : 0, stage: null,
    stage_message: null, created_at: at, queued_at: at, started_at: at,
    completed_at: status === 'complete' ? at : null, config_summary: {}, solve_options: {} as JobItem['solve_options'],
    has_results: status === 'complete', has_mesh_artifact: false, error_message: null,
    cancellation_requested: false, mesh_stats: null,
    script_snapshot: { version: 1, design: serializeDesign(useDesignStore.getState().design) },
    design_revision: 1, polar_grid: {}, rating: null, exported_files: [],
    auto_export_completed_at: null, auto_export_formats: {}, raw_results_file: null,
    mesh_artifact_file: null, log_tail: [],
  };
}

type EventType = 'stage' | 'progress' | 'log' | 'completed';
const manager = jobsSocket as unknown as {
  snapshot: JobsSnapshot;
  listeners: Set<() => void>;
  onEvent(message: { v: 1; kind: 'event'; cursor: number; jobId: string; type: EventType; payload?: Record<string, unknown> }): void;
};

function publishJobs(jobs: JobItem[]): void {
  manager.snapshot = { connection: 'connected', epoch: 1, cursor: 1, jobs, error: null };
  manager.listeners.forEach((listener) => listener());
}

function send(type: EventType, payload?: Record<string, unknown>): void {
  manager.onEvent({ v: 1, kind: 'event', cursor: manager.snapshot.cursor! + 1, jobId: RUNNING, type, payload });
}

/** Event `index` of a running solve: per frequency, a stage line with the count, then progress. */
function fireSolveEvent(index: number): void {
  const done = Math.floor(index / 2) + 1;
  if (index % 2 === 0) {
    send('stage', { stage: 'solving', message: `Frequency ${done} of ${FREQUENCIES}`, frequency_progress: { done, total: FREQUENCIES } });
  } else {
    send('progress', { progress: done / FREQUENCIES });
  }
}

const PANELS = ['TopBar', 'ParamPanel', 'Viewport', 'ResultsPanel', 'JobsPanel', 'StatusBar'] as const;
type Panel = typeof PANELS[number];
/** Component bodies reported by the fiber count. */
const BODIES = ['JobsCoordinator', 'TopBar', 'SolveActions', 'AttentionNotices', 'ParamPanel', 'Viewport',
  'ResultsPanel', 'CurrentModelStatus', 'JobsPanel', 'JobCard', 'SolveProgressView', 'StatusBar'] as const;

interface Tally { commits: number; ms: number }
let tallies: Record<Panel, Tally>;
const onRender: ProfilerOnRenderCallback = (id, _phase, actualDuration) => {
  const tally = tallies[id as Panel];
  tally.commits += 1;
  tally.ms += actualDuration;
};
function Measured({ id, children }: { id: Panel; children: ReactNode }) {
  return <Profiler id={id} onRender={onRender}>{children}</Profiler>;
}
function resetTallies(): void {
  tallies = Object.fromEntries(PANELS.map((panel) => [panel, { commits: 0, ms: 0 }])) as Record<Panel, Tally>;
  bodies.reset();
}

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

function Shell() {
  return (
    <QueryClientProvider client={queryClient}>
      <JobsCoordinator>
        <Measured id="TopBar"><TopBar onResetLayout={() => undefined}/></Measured>
        <Measured id="ParamPanel"><ParamPanel tab="geometry"/></Measured>
        <Measured id="Viewport"><Viewport/></Measured>
        <Measured id="ResultsPanel"><ResultsPanel/></Measured>
        <Measured id="JobsPanel"><JobsPanel/></Measured>
        <Measured id="StatusBar"><StatusBar/></Measured>
      </JobsCoordinator>
    </QueryClientProvider>
  );
}

describe('a running solve\'s job events', () => {
  let host: HTMLDivElement;
  let root: Root;
  let getContext: ReturnType<typeof vi.spyOn>;

  beforeEach(async () => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetDesignStore(); workspaceModeStore.setMode('parametric'); importedMeshStore.clear();
    resultsCache.clear(); provisionalResults.clear(); compareSelection.clear(); preferencesStore.resetForTests();
    Object.defineProperty(globalThis, 'WebGL2RenderingContext', { configurable: true, value: class WebGL2RenderingContext {} });
    getContext = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as WebGL2RenderingContext);
    resetWebGLProbe();
    vi.spyOn(jobsSocket, 'start').mockImplementation(() => undefined);
    vi.spyOn(jobsSocket, 'stop').mockImplementation(() => undefined);
    vi.spyOn(jobsSocket, 'refresh').mockResolvedValue(undefined);
    // Requests stay pending: what is measured is the job events, not loading.
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => undefined)));
    publishJobs([
      ...Array.from({ length: FINISHED }, (_, index) => job(`done-${index}`, index, 'complete')),
      job(RUNNING, FINISHED, 'running'),
    ].reverse());
    // The user is looking at an earlier run while the new one solves.
    compareSelection.setPrimary('done-0');
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    resetTallies();
    await act(async () => { root.render(<Shell/>); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
    resetTallies();
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    publishJobs([]);
    compareSelection.clear();
    queryClient.clear();
    getContext.mockRestore();
    delete (globalThis as Partial<typeof globalThis>).WebGL2RenderingContext;
    resetWebGLProbe();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it.skipIf(!BENCH)(`benchmark: ${EVENTS} events for one running job`, () => {
    const started = performance.now();
    for (let index = 0; index < EVENTS; index += 1) act(() => fireSolveEvent(index));
    const wall = performance.now() - started;
    const rows = PANELS.map((panel) => `${panel.padEnd(13)} commits ${String(tallies[panel].commits).padStart(4)}  ${tallies[panel].ms.toFixed(1).padStart(8)} ms  ${(tallies[panel].ms / EVENTS).toFixed(3)} ms/event`);
    const total = PANELS.reduce((sum, panel) => sum + tallies[panel].ms, 0);
    const counts = BODIES.map((name) => `${name} ${bodies.of(name)}`).join(', ');
    console.log(`[jobs-progress-bench] ${EVENTS} events, ${FINISHED} finished runs + 1 running\n${rows.join('\n')}\n`
      + `panels total ${total.toFixed(1)} ms (${(total / EVENTS).toFixed(3)} ms/event); wall ${wall.toFixed(1)} ms (${(wall / EVENTS).toFixed(3)} ms/event)\n`
      + `body renders: ${counts}`);
    expect(manager.snapshot.jobs.find((item) => item.id === RUNNING)?.progress).toBe(1);
  });

  it('progress, stage and log events re-render only the views that draw the solve\'s progress', () => {
    act(() => fireSolveEvent(0));
    act(() => fireSolveEvent(1));
    act(() => send('log', { lines: ['frequency 1 solved'] }));
    resetTallies();
    for (let index = 2; index < 42; index += 1) act(() => fireSolveEvent(index));
    act(() => send('log', { lines: ['frequency 21 solved'] }));

    for (const name of ['JobsCoordinator', 'TopBar', 'SolveActions', 'AttentionNotices', 'ParamPanel', 'Viewport', 'ResultsPanel']) {
      expect(bodies.of(name), name).toBe(0);
    }
    expect(tallies.Viewport.commits).toBe(0);
    expect(tallies.ParamPanel.commits).toBe(0);
    // The Jobs panel's card, the current model's status in Results and the
    // status bar's solve line draw the progress, on every event.
    expect(bodies.of('JobCard')).toBe(41);
    expect(bodies.of('CurrentModelStatus')).toBe(41);
    expect(bodies.of('StatusBar')).toBe(41);
    const bar = host.querySelector('.job-card [role="progressbar"]')!;
    expect(bar.getAttribute('aria-valuenow')).toBe('21');
    expect(host.querySelector('.job-card .job-stage')?.textContent).toContain(`Frequency 21 of ${FREQUENCIES}`);
  });

  it('a change the panels show still re-renders them', () => {
    act(() => fireSolveEvent(0));
    resetTallies();
    act(() => send('completed'));
    expect(bodies.of('JobsCoordinator')).toBeGreaterThan(0);
    expect(bodies.of('Viewport')).toBeGreaterThan(0);
    expect(bodies.of('ResultsPanel')).toBeGreaterThan(0);
    expect(bodies.of('TopBar')).toBeGreaterThan(0);
    expect(bodies.of('JobCard')).toBeGreaterThan(0);
  });
});
