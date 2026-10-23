import { afterEach, describe, expect, it, vi } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { preferencesStore } from '../prefs/preferences';
import { resetSolveOptionsStore, useImportedSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { archiveRunToWorkspace } from '../results/exporters';
import type { ResultPayload } from '../results/types';
import { exportRunAutomatically } from './autoExportRun';

afterEach(() => resetSolveOptionsStore());

/**
 * The run archive and automatic export, both on, against one Workspace.
 *
 * The fake below applies the server's `write-export` rules
 * (`server/workspace/api.py::_write_export_sync`) to an in-memory folder:
 * `merge_identical` refuses the whole request over one differing file,
 * `confirm` names every differing file and writes nothing, and both skip a
 * byte-identical one. Installed qualification of 14e62fc2 found the two
 * features sharing `<design>/<run>`: archive first blocked every export
 * format, STEP included, and export first blocked the archive.
 */

const result = {
  frequencies: [100, 200],
  spl_on_axis: { frequencies: [100, 200], spl: [90, 91], phase_degrees: [0, 10] },
  di: { frequencies: [100, 200], di: [3, 4] },
  impedance: { frequencies: [100, 200], real: [1, 1.1], imaginary: [0, -0.5] },
  directivity: { horizontal: [[[-90, -12], [0, 0], [30, -6]], [[-90, -10], [0, 0], [30, -6]]] },
  result_kind: 'parametric',
  result_contract_version: 1,
  client_request_id: null,
  client_metadata: {},
  metadata: { directivity: { normalization_angle_degrees: 5 } },
} as unknown as ResultPayload;

function completedJob(id: string): JobItem {
  return {
    id, run_number: 9, parent_job_id: null, status: 'complete', progress: 1,
    stage: null, stage_message: null, created_at: '2026-10-10T22:00:00Z', queued_at: '2026-10-10T22:00:00Z',
    started_at: '2026-10-10T22:00:01Z', completed_at: '2026-10-10T22:02:00Z',
    config_summary: { formula_type: 'OSSE' }, solve_options: {} as JobItem['solve_options'],
    has_results: true, has_mesh_artifact: true, persistence_warnings: [], label: null,
    error_message: null, cancellation_requested: false, mesh_stats: null,
    script_snapshot: { version: 1, design: { formula: 'OSSE', L: 120, a: 45, a0: 10, r0: 12.7, k: 1 } },
    design_revision: 1, polar_grid: {}, rating: null, exported_files: [], auto_export_completed_at: null,
    auto_export_formats: {}, archived_at: null, raw_results_file: null, mesh_artifact_file: null, log_tail: [],
  } as unknown as JobItem;
}

const ROOT = 'D:\\Waveguide Generator\\runs';
const absolute = (relative: string) => `${ROOT}\\${relative.replaceAll('/', '\\')}`;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
});

function workspace(jobId: string) {
  const files = new Map<string, string>();
  const writes: Array<{ subdirectory: string; existing: string; names: string[]; status: number }> = [];
  const fetcher = vi.fn<typeof fetch>(async (input, init) => {
    const path = String(input);
    if (path === `/api/results/${jobId}`) return json(result);
    if (path === `/api/jobs/${jobId}/archive-snapshot`) return json({
      schema_version: 1, results: result, results_sha256: 'a'.repeat(64),
      mesh_artifact: 'retained mesh', pressure_bases: [], radiation_impedance: null,
    });
    if (path === '/api/export/step') return new Response('step bytes', {
      headers: { 'Content-Disposition': 'attachment; filename="9_OSSE.step"' },
    });
    if (path === '/api/design/serialize') return json({ text: 'cfg text' });
    if (path !== '/api/workspace/write-export') return new Response('not found', { status: 404 });
    const form = init!.body as FormData;
    const subdirectory = String(form.get('subdirectory'));
    const existing = String(form.get('existing'));
    const names = form.getAll('relative_path').map(String);
    const blobs = form.getAll('file') as Blob[];
    const members = await Promise.all(names.map(async (name, index) => ({
      path: `${subdirectory}/${name}`, text: await blobs[index].text(),
    })));
    // Case-insensitive, as on Windows: a name that differs only in case is
    // the same file, and the server reports it with its on-disk casing.
    const onDisk = (member: string) => [...files.keys()].find((key) => key.toLowerCase() === member.toLowerCase());
    const differing = members
      .filter(({ path: member, text }) => onDisk(member) !== undefined && files.get(onDisk(member)!) !== text)
      .map(({ path: member, text }) => ({ path: onDisk(member)!, text }));
    const record = (status: number) => writes.push({ subdirectory, existing, names, status });
    if (differing.length && existing === 'merge_identical') {
      record(409);
      return json({ detail: `Export file already exists with different content: ${absolute(differing[0].path)}` }, 409);
    }
    if (differing.length && existing === 'confirm') {
      record(409);
      return json({
        code: 'export_collision', detail: `${differing.length} file(s) would be replaced`,
        directory: absolute(subdirectory), paths: differing.map(({ path: member }) => absolute(member)),
      }, 409);
    }
    record(200);
    members.forEach(({ path: member, text }) => files.set(onDisk(member) ?? member, text));
    return json({ directory: absolute(subdirectory), files: members.map(({ path: member }) => absolute(member)), replaced: [] });
  });
  return { files, writes, fetcher };
}

const AUTO_FORMATS = ['step', 'mwg_config', 'csv', 'json', 'html_report', 'txt'] as const;

describe('run archive and automatic export in one Workspace', () => {
  it.each(['parametric', 'imported'] as const)('automatically exports legacy %s runs with their live kind reference', async (kind) => {
    const job = completedJob(`legacy-${kind}`);
    if (kind === 'imported') job.config_summary = { geometry_type: 'imported' };
    useSolveOptionsStore.getState().updatePolar({ normAngle: 10 });
    useImportedSolveOptionsStore.getState().updatePolar({ normAngle: 30 });
    const { files, fetcher } = workspace(job.id);
    const exported = await exportRunAutomatically(job, ['json'], { preferences: preferencesStore.getSnapshot(), fetcher });
    expect(exported.failures).toEqual([]);
    const written = JSON.parse([...files.values()][0]);
    expect(written.results.metadata.directivity.normalization_angle_degrees).toBe(kind === 'imported' ? 30 : 10);
  });

  it.each(['cad', 'file'] as const)('never regenerates the anchor of a %s run during automatic export', async kind => {
    const job = completedJob(`imported-${kind}`);
    job.config_summary = { geometry_type: 'imported', ...(kind === 'file' ? { import_origin: 'mesh-file' } : {}) };
    job.cad_source = { ingest_id: 'B', model_id: 'model-B' } as JobItem['cad_source'];
    const { files, fetcher } = workspace(job.id);
    const exported = await exportRunAutomatically(job, ['step', 'stl', 'mwg_config', 'fusion_csv', 'csv'], {
      preferences: preferencesStore.getSnapshot(), fetcher,
    });
    expect(exported.failures.map(failure => failure.format)).toEqual(['step', 'stl', 'mwg_config', 'fusion_csv']);
    expect(exported.failures.every(failure => failure.reason.includes('imported models') || failure.reason.includes('Imported models'))).toBe(true);
    expect(exported.files).toHaveLength(1);
    expect([...files.keys()].every(path => path.endsWith('.csv'))).toBe(true);
    expect(fetcher.mock.calls.some(([path]) => String(path).startsWith('/api/export/') || path === '/api/design/serialize')).toBe(false);
  });

  it('automatically exports the recorded run normalization despite another imported draft', async () => {
    const job = completedJob('recorded-angle');
    job.solve_options = { ...job.solve_options, polar_config: { norm_angle: 0 } } as JobItem['solve_options'];
    useImportedSolveOptionsStore.getState().updatePolar({ normAngle: 30 });
    const { files, fetcher } = workspace(job.id);
    const exported = await exportRunAutomatically(job, ['json'], { preferences: preferencesStore.getSnapshot(), fetcher });
    expect(exported.failures).toEqual([]);
    const written = JSON.parse([...files.values()][0]);
    expect(written.results.metadata.directivity.normalization_angle_degrees).toBe(0);
  });

  it('archive first: every automatic format is still written, and the archive is untouched', async () => {
    const job = completedJob('archive-first');
    const { files, writes, fetcher } = workspace(job.id);
    const preferences = preferencesStore.getSnapshot();

    await archiveRunToWorkspace(job, preferences, fetcher);
    const archived = new Map(files);
    expect(archived.get('OSSE/9_OSSE/9_OSSE.json')).toBeDefined();

    const exported = await exportRunAutomatically(job, [...AUTO_FORMATS], { preferences, fetcher });

    expect(exported.failures).toEqual([]);
    expect(exported.files).toEqual(expect.arrayContaining([
      absolute('OSSE/9_OSSE/exports/9_OSSE.step'),
      absolute('OSSE/9_OSSE/exports/9_OSSE.json'),
      absolute('OSSE/9_OSSE/exports/9_OSSE_report.html'),
    ]));
    archived.forEach((text, path) => expect(files.get(path)).toBe(text));
    expect(writes.every(({ status }) => status === 200)).toBe(true);
  });

  it('export first: the archive completes in the run folder beside the exports', async () => {
    const job = completedJob('export-first');
    const { files, writes, fetcher } = workspace(job.id);
    const preferences = preferencesStore.getSnapshot();

    const exported = await exportRunAutomatically(job, [...AUTO_FORMATS], { preferences, fetcher });
    expect(exported.failures).toEqual([]);
    const before = new Map(files);

    const archived = await archiveRunToWorkspace(job, preferences, fetcher);

    expect(archived).toEqual(expect.arrayContaining([
      absolute('OSSE/9_OSSE/9_OSSE.json'),
      absolute('OSSE/9_OSSE/9_OSSE_report.html'),
      absolute('OSSE/9_OSSE/run.json'),
    ]));
    before.forEach((text, path) => expect(files.get(path)).toBe(text));
    expect([...files.keys()].some((path) => path.includes('/archive/'))).toBe(false);
    expect(writes.every(({ status }) => status === 200)).toBe(true);
  });

  it('writes the other formats when one is refused, and reports that one with its reason', async () => {
    const job = completedJob('one-refused');
    const { files, writes, fetcher } = workspace(job.id);
    // A JSON an earlier attempt left wherever automatic export writes, then or now.
    files.set('OSSE/9_OSSE/9_OSSE.json', 'an export from an earlier attempt');
    files.set('OSSE/9_OSSE/exports/9_OSSE.json', 'an export from an earlier attempt');

    const exported = await exportRunAutomatically(job, ['step', 'json', 'csv'], {
      preferences: preferencesStore.getSnapshot(), fetcher,
    });

    expect(exported.failures).toEqual([{
      format: 'json',
      reason: expect.stringContaining('already exists with different content'),
      blocked: true,
    }]);
    expect(files.get('OSSE/9_OSSE/exports/9_OSSE.step')).toBe('step bytes');
    expect(files.get('OSSE/9_OSSE/exports/9_OSSE.csv')).toBeDefined();
    expect(files.get('OSSE/9_OSSE/exports/9_OSSE.json')).toBe('an export from an earlier attempt');
    expect(writes.map(({ names, status }) => [names, status])).toEqual([
      [['9_OSSE.step'], 200], [['9_OSSE.json'], 409], [['9_OSSE.csv'], 200],
    ]);
  });

  it('archives a run folder an earlier version exported into without refusing or replacing a file', async () => {
    const job = completedJob('legacy-folder');
    const { files, fetcher } = workspace(job.id);
    const preferences = preferencesStore.getSnapshot();
    // What an earlier version left when automatic export won the race: its
    // own JSON and report, and a mesh, under the archive's names.
    files.set('OSSE/9_OSSE/9_OSSE.json', 'legacy export json');
    // Exported under a label later relabelled in case only.
    files.set('OSSE/9_OSSE/9_osse_report.html', 'legacy export report');
    files.set('OSSE/9_OSSE/9_OSSE.msh', 'a different mesh');

    await archiveRunToWorkspace(job, preferences, fetcher);

    expect(files.get('OSSE/9_OSSE/9_OSSE.json')).toBe('legacy export json');
    expect(files.get('OSSE/9_OSSE/9_osse_report.html')).toBe('legacy export report');
    expect(files.get('OSSE/9_OSSE/9_OSSE.msh')).toBe('a different mesh');
    // The archive's copy, stamped with the run's completion time.
    expect(JSON.parse(files.get('OSSE/9_OSSE/archive/9_OSSE.json')!)).toMatchObject({
      timestamp: '2026-10-10T22:02:00.000Z', results: { frequencies: [100, 200] },
    });
    expect(files.get('OSSE/9_OSSE/archive/9_OSSE_report.html')).toContain('<html');
    expect(files.get('OSSE/9_OSSE/archive/9_OSSE.msh')).toBe('retained mesh');
    // Only the names that were taken moved.
    expect(files.has('OSSE/9_OSSE/9_OSSE.csv')).toBe(true);
    expect(files.has('OSSE/9_OSSE/archive/9_OSSE.csv')).toBe(false);
    const artifacts = JSON.parse(files.get('OSSE/9_OSSE/run.json')!).artifacts;
    expect(artifacts.mesh).toBe('archive/9_OSSE.msh');
    // run.json names every file where it landed, the redirected result files included.
    expect(artifacts.files).toEqual(expect.arrayContaining([
      'archive/9_OSSE.json', 'archive/9_OSSE_report.html', 'archive/9_OSSE.msh', '9_OSSE.csv',
    ]));
    expect(artifacts.files).not.toContain('9_OSSE.json');
    expect(artifacts.files).not.toContain('9_OSSE.msh');
    expect(artifacts.files.every((name: string) => files.has(`OSSE/9_OSSE/${name}`))).toBe(true);

    // A retry of the same archive lands on the same names: a no-op, not a refusal.
    const snapshot = new Map(files);
    await archiveRunToWorkspace(job, preferences, fetcher);
    expect(new Map(files)).toEqual(snapshot);
  });
});
