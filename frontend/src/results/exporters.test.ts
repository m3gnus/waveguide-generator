import { deferred } from '../shell/cadlink/selection.fixtures';
import { activityStore } from '../shell/activity';
import { buildGeometryExport } from '../api/designIo';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { addWoofer, emptyAssembly } from '../stores/assembly';
import { designForFamily, serializeDesign, useDesignStore } from '../stores/design';
import { resultExportSnapshot } from './exportContext';
import { runReportChartSpecs } from './runReport';
import { preferencesStore } from '../prefs/preferences';
import type { ResultPayload } from './types';
import { chartUnqualifiedBands } from './powerQualification';
import { readFileSync } from 'node:fs';
import type { RadiationImpedancePresentation } from '../api/results';
import { writeToOutputFolder } from '../api/workspace';
import { runAutomaticWorkspaceExport, archiveRunToWorkspace, buildDirectivityRenderPayload, buildFrequencyCsv, buildFullResultsJson, buildImpedanceCsv, buildPolarCsv, buildRadiationImpedanceCsv, buildSummaryText, downloadMeshArtifact, runExportBundle, runExportFormat, runWorkspaceExportBundle, saveMeshArtifactToWorkspace, writeWorkspaceFiles } from './exporters';
import type { CadIdentityProvenance, JobItem } from '../api/jobsSocket';

/**
 * The report's charts are drawn by hornlab-plots on the server. These tests
 * exercise what the exports write around them, so the drawing is stubbed here
 * and checked in runReport.test.ts and chartImage.test.ts.
 */
const reportChart = vi.hoisted(() => ({ render: vi.fn(async () => 'data:image/png;base64,iVBORw==') }));// The png format loads the results panel lazily (it imports this module);
// load it once here so its first import is not timed inside a test.
await import('../shell/ResultsPanel');

vi.mock('./chartImage', async (importOriginal) => ({
  ...await importOriginal<typeof import('./chartImage')>(),
  renderChartSpec: reportChart.render,
}));
import {
  ExportCancelledError,
  ExportCollisionError,
  type ExportCollision,
} from '../api/exportDestination';

const cadIdentity: CadIdentityProvenance = {
  schema_version: 1,
  ingest_id: 'wgi_01J5A8QK3M9T2XVBH0RD7NWE6C',
  selected_instance_id: 'instance-a',
  solver_anchor_instance_id: 'instance-a',
  instances: [{
    instance_id: 'instance-a', design_id: 'wgd-shared', body_object_ids: ['body-a'],
    assembly_from_link: [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
    source_ids: ['source-a'], default_drive_channel_ids: ['left'],
  }],
  drive_channels: [{ drive_channel_id: 'left', source_ids: ['source-a'], instance_ids: ['instance-a'] }],
};

const result: ResultPayload = {
  frequencies: [100, 200],
  spl_on_axis: { frequencies: [100, 200], spl: [90, null], phase_degrees: [0, 10] },
  di: { frequencies: [100, 200], di: [3, 4] },
  impedance: { frequencies: [100, 200], real: [1, null], imaginary: [0, -.5] },
  directivity: { horizontal: [[[-90, -12], [0, 0]], [[-90, [0.1, 0]], [0, [1, 0]]]] },
};

const finalResult = {
  ...result,
  result_kind: 'parametric' as const,
  result_contract_version: 1 as const,
  client_request_id: null,
  client_metadata: {},
  provenance: {
    schema_version: 1,
    wg_version: 'test',
    dependency_shas: {},
    request_sha256: 'a'.repeat(64),
    geometry_sha256: 'a'.repeat(64),
    solve_options_sha256: 'a'.repeat(64),
    request_identity: 'execution',
    execution_request_sha256: 'a'.repeat(64),
    execution_geometry_sha256: 'a'.repeat(64),
    execution_solve_options_sha256: 'a'.repeat(64),
    effective_request_sha256: 'a'.repeat(64),
    effective_geometry_sha256: 'a'.repeat(64),
    effective_solve_options_sha256: 'a'.repeat(64),
    resolved_engine: 'test',
  },
  metadata: {},
};

const radiation: RadiationImpedancePresentation = {
  schema_version: 1,
  quantity: 'average_aperture_pressure_per_volume_velocity',
  units: 'Pa*s/m^3',
  phase_time_convention: 'engineering_exp_plus_jwt',
  frequencies_hz: [100],
  apertures: [
    { name: 'PORT,L', area_m2: 0.01, tag: 31 },
    { name: 'PORT_R', area_m2: 0.02, tag: 32 },
  ],
  engineering_matrix: {
    real: [[[1, 2], [3, 4]]],
    imaginary: [[[5, 6], [7, 8]]],
  },
  in_phase_termination: {
    aperture_names: ['PORT,L', 'PORT_R'],
    real: [[3, 7]],
    imaginary: [[11, 15]],
  },
};

function workspacePayload(init?: RequestInit) {
  expect(init?.body).toBeInstanceOf(FormData);
  const form = init?.body as FormData;
  const paths = form.getAll('relative_path').map(String);
  const files = form.getAll('file');
  // Without member_source every member is an upload; with it, one source per path.
  const sources = form.has('member_source') ? form.getAll('member_source').map(String) : paths.map(() => 'upload');
  let upload = 0;
  return {
    subdirectory: String(form.get('subdirectory')),
    existing: String(form.get('existing')),
    destination: form.get('destination'),
    jobId: form.get('job_id'),
    members: paths.map((relative_path, index) => sources[index] === 'upload'
      ? { relative_path, blob: files[upload++] as Blob, server: undefined }
      : { relative_path, blob: undefined as unknown as Blob, server: JSON.parse(sources[index]) as { hold?: string; member: string } }),
  };
}

describe('result exporters', () => {
  beforeEach(() => { activityStore.reset(); preferencesStore.resetForTests(); });
  it('preserves design-export bytes, names and warnings through a confirmed multi-format run export', async () => {
    const design = designForFamily('OSSE');
    const warning = 'Sizing reached its backstop. Check fine detail in CAD.';
    const bytes = new Uint8Array([0, 13, 10, 255, 42]);
    const confirm = vi.fn().mockResolvedValue(true);
    const published: Blob[][] = [];
    const fetcher: typeof fetch = async (url, init) => {
      if (String(url).startsWith('/api/export/')) return new Response(bytes, { headers: {
        'Content-Disposition': `attachment; filename="server.${String(url).includes('/step') ? 'step' : 'stl'}"`,
        'X-Export-Warning': warning,
      } });
      const payload = workspacePayload(init);
      published.push(payload.members.map(member => member.blob));
      if (published.length === 1) return Response.json({ code: 'export_collision', detail: 'Files would be replaced',
        paths: ['/out/server.step'], directory: '/out' }, { status: 409 });
      return Response.json({ files: ['/out/server.step', '/out/server.stl'], directory: '/out', replaced: ['/out/server.step'] });
    };
    const built = await buildGeometryExport('step', design, 4, 'horn', undefined, 'solid', fetcher);
    const bundle = await runWorkspaceExportBundle({ design, designRevision: 4, jobStem: 'horn',
      preferences: preferencesStore.getSnapshot(), fetcher, confirmReplacements: confirm,
    }, ['step', 'stl'], 'confirm');
    expect(built.warning).toBe(warning);
    expect(bundle.warnings).toEqual([warning, warning]);
    expect(bundle.files).toEqual(['/out/server.step', '/out/server.stl']);
    expect(bundle.replaced).toEqual(['/out/server.step']);
    expect(confirm).toHaveBeenCalledOnce();
    for (const files of published) for (const blob of files) {
      expect(new Uint8Array(await blob.arrayBuffer())).toEqual(new Uint8Array(await built.blob.arrayBuffer()));
    }
  });
  it.each(['stl', 'fusion_csv', 'mwg_config'] as const)('refuses %s generation even if an imported run carries a parametric anchor', async format => {
    const selectedRun = {
      id: 'cad-anchor', config_summary: { geometry_type: 'imported' },
      script_snapshot: { version: 1, design: { formula: 'OSSE', L: 120 } },
      design_revision: 3, solve_options: {},
    } as unknown as JobItem;
    expect(resultExportSnapshot(selectedRun).design).toBeUndefined();
    const fetcher = vi.fn<typeof fetch>();
    await expect(runExportFormat(format, {
      selectedRun, design: designForFamily('OSSE'), designRevision: 3,
      jobStem: 'cad-run', preferences: preferencesStore.getSnapshot(), fetcher,
    })).rejects.toThrow(/imported models/i);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('publishes exact returned STEP bytes with the existing destination and run guards', async () => {
    const selectedRun = { id: 'cad-return', config_summary: { geometry_type: 'imported' },
      script_snapshot: { version: 1, design: { formula: 'OSSE', L: 120 } }, design_revision: 7,
      solve_options: {} } as unknown as JobItem;
    const bytes = new Uint8Array([73, 83, 79, 13, 10, 255, 0, 13, 10]);
    const requests: Array<{ path: string; init?: RequestInit }> = [];
    const fetcher = vi.fn<typeof fetch>(async (url, init) => {
      const path = String(url);
      requests.push({ path, init });
      if (path === '/api/export/runs/cad-return/step') return new Response(bytes);
      if (path === '/api/workspace/write-export') return new Response(JSON.stringify({
        directory: '/chosen', files: ['/chosen/saved-run.step'],
      }));
      throw new Error(`Unexpected request: ${path}`);
    });
    const output = await runWorkspaceExportBundle({ selectedRun, jobId: selectedRun.id,
      design: designForFamily('OSSE'), jobStem: 'saved-run', preferences: preferencesStore.getSnapshot(),
      destinationToken: 'chosen-destination', fetcher }, ['step'], 'confirm');
    expect(output.failures).toEqual([]);
    expect(requests.map(({ path }) => path)).toEqual(['/api/export/runs/cad-return/step', '/api/workspace/write-export']);
    expect(requests[0].init?.body).toBeUndefined();
    const publication = workspacePayload(requests[1].init);
    expect(publication).toMatchObject({ jobId: 'cad-return', destination: 'chosen-destination', existing: 'confirm' });
    expect(new Uint8Array(await publication.members[0].blob.arrayBuffer())).toEqual(bytes);
  });

  it('keeps returned STEP fallback names scoped to the selected drive channel', async () => {
    const selectedRun = { id: 'cad-return', config_summary: { geometry_type: 'imported' } } as unknown as JobItem;
    const wrapped: ResultPayload = { frequencies: [], channels: { 'drive-a': result, 'drive-b': result } };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response('returned step bytes'));
    const saveBlob = vi.fn();
    expect(await runExportFormat('step', { selectedRun, result: wrapped, channelId: 'drive-b',
      jobStem: 'saved-run', preferences: preferencesStore.getSnapshot(), fetcher, saveBlob,
    })).toEqual(['saved-run-drive-b.step']);
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher.mock.calls[0][0]).toBe('/api/export/runs/cad-return/step');
    expect(await saveBlob.mock.calls[0][0].text()).toBe('returned step bytes');
  });

  it('publishes nothing when the returned STEP fails verification', async () => {
    const selectedRun = { id: 'cad-return', config_summary: { geometry_type: 'imported' },
      design_revision: 7, script_snapshot: null, solve_options: {} } as unknown as JobItem;
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({
      detail: "The saved STEP does not match the CAD return used by this run. Send the model again from Fusion and solve it before exporting STEP.",
    }), { status: 422 }));
    const output = await runWorkspaceExportBundle({ selectedRun, jobId: selectedRun.id,
      jobStem: 'saved-run', preferences: preferencesStore.getSnapshot(), fetcher }, ['step']);
    expect(output.files).toEqual([]);
    expect(output.failures[0].reason).toContain('does not match');
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher.mock.calls[0][0]).toBe('/api/export/runs/cad-return/step');
  });

  it.each(['parametric', 'assembly'] as const)('exports %s run config from its captured design even if passed a draft', async kind => {
    const captured = { ...designForFamily('R-OSSE'), L: 123,
      ...(kind === 'assembly' ? { assembly: addWoofer(emptyAssembly(), { anchor: 'below' }).assembly } : {}) };
    const selectedRun = { id: `captured-${kind}`, config_summary: kind === 'assembly'
      ? { geometry_type: 'imported', import_origin: 'design-assembly' } : {},
      script_snapshot: { version: 1, design: serializeDesign(captured) }, design_revision: 7, solve_options: {} } as unknown as JobItem;
    expect(resultExportSnapshot(selectedRun).design).toEqual(captured);
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ text: 'captured config' })));
    const saveText = vi.fn();
    await runExportFormat('mwg_config', { selectedRun, design: designForFamily('OSSE'), designRevision: 99,
      jobStem: 'saved-run', preferences: preferencesStore.getSnapshot(), fetcher, saveText });
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher.mock.calls[0][0]).toBe('/api/design/serialize');
    const body = JSON.parse(String(fetcher.mock.calls[0][1]?.body));
    expect(body.design).toMatchObject(serializeDesign(captured));
    expect(saveText).toHaveBeenCalledWith('captured config', 'saved-run_config.cfg');
  });

  it('uses the parametric run snapshot for historical geometry, never a supplied draft', async () => {
    const captured = { ...designForFamily('OSSE'), L: 123 };
    const selectedRun = { id: 'captured-geometry', config_summary: {},
      script_snapshot: { version: 1, design: serializeDesign(captured) }, design_revision: 7 } as unknown as JobItem;
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response('stored geometry'));
    await runExportFormat('step', { selectedRun, design: designForFamily('OSSE'), designRevision: 99,
      jobStem: 'saved-run', preferences: preferencesStore.getSnapshot(), fetcher, saveBlob: vi.fn() });
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toMatchObject({ design: serializeDesign(captured), designRevision: 7 });
    fetcher.mockClear();
    await expect(runExportFormat('step', { selectedRun: { ...selectedRun, script_snapshot: null }, design: captured,
      jobStem: 'saved-run', preferences: preferencesStore.getSnapshot(), fetcher })).rejects.toThrow('did not keep its design');
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('imports a retired snapshot only for an explicit geometry rebuild and preserves the historical run', async () => {
    const selectedRun = { id: 'lookup-geometry', config_summary: {}, design_revision: 7,
      script_snapshot: { version: 1, design: { formula: 'LOOKUP', lookup_profile: [[0, 12.7], [100, 80]] } } } as unknown as JobItem;
    const original = structuredClone(selectedRun);
    const converted = serializeDesign(designForFamily('FREEFORM'));
    const note = 'Opened as Custom profile. Maximum generated-grid change 0.2 mm.';
    const fetcher = vi.fn<typeof fetch>(async url => String(url) === '/api/design/open'
      ? new Response(JSON.stringify({ design: converted, migrationsApplied: [{ name: '007_custom_profile_import', note }] }))
      : new Response('rebuilt geometry'));
    expect(resultExportSnapshot(selectedRun).design).toBeUndefined();
    expect(fetcher).not.toHaveBeenCalled();
    await runExportFormat('step', { selectedRun, design: designForFamily('OSSE'), designRevision: 99,
      jobStem: 'saved-run', preferences: preferencesStore.getSnapshot(), fetcher, saveBlob: vi.fn() });
    expect(fetcher.mock.calls[0][0]).toBe('/api/design/open');
    const built = fetcher.mock.calls.find(([url]) => String(url).startsWith('/api/export/step'))!;
    expect(JSON.parse(String(built[1]?.body))).toMatchObject({ design: converted, designRevision: 7 });
    expect(selectedRun).toEqual(original);
    expect(useDesignStore.getState().loadNotice).toBe(note);
  });

  it.each(['step', 'stl', 'fusion_csv'] as const)('refuses %s geometry generation for a captured assembly run', async format => {
    const selectedRun = { id: 'assembly-geometry', config_summary: { geometry_type: 'imported', import_origin: 'design-assembly' },
      script_snapshot: { version: 1, design: serializeDesign(designForFamily('R-OSSE')) } } as unknown as JobItem;
    const fetcher = vi.fn<typeof fetch>();
    await expect(runExportFormat(format, { selectedRun, ...resultExportSnapshot(selectedRun),
      jobStem: 'saved-run', preferences: preferencesStore.getSnapshot(), fetcher })).rejects.toThrow(/imported models/i);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each(['parametric', 'assembly'] as const)('never exports a draft for a %s run without a readable snapshot', async kind => {
    const selectedRun = { id: `missing-${kind}`, config_summary: kind === 'assembly'
      ? { geometry_type: 'imported', import_origin: 'design-assembly' } : {}, script_snapshot: null } as unknown as JobItem;
    const fetcher = vi.fn<typeof fetch>();
    await expect(runExportFormat('mwg_config', { selectedRun, design: designForFamily('OSSE'),
      jobStem: 'saved-run', preferences: preferencesStore.getSnapshot(), fetcher })).rejects.toThrow('did not keep its design');
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each([
    { config_summary: { formula_type: 'cad-import' } },
    { config_summary: { import_origin: 'mesh-file' } },
    { design_availability: { source: 'cad-import' } },
    { cad_intent: { bundle_path: 'ingest/legacy' } },
    { cad_return: { bundle_path: 'ingest/legacy', return_id: null } },
  ])('does not expose a legacy imported anchor as an exportable design: %j', provenance => {
    expect(resultExportSnapshot({
      ...provenance,
      script_snapshot: { version: 1, design: { formula: 'OSSE', L: 120 } }, design_revision: 1,
    } as Parameters<typeof resultExportSnapshot>[0]).design).toBeUndefined();
  });

  it('builds the five client-side audit formats with stable missing-value behavior', () => {
    const preferences = preferencesStore.getSnapshot();
    expect(buildFrequencyCsv(result, preferences)).toContain('200,,4,,-0.5');
    expect(JSON.parse(buildFullResultsJson(result, preferences, new Date('2026-08-04T10:00:00Z'))).results).toEqual(result);
    expect(buildSummaryText(result, preferences, new Date('2026-08-04T10:00:00Z'))).toContain('Average SPL: 90.00 dB');
    expect(buildPolarCsv(result)).toContain('200,horizontal,-90,-20');
    expect(buildImpedanceCsv(result)).toContain('200,,-0.5');
  });

  it('names a modal channel impedance as such in every export', () => {
    const preferences = preferencesStore.getSnapshot();
    const modal: ResultPayload = {
      ...result,
      metadata: { ...(result.metadata ?? {}), impedance_projection: 'modal_mean_square_velocity' },
    };
    expect(buildFrequencyCsv(modal, preferences)).toContain('Impedance Real (Z/(rho*c) modal)');
    expect(buildImpedanceCsv(modal)).toContain('Z_Real_Z_over_rho_c_modal');
    expect(buildSummaryText(modal, preferences, new Date('2026-08-04T10:00:00Z'))).toContain('Z_Im/(rho*c)  (modal)');
    // An ordinary acoustic or electrical curve keeps its existing labels.
    expect(buildImpedanceCsv(result)).toContain('Z_Real_Z_over_rho_c,');
  });

  describe('polar frame/frequency alignment', () => {
    it('refuses fewer directivity frames than frequency labels', () => {
      const misaligned: ResultPayload = {
        frequencies: [100, 200, 300],
        directivity: { horizontal: [[[0, -1]], [[0, -2]]] },
      };
      expect(() => buildPolarCsv(misaligned)).toThrow(/2 frame\(s\).*3 point\(s\)/);
    });

    it('refuses a non-finite angle rather than emitting a NaN label', () => {
      const badAngle: ResultPayload = {
        frequencies: [100],
        directivity: { horizontal: [[[Number.NaN, -1]]] },
      };
      expect(() => buildPolarCsv(badAngle)).toThrow(/non-finite angle/);
    });

    it('exports normally when every plane is aligned with the frequency axis', () => {
      const aligned: ResultPayload = {
        frequencies: [100, 200],
        directivity: {
          horizontal: [[[0, -1], [30, -4]], [[0, -2], [30, -5]]],
          vertical: [[[0, -1.5]], [[0, -2.5]]],
        },
      };
      const csv = buildPolarCsv(aligned);
      expect(csv).toContain('100,horizontal,0,-1');
      expect(csv).toContain('200,horizontal,30,-5');
      expect(csv).toContain('200,vertical,0,-2.5');
    });

    it('refuses an extra frame rather than repeating the last frequency', () => {
      const extra: ResultPayload = { frequencies: [100], directivity: { horizontal: [[[0, 0]], [[0, -3]]] } };
      expect(() => buildPolarCsv(extra)).toThrow(/2 frame.*1 point/);
    });

    it('uses the polar grid even when SPL has a different equally sized grid', () => {
      const independent: ResultPayload = {
        frequencies: [100, 200],
        spl_on_axis: { frequencies: [110, 220], spl: [80, 81] },
        directivity: { horizontal: [[[0, 0]], [[0, 0]]] },
      };
      expect(buildPolarCsv(independent)).toContain('200,horizontal,0,0');
      expect(buildPolarCsv(independent)).not.toContain('220,');
    });

    it('labels every measured angle, including a grid that shifts between frequencies', () => {
      // The polar CSV names the angle on each row, so an angle grid that moves
      // between frequencies is exported as measured rather than refused.
      const angular: ResultPayload = { frequencies: [100, 200], directivity: {
        horizontal: [[[0, 0], [30, -6]], [[0, 0], [40, -6]]],
      } };
      const csv = buildPolarCsv(angular);
      expect(csv).toContain('100,horizontal,30,-6');
      expect(csv).toContain('200,horizontal,40,-6');
    });

    it('surfaces a mismatched axis as a bundle-level export failure, not a thrown crash', async () => {
      const misaligned: ResultPayload = {
        frequencies: [100, 200, 300],
        directivity: { horizontal: [[[0, -1]], [[0, -2]]] },
      };
      const saveText = vi.fn();
      const bundle = await runExportBundle({
        result: misaligned, jobStem: 'horn_1', preferences: preferencesStore.getSnapshot(), saveText,
      }, ['polar_csv']);
      expect(saveText).not.toHaveBeenCalled();
      expect(bundle.files).toEqual([]);
      expect(bundle.failures).toEqual([
        { format: 'polar_csv', reason: 'WG could not write this file. Try again.' },
      ]);
    });
  });

  it('adds one versioned CAD identity sidecar to a manual result bundle', async () => {
    const saveText = vi.fn();
    const bundle = await runExportBundle({
      result,
      jobId: 'job-a',
      jobStem: 'horn_1',
      cadIdentity,
      preferences: preferencesStore.getSnapshot(),
      saveText,
    }, ['csv']);

    expect(bundle).toEqual({
      files: ['horn_1.csv', 'horn_1_cad_identity.json'], failures: [],
    });
    const sidecar = JSON.parse(String(saveText.mock.calls[1][0]));
    expect(saveText.mock.calls[1][1]).toBe('horn_1_cad_identity.json');
    expect(sidecar).toEqual({
      schema: 'waveguide-generator.cad-result-provenance',
      schema_version: 1,
      job_id: 'job-a',
      identity: cadIdentity,
    });
  });
  it('exports the engineering radiation matrix and reduced port loads with explicit semantics', () => {
    const csv = buildRadiationImpedanceCsv(radiation);
    expect(csv).toContain('# Units: Pa*s/m^3');
    expect(csv).toContain('# Phase time convention: engineering_exp_plus_jwt');
    expect(csv).toContain('100,engineering_matrix,"PORT,L",PORT_R,2,6');
    expect(csv).toContain('100,in_phase_termination,"PORT,L","PORT,L+PORT_R",3,11');
  });

  it('integrates lossless NPZ and curve CSV as one non-channel export family', async () => {
    const saveBlob = vi.fn();
    const saveText = vi.fn();
    const fetcher = vi.fn<typeof fetch>(async (input) => {
      const path = String(input);
      if (path.endsWith('/presentation')) {
        return new Response(JSON.stringify(radiation), { status: 200 });
      }
      if (path === '/api/radiation-impedance/cardioid-job') {
        return new Response(new Uint8Array([1, 2, 3]), { status: 200 });
      }
      return new Response('not found', { status: 404 });
    });
    const bundle = await runExportBundle({
      result: {
        frequencies: [],
        channel_order: ['left', 'right'],
        channels: { left: result, right: result },
      },
      jobId: 'cardioid-job',
      hasRadiationImpedanceArtifact: true,
      jobStem: 'cardioid_7',
      preferences: preferencesStore.getSnapshot(),
      fetcher,
      saveBlob,
      saveText,
    }, ['radiation_impedance_npz', 'radiation_impedance_csv']);

    expect(bundle).toEqual({
      files: ['cardioid_7_radiation_impedance.npz', 'cardioid_7_radiation_impedance.csv'],
      failures: [],
    });
    expect(fetcher.mock.calls.map(([input]) => String(input))).toEqual([
      '/api/radiation-impedance/cardioid-job',
      '/api/radiation-impedance/cardioid-job/presentation',
    ]);
    expect(saveBlob).toHaveBeenCalledOnce();
    expect(saveText).toHaveBeenCalledOnce();
  });

  it('refuses the optional radiation export cleanly when the artifact is absent', async () => {
    const bundle = await runExportBundle({
      jobId: 'ordinary-job',
      hasRadiationImpedanceArtifact: false,
      jobStem: 'ordinary_1',
      preferences: preferencesStore.getSnapshot(),
      fetcher: vi.fn(),
    }, ['radiation_impedance_npz', 'radiation_impedance_csv']);
    expect(bundle).toEqual({
      files: [],
      failures: [
        { format: 'radiation_impedance_npz', reason: 'This run has no port data. Solve a passive cardioid design to export it.' },
        { format: 'radiation_impedance_csv', reason: 'This run has no port data. Solve a passive cardioid design to export it.' },
      ],
    });
  });
  it('joins DI and impedance onto their own frequencies instead of the SPL row index', () => {
    // DI and impedance may be solved on their own grids. Zipping them against the SPL
    // row index would label 175 Hz DI as 100 Hz and 150 Hz impedance as 200 Hz, with
    // nothing in the file revealing it.
    const offGrid: ResultPayload = {
      frequencies: [100, 200],
      spl_on_axis: { frequencies: [100, 200], spl: [90, 91], phase_degrees: [0, 10] },
      di: { frequencies: [100, 175], di: [3, 4] },
      impedance: { frequencies: [150, 200, 250], real: [1, 2, 3], imaginary: [0, -0.5, -0.25] },
    };
    const preferences = preferencesStore.getSnapshot();

    expect(buildFrequencyCsv(offGrid, preferences)).toBe([
      'Frequency (Hz),SPL (dB),DI (dB),Impedance Real (Z/(rho*c)),Impedance Imag (Z/(rho*c))',
      '100,90,3,,',
      '150,,,1,0',
      '175,,4,,',
      '200,91,,2,-0.5',
      '250,,,3,-0.25',
      '',
    ].join('\n'));

    const summary = buildSummaryText(offGrid, preferences, new Date('2026-08-10T10:00:00Z'));
    expect(summary).toContain('Frequency range: 100 - 250 Hz');
    expect(summary).toContain('Number of points: 5');
    expect(summary).toContain('175.00  n/a  4.00  n/a  n/a');
    expect(summary).toContain('200.00  91.00  n/a  2.00  -0.50');
  });
  it('leaves the frozen CSV schema untouched when every series shares one grid', () => {
    // The join must be a no-op for the shape the solver emits today, so an existing
    // consumer of the frozen schema sees byte-identical output.
    expect(buildFrequencyCsv(result, preferencesStore.getSnapshot())).toBe([
      'Frequency (Hz),SPL (dB),DI (dB),Impedance Real (Z/(rho*c)),Impedance Imag (Z/(rho*c))',
      '100,90,3,1,0',
      '200,,4,,-0.5',
      '',
    ].join('\n'));
  });
  it('labels electrical impedance values as ohms in every text export', () => {
    const electrical: ResultPayload = {
      ...result,
      metadata: { impedance_units: 'ohms', impedance_quantity: 'electrical_input_impedance' },
    };
    const preferences = preferencesStore.getSnapshot();

    expect(buildFrequencyCsv(electrical, preferences).split('\n')[0])
      .toBe('Frequency (Hz),SPL (dB),DI (dB),Impedance Real (ohms),Impedance Imag (ohms)');
    expect(buildSummaryText(electrical, preferences)).toContain('Average Real Part ohms: 1.00');
    expect(buildSummaryText(electrical, preferences)).toContain('Z_Re(ohms)  Z_Im(ohms)');
    expect(buildImpedanceCsv(electrical).split('\n')[0]).toBe('Freq_Hz,Z_Real_Ohm,Z_Imag_Ohm');
  });
  it('preserves the normalized-acoustic labels for unloaded waveguide exports', () => {
    const preferences = preferencesStore.getSnapshot();

    expect(buildFrequencyCsv(result, preferences).split('\n')[0])
      .toContain('Impedance Real (Z/(rho*c)),Impedance Imag (Z/(rho*c))');
    expect(buildSummaryText(result, preferences)).toContain('Z_Re/(rho*c)  Z_Im/(rho*c)');
    expect(buildImpedanceCsv(result).split('\n')[0])
      .toBe('Freq_Hz,Z_Real_Z_over_rho_c,Z_Imag_Z_over_rho_c');
  });
  it('builds only the selected client-side format', async () => {
    const toJSON = vi.fn(() => { throw new Error('unselected JSON builder ran'); });
    const saveText = vi.fn();
    const csvResult = { ...result, toJSON };

    await expect(runExportFormat('csv', {
      result: csvResult,
      jobStem: 'horn_1',
      preferences: preferencesStore.getSnapshot(),
      saveText,
    })).resolves.toEqual(['horn_1.csv']);
    expect(toJSON).not.toHaveBeenCalled();
    expect(saveText).toHaveBeenCalledOnce();
  });
  it('downloads a selected retained pressure basis and stages it by server filename', async () => {
    const saveBlob = vi.fn();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(
      new Uint8Array([80, 75, 3, 4]),
      {
        status: 200,
        headers: { 'Content-Disposition': 'attachment; filename="drive-mf_pressure_basis.npz"' },
      },
    ));

    await expect(runExportFormat('pressure_basis', {
      result: { frequencies: [], channel_order: ['drive-mf'], channels: { 'drive-mf': result } },
      channelId: 'drive-mf',
      jobId: 'job/basis',
      jobStem: 'horn_1',
      preferences: preferencesStore.getSnapshot(),
      fetcher,
      saveBlob,
    })).resolves.toEqual(['drive-mf_pressure_basis.npz']);

    expect(fetcher).toHaveBeenCalledWith('/api/pressure-basis/job%2Fbasis?channel_id=drive-mf');
    expect(saveBlob.mock.calls[0][1]).toBe('drive-mf_pressure_basis.npz');
    expect([...new Uint8Array(await (saveBlob.mock.calls[0][0] as Blob).arrayBuffer())])
      .toEqual([80, 75, 3, 4]);
  });
  it('exports retained drive bases without requesting solve-time derived channels', async () => {
    const wrapped: ResultPayload = {
      frequencies: [],
      channel_order: ['drive-mf', 'combined'],
      channels: {
        'drive-mf': { ...result, metadata: { drive_channel_id: 'drive-mf' } },
        combined: { ...result, metadata: { derived_from_channels: ['drive-mf'] } },
      },
    };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(
      new Uint8Array([80, 75]),
      { status: 200, headers: { 'Content-Disposition': 'attachment; filename="drive-mf_pressure_basis.npz"' } },
    ));

    const bundle = await runExportBundle({
      result: wrapped,
      jobId: 'basis-job',
      jobStem: 'horn_1',
      preferences: preferencesStore.getSnapshot(),
      fetcher,
      saveBlob: vi.fn(),
    }, ['pressure_basis']);

    expect(bundle).toEqual({ files: ['drive-mf_pressure_basis.npz'], failures: [] });
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher).toHaveBeenCalledWith('/api/pressure-basis/basis-job?channel_id=drive-mf');
  });
  it('writes both derived-acoustics sidecars as one retryable format', async () => {
    const saveText = vi.fn();

    await expect(runExportFormat('derived_acoustics', {
      result,
      jobStem: 'horn_1',
      preferences: preferencesStore.getSnapshot(),
      saveText,
    })).resolves.toEqual([
      'horn_1_derived_acoustics.csv',
      'horn_1_derived_acoustics.json',
    ]);

    expect(saveText).toHaveBeenCalledTimes(2);
    expect(saveText.mock.calls[0][0]).toContain('power_response_db_spl_avg');
    expect(saveText.mock.calls[0][0]).toContain('radiated_power_surface_w,radiated_power_sphere_w,power_agreement_db');
    expect(JSON.parse(saveText.mock.calls[1][0]).rows[0]).toMatchObject({
      frequency_hz: 100,
      power_response_db_spl_avg: 87,
    });
  });
  it('writes one self-contained report for the complete multi-channel run', async () => {
    const saveText = vi.fn();
    const wrapped: ResultPayload = {
      frequencies: [], channel_order: ['HF', 'MF'], channels: { HF: result, MF: result },
    };

    await expect(runExportFormat('html_report', {
      result: wrapped,
      jobStem: 'horn_1',
      designName: 'Horn One',
      // A dark export theme: the report's charts stay classic on its light page.
      preferences: { ...preferencesStore.getSnapshot(), chartTheme: 'console' },
      now: new Date('2026-08-20T12:00:00Z'),
      saveText,
    })).resolves.toEqual(['horn_1_report.html']);

    expect(saveText).toHaveBeenCalledOnce();
    expect(saveText.mock.calls[0][1]).toBe('horn_1_report.html');
    expect(saveText.mock.calls[0][2]).toBe('text/html;charset=utf-8');
    expect(saveText.mock.calls[0][0]).toContain('<h1>Horn One</h1>');
    expect(saveText.mock.calls[0][0]).toContain('<h2>HF</h2>');
    expect(saveText.mock.calls[0][0]).toContain('<h2>MF</h2>');
    // Both channels' charts, drawn classic and embedded, so the file opens
    // offline on its own.
    const drawn = runReportChartSpecs(wrapped);
    expect(drawn.map(({ section }) => section)).toEqual(['HF', 'MF']);
    expect(reportChart.render.mock.calls.map((call) => (call as unknown[]).slice(0, 2))).toEqual(drawn.map(({ spec }) => [spec, 'classic']));
    expect(saveText.mock.calls[0][0].match(/<img class="plot"[^>]*src="data:image\/png;base64,iVBORw=="/g)).toHaveLength(2);
  });
  it('exports only the selected CAD drive channel and refuses an ambiguous wrapper', async () => {
    const wrapped: ResultPayload = {
      frequencies: [],
      channel_order: ['drive-a', 'drive-b'],
      channels: { 'drive-a': result, 'drive-b': { ...result, spl_on_axis: { frequencies: [100, 200], spl: [70, 71] } } },
    };
    await expect(runExportFormat('csv', {
      result: wrapped,
      jobStem: 'horn_1',
      preferences: preferencesStore.getSnapshot(),
      saveText: vi.fn(),
    })).rejects.toThrow('Choose a drive channel');
    const saveText = vi.fn();
    await runExportFormat('csv', {
      result: wrapped,
      channelId: 'drive-b',
      jobStem: 'horn_1',
      preferences: preferencesStore.getSnapshot(),
      saveText,
    });
    expect(saveText.mock.calls[0][0]).toContain('100,70');
    expect(saveText.mock.calls[0][0]).not.toContain('100,90');
  });
  it('bundles every CAD drive channel with per-channel names and writes design formats once', async () => {
    const wrapped: ResultPayload = {
      frequencies: [],
      channel_order: ['drive-hf'],
      channels: {
        'drive-mf': { ...result, spl_on_axis: { frequencies: [100, 200], spl: [70, 71] } },
        'drive-hf': result,
      },
    };
    const saveText = vi.fn();
    const fetcher = vi.fn<typeof fetch>(async () => new Response('step', {
      status: 200,
      headers: { 'Content-Disposition': 'attachment; filename="horn_1.step"' },
    }));
    const bundle = await runExportBundle({
      result: wrapped,
      design: designForFamily('OSSE'),
      jobStem: 'horn_1',
      preferences: preferencesStore.getSnapshot(),
      saveText,
      saveBlob: vi.fn(),
      fetcher,
    }, ['csv', 'step']);

    expect(bundle).toEqual({
      files: ['horn_1-drive-hf.csv', 'horn_1-drive-mf.csv', 'horn_1.step'],
      failures: [],
    });
    expect(saveText.mock.calls.map(([, filename]) => filename)).toEqual([
      'horn_1-drive-hf.csv', 'horn_1-drive-mf.csv',
    ]);
    expect(saveText.mock.calls[0][0]).toContain('100,90');
    expect(saveText.mock.calls[1][0]).toContain('100,70');
    expect(fetcher).toHaveBeenCalledOnce();
  });
  it('posts config exports to the non-mutating serializer endpoint', async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const path = String(input);
      if (path === '/api/design/serialize') return new Response(JSON.stringify({ text: 'cfg', suggestedFilename: 'horn_1_config.cfg' }), { status: 200 });
      return new Response('geometry', { status: 200, headers: { 'Content-Disposition': `attachment; filename="file.${path.includes('/step') ? 'step' : 'stl'}"` } });
    });
    const context = { jobStem: 'horn_1', preferences: preferencesStore.getSnapshot(), design: designForFamily('OSSE'), designRevision: 4, fetcher, saveText: vi.fn(), saveBlob: vi.fn() };
    await runExportFormat('mwg_config', context);
    await runExportFormat('step', context);
    await runExportFormat('stl', context);
    expect(fetcher.mock.calls.map(([input]) => String(input))).toEqual(['/api/design/serialize', '/api/export/step?body=solid', '/api/export/stl']);
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).not.toHaveProperty('identity');
  });
  it('names a Custom profile parameter config .cfg', async () => {
    const saveText = vi.fn();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ text: 'mwg' }), { status: 200 }));
    expect(await runExportFormat('mwg_config', {
      jobStem: 'lk_1', preferences: preferencesStore.getSnapshot(), design: designForFamily('FREEFORM'), fetcher, saveText,
    })).toEqual(['lk_1_config.cfg']);
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body)).filename).toBe('lk_1_config.cfg');
    expect(saveText).toHaveBeenCalledWith('mwg', 'lk_1_config.cfg');
  });
  it('prepares the Fusion CSV pair in one request and publishes neither half on failure', async () => {
    const saveBlob = vi.fn();
    const context = { jobStem: 'horn_1', preferences: preferencesStore.getSnapshot(), design: designForFamily('OSSE'), saveBlob };
    const files = [
      { filename: 'horn_1_profiles.csv', text: '# profiles\r\n1;2;3\r\n' },
      { filename: 'horn_1_slices.csv', text: '# slices\r\n1;2;3\r\n1;2;3\r\n' },
    ];
    for (const response of [new Response('slices failed', { status: 500 }), Response.json({ files: [files[0], { filename: files[1].filename }] })]) {
      const workspaceResponse = response.clone();
      await expect(runExportFormat('fusion_csv', { ...context, fetcher: async () => response })).rejects.toThrow();
      expect(saveBlob).not.toHaveBeenCalled();
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(workspaceResponse);
      const bundle = await runWorkspaceExportBundle({ ...context, fetcher }, ['fusion_csv']);
      expect(bundle.files).toEqual([]);
      expect(bundle.failures).toHaveLength(1);
      expect(fetcher).toHaveBeenCalledOnce(); // No Workspace publication.
    }
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ files }));
    expect(await runExportFormat('fusion_csv', { ...context, fetcher })).toEqual(files.map(file => file.filename));
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher.mock.calls[0][0]).toBe('/api/export/profiles?kind=pair');
    for (const [index, file] of files.entries()) {
      expect(await saveBlob.mock.calls[index][0].text()).toBe(file.text);
      expect(saveBlob.mock.calls[index][1]).toBe(file.filename);
    }
  });
  it.each([-3, -6, -9, -12] as const)('passes the selected %s dB map range without changing result arrays', (mapReference) => {
    const before = JSON.stringify(result);
    const payload = buildDirectivityRenderPayload(result, { ...preferencesStore.getSnapshot(), mapReference });
    expect(payload).toMatchObject({ reference_level: mapReference, display_range_db: [mapReference * 5, 0] });
    expect(payload.directivity).toBe(result.directivity);
    expect(JSON.stringify(result)).toBe(before);
  });

  it('writes the png format from the cards\' own charts and the dedicated map, in the export theme', async () => {
    reportChart.render.mockClear();
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => String(input) === '/api/render-directivity'
      ? new Response(JSON.stringify({ image: 'data:image/png;base64,Ag==' }), { status: 200 })
      : new Response('not served', { status: 404 }));
    const saveBlob = vi.fn();
    const context = { jobStem: 'horn_1', preferences: { ...preferencesStore.getSnapshot(), chartTheme: 'paper', mapReference: -12 as const, directivityGuideInterval: 15 as const, smoothing: 'none' as const }, result, fetcher, saveBlob };
    expect(await runExportFormat('png', context)).toEqual([
      'horn_1_frequency_response.png', 'horn_1_directivity_index.png', 'horn_1_impedance.png', 'horn_1_directivity_map.png',
    ]);
    // /api/render-charts is gone: the line charts are card specs, the map its own request.
    expect(fetcher.mock.calls.map(([input]) => String(input))).toEqual(['/api/render-directivity']);
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toMatchObject({ theme: 'paper', reference_level: -12, angle_guide_step: 15 });
    const drawn = reportChart.render.mock.calls.map((call) => (call as unknown as [{ title: string; series: Array<{ label: string; x: number[]; y: Array<number | null> }> }, string]));
    expect(drawn.map(([spec, theme]) => [spec.title, theme])).toEqual([
      ['Sound pressure level', 'paper'], ['Directivity index', 'paper'], ['Impedance', 'paper'],
    ]);
    // The SPL file is the SPL card's own series: the run's on-axis samples.
    expect(drawn[0][0].series[0]).toMatchObject({ label: 'horn_1', x: [100, 200], y: [90, null] });
    expect(saveBlob.mock.calls.map(([, filename]) => filename)).toEqual([
      'horn_1_frequency_response.png', 'horn_1_directivity_index.png', 'horn_1_impedance.png', 'horn_1_directivity_map.png',
    ]);
    expect(Array.from(new Uint8Array(await saveBlob.mock.calls[3][0].arrayBuffer()))).toEqual([2]);
  });

  it('draws no map file for a run without directivity', async () => {
    const fetcher = vi.fn<typeof fetch>();
    const saveBlob = vi.fn();
    const { directivity: _directivity, ...flat } = result;
    expect(await runExportFormat('png', { jobStem: 'horn_1', preferences: preferencesStore.getSnapshot(), result: flat, fetcher, saveBlob }))
      .toEqual(['horn_1_frequency_response.png', 'horn_1_directivity_index.png', 'horn_1_impedance.png']);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('draws no map file when every directivity plane is empty, as the map card shows none', async () => {
    const fetcher = vi.fn<typeof fetch>();
    const saveBlob = vi.fn();
    const empty = { ...result, directivity: { horizontal: [], vertical: [] } } as unknown as ResultPayload;
    expect(await runExportFormat('png', { jobStem: 'horn_1', preferences: preferencesStore.getSnapshot(), result: empty, fetcher, saveBlob }))
      .toEqual(['horn_1_frequency_response.png', 'horn_1_directivity_index.png', 'horn_1_impedance.png']);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each(['map', 'last chart'] as const)('saves nothing when the %s fails to draw', async (failing) => {
    reportChart.render.mockClear();
    if (failing === 'last chart') {
      reportChart.render
        .mockResolvedValueOnce('data:image/png;base64,iVBORw==')
        .mockResolvedValueOnce('data:image/png;base64,iVBORw==')
        .mockRejectedValueOnce(new Error('WG could not render the charts for this export. Try the export again.'));
    }
    const fetcher = vi.fn<typeof fetch>(async () => failing === 'map'
      ? new Response(JSON.stringify({ detail: 'renderer down' }), { status: 500 })
      : new Response(JSON.stringify({ image: 'data:image/png;base64,Ag==' }), { status: 200 }));
    const saveBlob = vi.fn();
    await expect(runExportFormat('png', { jobStem: 'horn_1', preferences: preferencesStore.getSnapshot(), result, fetcher, saveBlob }))
      .rejects.toThrow(failing === 'map' ? 'renderer down' : 'WG could not render the charts for this export');
    expect(saveBlob).not.toHaveBeenCalled();
    reportChart.render.mockReset().mockImplementation(async () => 'data:image/png;base64,iVBORw==');
  });

  it.each(['map', 'last chart'] as const)('publishes no png file in a Workspace bundle when the %s fails', async (failing) => {
    if (failing === 'last chart') {
      reportChart.render
        .mockResolvedValueOnce('data:image/png;base64,iVBORw==')
        .mockResolvedValueOnce('data:image/png;base64,iVBORw==')
        .mockRejectedValueOnce(new Error('WG could not render the charts for this export. Try the export again.'));
    }
    const written: string[][] = [];
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      const path = String(input);
      if (path === '/api/render-directivity') {
        return failing === 'map'
          ? new Response(JSON.stringify({ detail: 'renderer down' }), { status: 500 })
          : new Response(JSON.stringify({ image: 'data:image/png;base64,Ag==' }), { status: 200 });
      }
      if (path === '/api/workspace/write-export') {
        const members = workspacePayload(init).members.map((member: { relative_path: string }) => member.relative_path);
        written.push(members);
        return new Response(JSON.stringify({ directory: 'C:/output/horn_1', files: members.map((name: string) => `C:/output/horn_1/${name}`) }), { status: 200 });
      }
      return new Response('not found', { status: 404 });
    });
    const bundle = await runWorkspaceExportBundle({ result, jobStem: 'horn_1', preferences: preferencesStore.getSnapshot(), fetcher }, ['csv', 'png']);
    expect(bundle.failures).toHaveLength(1);
    expect(bundle.failures[0]).toMatchObject({ format: 'png' });
    expect(written.flat().filter((name) => name.endsWith('.png'))).toEqual([]);
    expect(written.flat()).toContain('horn_1.csv');
    reportChart.render.mockReset().mockImplementation(async () => 'data:image/png;base64,iVBORw==');
  });

  it('hatches a combined channel\'s unqualified bands from its members, as the SPL card does', async () => {
    reportChart.render.mockClear();
    // Stored before qualification existed: the combined channel has no flags of
    // its own, so only its members (read through the envelope) can mark it.
    const wrapper = JSON.parse(new TextDecoder().decode(readFileSync('../server/tests/data/power-qualification-speaker2-v4.json'))) as ResultPayload;
    const combined = wrapper.channels!.combined as ResultPayload;
    expect(chartUnqualifiedBands([{ id: 'p', label: 'Combined', result: combined, primary: true }])).toEqual([]);
    const card = chartUnqualifiedBands([{ id: 'p', label: 'Combined', result: combined, wrapper, primary: true }]);
    expect(card.length).toBeGreaterThan(0);
    await runExportFormat('png', { jobStem: 'spk', channelId: 'combined', preferences: preferencesStore.getSnapshot(), result: wrapper, fetcher: vi.fn<typeof fetch>(), saveBlob: vi.fn() });
    const [spec] = reportChart.render.mock.calls[0] as unknown as [{ title: string; bands?: Array<{ x0: number; x1: number }> }];
    expect(spec.title).toBe('Sound pressure level');
    expect(spec.bands?.map(({ x0, x1 }) => [x0, x1])).toEqual(card.map(({ fromHz, toHz }) => [fromHz, toHz]));
  });

  it('draws the bundle charts the preferences choose, as the cards do', async () => {
    reportChart.render.mockClear();
    const angled: ResultPayload = {
      ...result,
      directivity: { horizontal: [[[-30, -3], [0, 0], [30, -4]], [[-30, -6], [0, 0], [30, -7]]] },
      directivity_phase: { horizontal: [[[-30, 5], [0, 0], [30, 6]], [[-30, 8], [0, 0], [30, 9]]] },
      beam_shape: { frequencies: [100, 200], horizontal_beamwidth_deg: [120, 90], vertical_beamwidth_deg: [100, 80] },
    } as unknown as ResultPayload;
    const preferences = { ...preferencesStore.getSnapshot(), smoothing: 'none' as const, splPhase: true, impedanceDisplay: 'magnitude_phase' as const, measurementPlane: 'horizontal' as const, measurementAngles: [30] };
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ image: 'data:image/png;base64,Ag==' }), { status: 200 }));
    expect(await runExportFormat('png', { jobStem: 'horn_1', preferences, result: angled, fetcher, saveBlob: vi.fn() })).toEqual([
      'horn_1_frequency_response.png', 'horn_1_directivity_index.png', 'horn_1_impedance.png', 'horn_1_beam_shape.png', 'horn_1_directivity_map.png',
    ]);
    const specs = reportChart.render.mock.calls.map((call) => (call as unknown as [{ title: string; y2?: { label: string }; series: Array<{ label: string; axis?: number; y: Array<number | null> }> }])[0]);
    const [spl, , impedance, beam] = specs;
    // SPL at the chosen angle, with its phase on the right-hand axis.
    expect(spl.series.some(({ label }) => label.includes('30'))).toBe(true);
    expect(spl.y2?.label).toBe('Phase [°]');
    // Impedance as magnitude and phase.
    expect(impedance.y2?.label).toBe('Phase [°]');
    // beam_shape.png is the H/V beam width.
    expect(beam).toMatchObject({ title: 'Beam width' });
    expect(beam.series.map(({ y }) => y)).toEqual([[120, 90], [100, 80]]);
  });

  it('dispatches one on-axis FRD download per result channel', async () => {
    const wrapped: ResultPayload = {
      frequencies: [], channel_order: ['drive-hf', 'drive-mf'],
      channels: { 'drive-hf': result, 'drive-mf': result },
    };
    const saveText = vi.fn();
    const bundle = await runExportBundle({
      result: wrapped, jobStem: 'horn_1', preferences: preferencesStore.getSnapshot(), saveText,
    }, ['on_axis_frd']);

    expect(bundle).toEqual({
      files: ['horn_1-drive-hf.frd', 'horn_1-drive-mf.frd'], failures: [],
    });
    expect(saveText.mock.calls.map(([, filename]) => filename)).toEqual([
      'horn_1-drive-hf.frd', 'horn_1-drive-mf.frd',
    ]);
  });

  it('dispatches a manual polar FRD set through the selected Workspace', async () => {
    const polar: ResultPayload = {
      frequencies: [1000], spl_on_axis: { frequencies: [1000], spl: [90], phase_degrees: [0] },
      directivity: {
        horizontal: [[[-30, -6], [0, 0], [30, -6]]],
        vertical: [[[-20, -5], [0, 0], [20, -5]]],
      },
    };
    const requests: Array<{ path: string; init?: RequestInit }> = [];
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      const path = String(input);
      requests.push({ path, init });
      if (path === '/api/workspace/path') return new Response(JSON.stringify({ selected: true, path: '/chosen' }), { status: 200 });
      return new Response(JSON.stringify({
        directory: '/chosen/horn_1',
        files: Array.from({ length: 6 }, (_, index) => `/chosen/horn_1/${index}.frd`),
      }), { status: 200 });
    });

    const files = await runExportFormat('polar_frd', {
      result: polar, jobStem: 'horn_1', preferences: preferencesStore.getSnapshot(), fetcher,
    });

    expect(files).toHaveLength(6);
    expect(requests.map(({ path }) => path)).toEqual(['/api/workspace/path', '/api/workspace/write-export']);
    const payload = workspacePayload(requests[1].init);
    expect(payload.subdirectory).toBe('horn_1');
    expect(payload.members.map((member: { relative_path: string }) => member.relative_path)).toEqual([
      'hor/horn_1 -30.frd', 'hor/horn_1 0.frd', 'hor/horn_1 30.frd',
      'ver/horn_1 -20.frd', 'ver/horn_1 0.frd', 'ver/horn_1 20.frd',
    ]);
  });

  it('writes all polar result channels in one manual Workspace request', async () => {
    const channel: ResultPayload = {
      frequencies: [1000], spl_on_axis: { frequencies: [1000], spl: [90] },
      directivity: { horizontal: [[[0, 0]]] },
    };
    const wrapped: ResultPayload = {
      frequencies: [], channel_order: ['drive-hf', 'drive-mf'],
      channels: { 'drive-hf': channel, 'drive-mf': channel },
    };
    let members: Array<{ relative_path: string }> = [];
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      if (String(input) === '/api/workspace/path') return new Response(JSON.stringify({ selected: true }), { status: 200 });
      members = workspacePayload(init).members;
      return new Response(JSON.stringify({
        directory: '/chosen/horn_1', files: members.map(({ relative_path }) => `/chosen/horn_1/${relative_path}`),
      }), { status: 200 });
    });

    const bundle = await runExportBundle({
      result: wrapped, jobStem: 'horn_1', preferences: preferencesStore.getSnapshot(), fetcher,
    }, ['polar_frd']);

    expect(fetcher.mock.calls.map(([input]) => String(input))).toEqual(['/api/workspace/path', '/api/workspace/write-export']);
    expect(members.map(({ relative_path }) => relative_path)).toEqual([
      'hor/horn_1-drive-hf 0.frd', 'hor/horn_1-drive-mf 0.frd',
    ]);
    expect(bundle.failures).toEqual([]);
  });

  it('preserves polar Workspace cancellation as a format failure without writing', async () => {
    const polar: ResultPayload = {
      frequencies: [1000], spl_on_axis: { frequencies: [1000], spl: [90] },
      directivity: { horizontal: [[[0, 0]]] },
    };
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ selected: false }), { status: 200 }));
    const bundle = await runExportBundle({
      result: polar, jobStem: 'horn_1', preferences: preferencesStore.getSnapshot(), fetcher,
    }, ['polar_frd']);

    expect(fetcher.mock.calls.map(([input]) => String(input))).toEqual(['/api/workspace/path', '/api/folders/select']);
    expect(bundle).toEqual({
      files: [], failures: [{ format: 'polar_frd', reason: 'Export cancelled. No files were written.' }],
    });
  });

  it('stages automatic polar FRDs with their plane directories in the common Workspace write', async () => {
    const polar: ResultPayload = {
      frequencies: [1000], spl_on_axis: { frequencies: [1000], spl: [90] },
      directivity: { horizontal: [[[-30, -6], [0, 0], [30, -6]]] },
    };
    let writePayload: { members: Array<{ relative_path: string }> } | undefined;
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      expect(String(input)).toBe('/api/workspace/write-export');
      writePayload = workspacePayload(init);
      return new Response(JSON.stringify({
        directory: '/output/horn_1',
        files: writePayload!.members.map(({ relative_path }) => `/output/horn_1/${relative_path}`),
      }), { status: 200 });
    });

    const bundle = await runWorkspaceExportBundle({
      result: polar, jobStem: 'horn_1', preferences: preferencesStore.getSnapshot(), fetcher,
    }, ['polar_frd']);

    expect(bundle.failures).toEqual([]);
    expect(writePayload?.members.map(({ relative_path }) => relative_path)).toEqual([
      'hor/horn_1 -30.frd', 'hor/horn_1 0.frd', 'hor/horn_1 30.frd',
    ]);
  });

  it('pins distinct config and summary suffixes, including the config fallback', async () => {
    const saveText = vi.fn();
    const preferences = preferencesStore.getSnapshot();
    const configFetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ text: 'cfg' }), { status: 200 }));
    expect(await runExportFormat('mwg_config', {
      jobStem: '123_asro68', preferences, design: designForFamily('OSSE'), fetcher: configFetcher, saveText,
    })).toEqual(['123_asro68_config.cfg']);
    expect(JSON.parse(String(configFetcher.mock.calls[0][1]?.body)).filename).toBe('123_asro68_config.cfg');

    expect(await runExportFormat('txt', {
      jobStem: '123_asro68', preferences, result, saveText,
    })).toEqual(['123_asro68_summary.txt']);
  });

  it.each([false, true])('downloads a mesh with regenerated=%s while preserving its bytes', async (regenerated) => {
    const saveBlob = vi.fn();
    const reportNotice = vi.fn();
    const expectedName = `123_asro68${regenerated ? '-regenerated' : ''}.msh`;
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response('stored mesh bytes', {
      status: 200,
      headers: { 'Content-Disposition': 'attachment; filename="artifact-uuid.msh"', ...(regenerated ? { 'X-WG2-Mesh-Regenerated': '1', 'X-WG2-Mesher-Version': '0.2.8' } : {}) },
    }));
    const filename = await downloadMeshArtifact({
      id: 'artifact-uuid', run_number: 123, label: 'asro68', config_summary: {},
    }, reportNotice, fetcher, saveBlob);

    expect(fetcher).toHaveBeenCalledWith('/api/mesh-artifact/artifact-uuid', expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(new Headers(fetcher.mock.calls[0][1]?.headers).get('X-WG-Operation-Id')).toBeTruthy();
    expect(filename).toBe(expectedName);
    expect(saveBlob).toHaveBeenCalledTimes(1);
    expect(saveBlob.mock.calls[0][1]).toBe(expectedName);
    expect(reportNotice).toHaveBeenCalledTimes(regenerated ? 1 : 0);
    if (regenerated) expect(reportNotice).toHaveBeenCalledWith('#123 · asro68: the original mesh is gone, so WG rebuilt it with the current version (0.2.8) and may differ from the mesh that was solved.');
    expect(await (saveBlob.mock.calls[0][0] as Blob).text()).toBe('stored mesh bytes');
  });

  it('reads a structured refusal through the shared wording instead of "[object Object]"', async () => {
    const refused = (detail: unknown) => vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ detail }), { status: 422 }));
    const job = { id: 'refused', run_number: 7, label: 'refused', config_summary: {} };
    await expect(downloadMeshArtifact(job, vi.fn(), refused({ message: 'This run kept no mesh.', next_step: 'Solve it again to export the mesh.' }), vi.fn()))
      .rejects.toThrow('This run kept no mesh. Solve it again to export the mesh.');
    await expect(downloadMeshArtifact(job, vi.fn(), refused([{ loc: ['body', 'x'], msg: 'Input should be a valid number', type: 'float_parsing' }]), vi.fn()))
      .rejects.not.toThrow('[object Object]');
    await expect(downloadMeshArtifact(job, vi.fn(), vi.fn<typeof fetch>().mockResolvedValue(new Response('', { status: 500 })), vi.fn()))
      .rejects.toThrow('WG could not export the mesh for this run. Try the export again.');
  });

  it('sends a chosen destination as a handle, and the files into that folder itself', async () => {
    const requests: Array<{ path: string; init?: RequestInit }> = [];
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      const path = String(input);
      requests.push({ path, init });
      if (path === '/api/workspace/write-export') {
        return new Response(JSON.stringify({
          directory: '/exports',
          files: ['/exports/horn_1.csv'],
          replaced: ['/exports/horn_1.csv'],
        }), { status: 200 });
      }
      return new Response('not found', { status: 404 });
    });

    const bundle = await runWorkspaceExportBundle({
      result,
      jobStem: 'horn_1',
      workspaceSubdirectory: 'design_a/run_3',
      preferences: preferencesStore.getSnapshot(),
      fetcher,
      destinationToken: 'handle-1',
    }, ['csv'], 'overwrite');

    const payload = workspacePayload(requests.find(({ path }) => path === '/api/workspace/write-export')!.init);
    // The folder the user chose receives the files. Nesting the run's own
    // subdirectory under it would be answering a question nobody asked.
    expect(payload.subdirectory).toBe('');
    expect(payload.destination).toBe('handle-1');
    expect(payload.existing).toBe('overwrite');
    expect(bundle.directory).toBe('/exports');
    // What was overwritten, so the caller can say so rather than counting.
    expect(bundle.replaced).toEqual(['/exports/horn_1.csv']);
  });

  it('asks once before replacing files in the chosen folder, then repeats as overwrite', async () => {
    const requests: Array<{ path: string; init?: RequestInit }> = [];
    let refused = false;
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      const path = String(input);
      requests.push({ path, init });
      if (path !== '/api/workspace/write-export') return new Response('not found', { status: 404 });
      if (!refused) {
        refused = true;
        return new Response(JSON.stringify({
          code: 'export_collision',
          detail: '1 file(s) would be replaced',
          directory: '/exports',
          paths: ['/exports/horn_1.csv'],
        }), { status: 409 });
      }
      return new Response(JSON.stringify({
        directory: '/exports',
        files: ['/exports/horn_1.csv'],
        replaced: ['/exports/horn_1.csv'],
      }), { status: 200 });
    });
    const asked: ExportCollision[] = [];

    const bundle = await runWorkspaceExportBundle({
      result,
      jobStem: 'horn_1',
      preferences: preferencesStore.getSnapshot(),
      fetcher,
      destinationToken: 'handle-1',
      confirmReplacements: async (collision) => { asked.push(collision); return true; },
    }, ['csv'], 'confirm');

    // One question, carrying every file, before anything was written.
    expect(asked).toEqual([{
      directory: '/exports',
      paths: ['/exports/horn_1.csv'],
    }]);
    const writes = requests.filter(({ path }) => path === '/api/workspace/write-export');
    expect(writes.map(({ init }) => workspacePayload(init).existing)).toEqual(['confirm', 'overwrite']);
    // The same bytes, not a rebuilt bundle: re-rendering would restamp the
    // timestamps, so the retry would not be the export that was asked about.
    const [first, second] = writes.map(({ init }) => workspacePayload(init));
    expect(second.members.map(({ relative_path }) => relative_path))
      .toEqual(first.members.map(({ relative_path }) => relative_path));
    expect(await second.members[0].blob.text()).toBe(await first.members[0].blob.text());
    expect(bundle.files).toEqual(['/exports/horn_1.csv']);
    expect(bundle.replaced).toEqual(['/exports/horn_1.csv']);
  });

  it('writes nothing when the replacement is declined', async () => {
    const requests: string[] = [];
    const fetcher = vi.fn<typeof fetch>(async (input) => {
      requests.push(String(input));
      return new Response(JSON.stringify({
        code: 'export_collision',
        detail: '1 file(s) would be replaced',
        directory: '/exports',
        paths: ['/exports/horn_1.csv'],
      }), { status: 409 });
    });

    await expect(runWorkspaceExportBundle({
      result,
      jobStem: 'horn_1',
      preferences: preferencesStore.getSnapshot(),
      fetcher,
      destinationToken: 'handle-1',
      confirmReplacements: async () => false,
    }, ['csv'], 'confirm')).rejects.toBeInstanceOf(ExportCancelledError);

    // The refusal, and no retry: declining is an answer, not a failure to work
    // around.
    expect(requests.filter((path) => path === '/api/workspace/write-export')).toHaveLength(1);
  });

  it('raises the refusal rather than overwriting when nothing can ask', async () => {
    // A caller with no confirmation route must not inherit a silent overwrite.
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
      code: 'export_collision',
      detail: '1 file(s) would be replaced',
      directory: '/exports',
      paths: ['/exports/horn_1.csv'],
    }), { status: 409 }));

    await expect(runWorkspaceExportBundle({
      result,
      jobStem: 'horn_1',
      preferences: preferencesStore.getSnapshot(),
      fetcher,
      destinationToken: 'handle-1',
    }, ['csv'], 'confirm')).rejects.toBeInstanceOf(ExportCollisionError);
  });

  it('leaves automatic export non-interactive: no handle, and the run\'s own folder', async () => {
    const requests: Array<{ path: string; init?: RequestInit }> = [];
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      const path = String(input);
      requests.push({ path, init });
      if (path === '/api/workspace/write-export') {
        return new Response(JSON.stringify({
          directory: 'C:/output/design_a/run_3',
          files: ['C:/output/design_a/run_3/horn_1.csv'],
        }), { status: 200 });
      }
      return new Response('not found', { status: 404 });
    });

    await runWorkspaceExportBundle({
      result,
      jobStem: 'horn_1',
      workspaceSubdirectory: 'design_a/run_3',
      jobId: 'stored-run',
      preferences: preferencesStore.getSnapshot(),
      fetcher,
    }, ['csv']);

    const payload = workspacePayload(requests.find(({ path }) => path === '/api/workspace/write-export')!.init);
    expect(payload.subdirectory).toBe('design_a/run_3');
    expect(payload.destination).toBeNull();
    expect(payload.jobId).toBe('stored-run');
    expect(payload.existing).toBe('merge_identical');
    // Nothing asked the user anything: a background export that opened a
    // folder dialog would interrupt a solve nobody is watching.
    expect(requests.map(({ path }) => path)).not.toContain('/api/workspace/export-destination');
  });

  it('writes automatic text and binary exports to the Workspace without browser downloads', async () => {
    const requests: Array<{ path: string; init?: RequestInit }> = [];
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      const path = String(input);
      requests.push({ path, init });
      if (path === '/api/render-directivity') {
        return new Response(JSON.stringify({ image: 'data:image/png;base64,BAUG' }), { status: 200 });
      }
      if (path === '/api/workspace/write-export') {
        return new Response(JSON.stringify({
          directory: 'C:/output/horn_1',
          files: ['C:/output/horn_1/horn_1.csv', 'C:/output/horn_1/horn_1_directivity_map.png'],
        }), { status: 200 });
      }
      return new Response('not found', { status: 404 });
    });

    const bundle = await runWorkspaceExportBundle({
      result,
      jobStem: 'horn_1',
      preferences: preferencesStore.getSnapshot(),
      fetcher,
    }, ['csv', 'png']);

    expect(bundle).toMatchObject({ directory: 'C:/output/horn_1', failures: [] });
    const write = requests.find(({ path }) => path === '/api/workspace/write-export')!;
    const payload = workspacePayload(write.init);
    expect(payload).toMatchObject({ subdirectory: 'horn_1', existing: 'merge_identical' });
    expect(payload.members.map((member: { relative_path: string }) => member.relative_path)).toEqual([
      'horn_1.csv', 'horn_1_frequency_response.png', 'horn_1_directivity_index.png', 'horn_1_impedance.png', 'horn_1_directivity_map.png',
    ]);
    expect(Array.from(new Uint8Array(await payload.members[4].blob.arrayBuffer()))).toEqual([4, 5, 6]);
    expect(requests.filter(({ path }) => path === '/api/render-directivity')).toHaveLength(1);
    expect(requests.map(({ path }) => path)).not.toContain('/api/render-charts');
  });

  it('auto-saves a retained mesh by reference: the server copies it, nothing is downloaded', async () => {
    const reportNotice = vi.fn();
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
      directory: 'C:/output/123_asro68', files: ['C:/output/123_asro68/123_asro68.msh'],
    }), { status: 200 }));

    const saved = await saveMeshArtifactToWorkspace({
      id: 'artifact-uuid', archive_folder: 'asro68', archive_run: '123_asro68', run_number: 123, label: 'asro68', config_summary: {},
    }, reportNotice, fetcher);

    expect(saved).toBe('C:/output/123_asro68/123_asro68.msh');
    expect(fetcher.mock.calls.map(([input]) => String(input))).toEqual(['/api/workspace/write-export']);
    const payload = workspacePayload(fetcher.mock.calls[0][1]);
    expect(payload).toMatchObject({ subdirectory: 'asro68/123_asro68', existing: 'merge_identical', jobId: 'artifact-uuid' });
    expect(payload.members).toEqual([{ relative_path: '123_asro68.msh', blob: undefined, server: { member: 'mesh' } }]);
    expect(reportNotice).not.toHaveBeenCalled();
  });

  it.each([false, true])('auto-saves a mesh the server no longer holds (regenerated=%s) as before', async (regenerated) => {
    const reportNotice = vi.fn();
    const expectedName = `123_asro68${regenerated ? '-regenerated' : ''}.msh`;
    let writes = 0;
    const fetcher = vi.fn<typeof fetch>(async (input) => {
      if (String(input) === '/api/mesh-artifact/artifact-uuid') return new Response('mesh bytes', { status: 200, headers: regenerated ? { 'X-WG2-Mesh-Regenerated': '1' } : {} });
      expect(reportNotice).not.toHaveBeenCalled();
      if (++writes === 1) return new Response(JSON.stringify({
        code: 'server_member_unavailable', detail: 'This run did not keep its mesh.',
      }), { status: 409 });
      return new Response(JSON.stringify({
        directory: 'C:/output/123_asro68',
        files: [`C:/output/123_asro68/${expectedName}`],
      }), { status: 200 });
    });

    const saved = await saveMeshArtifactToWorkspace({
      id: 'artifact-uuid', archive_folder: 'asro68', archive_run: '123_asro68', run_number: 123, label: 'asro68', config_summary: {},
    }, reportNotice, fetcher);

    expect(saved).toBe(`C:/output/123_asro68/${expectedName}`);
    const payload = workspacePayload(fetcher.mock.calls[2][1]);
    expect(payload.subdirectory).toBe('asro68/123_asro68');
    expect(payload.members[0].relative_path).toBe(expectedName);
    expect(await payload.members[0].blob.text()).toBe('mesh bytes');
    expect(reportNotice).toHaveBeenCalledTimes(regenerated ? 1 : 0);
    if (regenerated) expect(reportNotice).toHaveBeenCalledWith('#123 · asro68: the original mesh is gone, so WG rebuilt it with the current version and may differ from the mesh that was solved.');
    expect(fetcher.mock.calls.map(([input]) => String(input))).toEqual([
      '/api/workspace/write-export', '/api/mesh-artifact/artifact-uuid', '/api/workspace/write-export',
    ]);
  });

  it('keeps a mesh save refused by the folder a destination conflict, without downloading', async () => {
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
      detail: 'A different file already exists at C:/output/123_asro68/123_asro68.msh. Move it, or choose another folder.',
    }), { status: 409 }));
    await expect(saveMeshArtifactToWorkspace({
      id: 'artifact-uuid', archive_folder: 'asro68', archive_run: '123_asro68', run_number: 123, label: 'asro68', config_summary: {},
    }, vi.fn(), fetcher)).rejects.toMatchObject({ destinationConflict: true });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it('adds both radiation artifacts to the permanent run archive only when retained', async () => {
    const job: JobItem = {
      id: 'archive-cardioid', archive_folder: 'Cardioid', archive_run: '8_Cardioid', run_number: 8, parent_job_id: null, status: 'complete', progress: 1,
      stage: null, stage_message: null, created_at: '2026-08-20T10:00:00Z', queued_at: '2026-08-20T10:00:00Z',
      started_at: '2026-08-20T10:00:01Z', completed_at: '2026-08-20T10:02:00Z', config_summary: {},
      solve_options: {} as JobItem['solve_options'], has_results: true, has_mesh_artifact: false,
      has_radiation_impedance_artifact: true, radiation_impedance_artifact_bytes: 3, persistence_warnings: [],
      label: 'Cardioid', error_message: null, cancellation_requested: false, mesh_stats: null, script_snapshot: null,
      design_revision: 1, polar_grid: {}, rating: null, exported_files: [], auto_export_completed_at: null,
      auto_export_formats: {}, archived_at: null, raw_results_file: null, mesh_artifact_file: null, log_tail: [],
    };
    const writes: ReturnType<typeof workspacePayload>[] = [];
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      expect(activityStore.getSnapshot()).toMatchObject([{ kind: 'export', label: 'Archiving the run' }]);
      const path = String(input);
      if (init?.method === 'DELETE') return new Response(JSON.stringify({ released: true }));
      if (path === '/api/jobs/archive-cardioid/archive-snapshot?members=held') return new Response(JSON.stringify({
        schema_version: 1,
        results: finalResult,
        results_sha256: 'a'.repeat(64),
        hold: 'held-cardioid',
        mesh_artifact: null,
        pressure_bases: [{ channel_id: 'mf drive', member: 'pressure_basis/mf drive', bytes: 3 }],
        radiation_impedance: { member: 'radiation_impedance', bytes: 3, presentation: radiation },
      }), { status: 200 });
      if (path === '/api/workspace/write-export') {
        const payload = workspacePayload(init);
        writes.push(payload);
        return new Response(JSON.stringify({
          directory: `/workspace/${payload.subdirectory}`,
          files: payload.members.map(({ relative_path }) => `/workspace/${payload.subdirectory}/${relative_path}`),
        }), { status: 200 });
      }
      return new Response('not found', { status: 404 });
    });

    const started = vi.spyOn(activityStore, 'start');
    await archiveRunToWorkspace(job, preferencesStore.getSnapshot(), fetcher);
    expect(started).toHaveBeenCalledTimes(1);
    expect(activityStore.getSnapshot()).toHaveLength(0);
    started.mockRestore();
    expect(writes.every(({ jobId }) => jobId === job.id)).toBe(true);
    expect(writes.flatMap(({ members }) => members.map(({ relative_path }) => relative_path))).not.toContain('design.json');
    expect(writes[0].members.map(({ relative_path }) => relative_path)).toEqual([
      '8_Cardioid.json',
      '8_Cardioid.csv',
      '8_Cardioid_derived_acoustics.csv',
      '8_Cardioid_derived_acoustics.json',
      '8_Cardioid_report.html',
      'mf-drive_pressure_basis.npz',
      '8_Cardioid_radiation_impedance.npz',
      '8_Cardioid_radiation_impedance.csv',
    ]);
    // The server writes the bytes it holds: only their names cross.
    expect(writes[0].members.map(({ server }) => server)).toEqual([
      undefined, undefined, undefined, undefined, undefined,
      { hold: 'held-cardioid', member: 'pressure_basis/mf drive' },
      { hold: 'held-cardioid', member: 'radiation_impedance' },
      undefined,
    ]);
    const record = JSON.parse(await writes.at(-1)!.members[0].blob.text());
    expect(record.artifacts.radiationImpedance.bytes).toBe(3);
    // The snapshot is read once, and its held copy released after the last write.
    const calls = fetcher.mock.calls.map(([input, init]) => `${init?.method ?? 'GET'} ${String(input)}`);
    expect(calls.filter((call) => call.includes('archive-cardioid'))).toEqual([
      'GET /api/jobs/archive-cardioid/archive-snapshot?members=held',
      'DELETE /api/jobs/archive-cardioid/archive-holds/held-cardioid',
    ]);
    expect(calls.at(-1)).toBe('DELETE /api/jobs/archive-cardioid/archive-holds/held-cardioid');
  });

  it.each(['missing', 'pinned', 'legacy-record', 'retryable'])('preserves the CAD reference before publishing run.json (%s)', async (outcome) => {
    // The placement answer used to be discarded, so a run could be marked
    // archived without the model it was solved from and nothing said so.
    const job = {
      id: 'archive-cad', archive_folder: 'Tritonia', archive_run: '21_Tritonia', run_number: 21, parent_job_id: null, status: 'complete', progress: 1,
      stage: null, stage_message: null, created_at: '2026-08-20T10:00:00Z', queued_at: '2026-08-20T10:00:00Z',
      started_at: '2026-08-20T10:00:01Z', completed_at: '2026-08-20T10:02:00Z',
      config_summary: { geometry_type: 'imported' },
      solve_options: {} as JobItem['solve_options'], has_results: true, has_mesh_artifact: false,
      cad_source: {
        ingest_id: 'wgi_01', lineage_id: 'wgl_a', archive_stem: 'Tritonia',
        return_state_hash: 'sha256:aaa',
      },
      label: 'Tritonia', error_message: null, cancellation_requested: false, mesh_stats: null,
      script_snapshot: null, design_revision: 1, polar_grid: {}, rating: null, exported_files: [],
      auto_export_completed_at: null, auto_export_formats: {}, archived_at: null,
      raw_results_file: null, mesh_artifact_file: null, log_tail: [],
    } as unknown as JobItem;
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const records: Record<string, unknown>[] = [];
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      const path = String(input);
      if (init?.method === 'DELETE') return new Response(JSON.stringify({ released: true }));
      if (path === '/api/jobs/archive-cad/archive-snapshot?members=held') return new Response(JSON.stringify({
        schema_version: 1, results: finalResult, results_sha256: 'c'.repeat(64), hold: 'held-cad',
        mesh_artifact: null, pressure_bases: [], radiation_impedance: null,
      }), { status: 200 });
      if (path.endsWith('/mesh-replay')) return new Response(JSON.stringify({ detail: 'Retained CAD originals unavailable' }), { status: 409 });
      if (path === '/api/cadlink/runs/archive-document') {
        expect(records).toHaveLength(0);
        expect(JSON.parse(String(init?.body))).toEqual({ jobId: job.id });
        return new Response(JSON.stringify(outcome === 'pinned' || outcome === 'legacy-record' ? {
          placed: true, relativePath: 'cad/Tritonia_260820-0900_aaa.f3d',
          modelFile: outcome === 'pinned' ? 'cad/Tritonia_260820-0900_aaa.f3d' : null,
        } : {
          placed: false, retryable: outcome === 'retryable', reason: 'The CAD model this run was solved from is not in the project archive.',
        }), { status: 200 });
      }
      const payload = workspacePayload(init);
      for (const member of payload.members) {
        if (member.relative_path === 'run.json') records.push(JSON.parse(await member.blob.text()));
      }
      return new Response(JSON.stringify({
        directory: `/workspace/${payload.subdirectory}`,
        files: payload.members.map(({ relative_path }) => `/workspace/${payload.subdirectory}/${relative_path}`),
      }), { status: 200 });
    });

    if (outcome === 'retryable') {
      await expect(archiveRunToWorkspace(job, preferencesStore.getSnapshot(), fetcher)).rejects.toThrow('not in the project archive');
      expect(records).toHaveLength(0);
      warn.mockRestore();
      return;
    }
    await archiveRunToWorkspace(job, preferencesStore.getSnapshot(), fetcher);
    expect(records).toHaveLength(1);
    expect((records[0].cad as { modelFile?: string }).modelFile)
      .toBe(outcome === 'pinned' ? 'cad/Tritonia_260820-0900_aaa.f3d' : undefined);

    expect(fetcher.mock.calls.map(([input]) => String(input)))
      .toContain('/api/cadlink/runs/archive-document');
    if (outcome === 'missing') expect(warn).toHaveBeenCalledWith(
      expect.stringContaining('not in the project archive'),
    );
    warn.mockRestore();
  });

  it('writes the exact retained mesh copied into the archive snapshot, by reference', async () => {
    const job = {
      id: 'archive-mesh', archive_folder: 'OSSE', archive_run: '9_OSSE', run_number: 9, parent_job_id: null, status: 'complete', progress: 1,
      stage: null, stage_message: null, created_at: '2026-08-20T10:00:00Z', queued_at: '2026-08-20T10:00:00Z',
      started_at: '2026-08-20T10:00:01Z', completed_at: '2026-08-20T10:02:00Z', config_summary: {},
      solve_options: {} as JobItem['solve_options'], has_results: true, has_mesh_artifact: false,
      label: 'Mesh Run', error_message: null, cancellation_requested: false, mesh_stats: null, script_snapshot: null,
      design_revision: 1, polar_grid: {}, rating: null, exported_files: [], auto_export_completed_at: null,
      auto_export_formats: {}, archived_at: null, raw_results_file: null, mesh_artifact_file: null, log_tail: [],
    } satisfies JobItem;
    const writes: ReturnType<typeof workspacePayload>[] = [];
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      if (init?.method === 'DELETE') return new Response(JSON.stringify({ released: true }));
      if (String(input) === '/api/jobs/archive-mesh/archive-snapshot?members=held') return new Response(JSON.stringify({
        schema_version: 1, results: finalResult, results_sha256: 'b'.repeat(64), hold: 'held-mesh',
        mesh_artifact: { member: 'mesh', bytes: 16 }, pressure_bases: [], radiation_impedance: null,
      }), { status: 200 });
      const payload = workspacePayload(init);
      writes.push(payload);
      return new Response(JSON.stringify({
        directory: `/workspace/${payload.subdirectory}`,
        files: payload.members.map(({ relative_path }: { relative_path: string }) => `/workspace/${payload.subdirectory}/${relative_path}`),
      }), { status: 200 });
    });

    await archiveRunToWorkspace(job, preferencesStore.getSnapshot(), fetcher);

    // One write carries every held member, after the files built here, so a
    // lost hold writes nothing; run.json follows in its own write.
    expect(writes).toHaveLength(2);
    expect(writes[0].members.map(({ relative_path }) => relative_path)).toEqual([
      '9_Mesh_Run.json', '9_Mesh_Run.csv', '9_Mesh_Run_derived_acoustics.csv',
      '9_Mesh_Run_derived_acoustics.json', '9_Mesh_Run_report.html', '9_Mesh_Run.msh',
    ]);
    // The exact mesh the snapshot copied, written by the server from its hold.
    expect(writes[0].members.at(-1)!.server).toEqual({ hold: 'held-mesh', member: 'mesh' });
    expect(JSON.parse(await writes[1].members[0].blob.text()).artifacts.mesh)
      .toBe('9_Mesh_Run.msh');
  });

  it.each([
    ['small', 100, 100],
    ['300 MB class, over the per-request upload cap', 200, 100],
    ['near the 1 GiB hold limit', 700, 300],
  ] as const)('writes every held member with the bundle in one request (%s)', async (_case, basisMb, meshMb) => {
    const MB = 1024 * 1024;
    const job = {
      id: 'archive-big', archive_folder: 'OSSE', archive_run: '9_OSSE', run_number: 9, parent_job_id: null, status: 'complete', progress: 1,
      stage: null, stage_message: null, created_at: '2026-08-20T10:00:00Z', queued_at: '2026-08-20T10:00:00Z',
      started_at: '2026-08-20T10:00:01Z', completed_at: '2026-08-20T10:02:00Z', config_summary: {},
      solve_options: {} as JobItem['solve_options'], has_results: true, has_mesh_artifact: false,
      label: 'Mesh Run', error_message: null, cancellation_requested: false, mesh_stats: null, script_snapshot: null,
      design_revision: 1, polar_grid: {}, rating: null, exported_files: [], auto_export_completed_at: null,
      auto_export_formats: {}, archived_at: null, raw_results_file: null, mesh_artifact_file: null, log_tail: [],
    } satisfies JobItem;
    const writes: ReturnType<typeof workspacePayload>[] = [];
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      if (init?.method === 'DELETE') return new Response(JSON.stringify({ released: true }));
      if (String(input).endsWith('/archive-snapshot?members=held')) return new Response(JSON.stringify({
        schema_version: 1, results: finalResult, results_sha256: 'b'.repeat(64), hold: 'held-big',
        // Sizes only: the server holds the bytes, so none are allocated here.
        mesh_artifact: { member: 'mesh', bytes: meshMb * MB },
        pressure_bases: [{ channel_id: 'drive', member: 'pressure_basis/drive', bytes: basisMb * MB }],
        radiation_impedance: null,
      }));
      const payload = workspacePayload(init);
      writes.push(payload);
      return new Response(JSON.stringify({
        directory: `/workspace/${payload.subdirectory}`,
        files: payload.members.map(({ relative_path }) => `/workspace/${payload.subdirectory}/${relative_path}`),
      }));
    });
    const files = await archiveRunToWorkspace(job, preferencesStore.getSnapshot(), fetcher);
    const names = writes.map(({ members }) => members.map(({ relative_path }) => relative_path));
    const bundle = ['9_Mesh_Run.json', '9_Mesh_Run.csv', '9_Mesh_Run_derived_acoustics.csv',
      '9_Mesh_Run_derived_acoustics.json', '9_Mesh_Run_report.html', 'drive_pressure_basis.npz'];
    // Held members are not uploaded, so their size never splits the write.
    expect(names).toEqual([[...bundle, '9_Mesh_Run.msh'], ['run.json']]);
    expect(writes[0].members.filter(({ server }) => server).map(({ relative_path }) => relative_path))
      .toEqual(['drive_pressure_basis.npz', '9_Mesh_Run.msh']);
    expect(files.map((file) => file.split('/').at(-1))).toEqual([...bundle, '9_Mesh_Run.msh', 'run.json']);
    expect(JSON.parse(await writes.at(-1)!.members[0].blob.text()).artifacts.mesh).toBe('9_Mesh_Run.msh');
  });

  it.each([1, 2])('takes a fresh snapshot when a held copy is lost (%s time(s)), leaving nothing partial', async (losses) => {
    const job = {
      id: 'archive-lost', archive_folder: 'OSSE', archive_run: '9_OSSE', run_number: 9, parent_job_id: null, status: 'complete', progress: 1,
      stage: null, stage_message: null, created_at: '2026-08-20T10:00:00Z', queued_at: '2026-08-20T10:00:00Z',
      started_at: '2026-08-20T10:00:01Z', completed_at: '2026-08-20T10:02:00Z', config_summary: {},
      solve_options: {} as JobItem['solve_options'], has_results: true, has_mesh_artifact: false,
      label: 'Mesh Run', error_message: null, cancellation_requested: false, mesh_stats: null, script_snapshot: null,
      design_revision: 1, polar_grid: {}, rating: null, exported_files: [], auto_export_completed_at: null,
      auto_export_formats: {}, archived_at: null, raw_results_file: null, mesh_artifact_file: null, log_tail: [],
    } satisfies JobItem;
    let snapshots = 0;
    let refusals = 0;
    const written: ReturnType<typeof workspacePayload>[] = [];
    const released: string[] = [];
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      if (init?.method === 'DELETE') { released.push(String(input)); return new Response(JSON.stringify({ released: false })); }
      if (String(input).endsWith('/archive-snapshot?members=held')) return new Response(JSON.stringify({
        schema_version: 1, results: finalResult, results_sha256: 'b'.repeat(64), hold: `hold-${++snapshots}`,
        mesh_artifact: { member: 'mesh', bytes: 16 }, pressure_bases: [], radiation_impedance: null,
      }));
      const payload = workspacePayload(init);
      if (payload.members.some(({ server }) => server) && refusals < losses) {
        refusals += 1;
        // The server resolves held members before writing anything.
        return new Response(JSON.stringify({ code: 'server_member_unavailable', detail: 'WG’s copy of this run’s files expired before they were written.' }), { status: 409 });
      }
      written.push(payload);
      return new Response(JSON.stringify({
        directory: `/workspace/${payload.subdirectory}`,
        files: payload.members.map(({ relative_path }) => `/workspace/${payload.subdirectory}/${relative_path}`),
      }));
    });
    const archived = archiveRunToWorkspace(job, preferencesStore.getSnapshot(), fetcher);
    if (losses === 2) {
      await expect(archived).rejects.toThrow('WG’s copy of this run’s files expired twice before it could write them. WG tries the archive again automatically.');
      expect(written).toEqual([]);
    } else {
      await archived;
      expect(written.map(({ existing, members }) => [existing, members.map(({ relative_path }) => relative_path)])).toEqual([
        ['confirm', ['9_Mesh_Run.json', '9_Mesh_Run.csv', '9_Mesh_Run_derived_acoustics.csv',
          '9_Mesh_Run_derived_acoustics.json', '9_Mesh_Run_report.html', '9_Mesh_Run.msh']],
        ['confirm', ['run.json']],
      ]);
      expect(written[0].members.at(-1)!.server).toEqual({ hold: 'hold-2', member: 'mesh' });
    }
    expect(snapshots).toBe(2);
    expect(released).toEqual(['/api/jobs/archive-lost/archive-holds/hold-1', '/api/jobs/archive-lost/archive-holds/hold-2']);
  });

  it.each([['mesh-file', 200], ['mesh-file', 409], ['mesh-file', 503], ['cad', 200], ['cad', 409], ['native', 409], ['design-assembly', 409]] as const)('archives %s replay bytes or an explicit refusal (%s)', async (origin, status) => {
    const job = {
      id: 'archive-replay', archive_folder: 'Driver', archive_run: '9_Driver', run_number: 9, parent_job_id: null, status: 'complete', progress: 1,
      stage: null, stage_message: null, created_at: '2026-08-20T10:00:00Z', queued_at: '2026-08-20T10:00:00Z',
      started_at: '2026-08-20T10:00:01Z', completed_at: '2026-08-20T10:02:00Z',
      config_summary: { geometry_type: 'imported', import_origin: origin },
      solve_options: {} as JobItem['solve_options'], has_results: true, has_mesh_artifact: true,
      label: 'Mesh Run', error_message: null, cancellation_requested: false, mesh_stats: null, script_snapshot: null,
      design_revision: 1, polar_grid: {}, rating: null, exported_files: [], auto_export_completed_at: null,
      auto_export_formats: {}, archived_at: null, raw_results_file: null, mesh_artifact_file: null, log_tail: [],
    } satisfies JobItem;
    const writes: ReturnType<typeof workspacePayload>[] = [];
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      if (init?.method === 'DELETE') return new Response(JSON.stringify({ released: true }));
      if (String(input).endsWith('/archive-snapshot?members=held')) return new Response(JSON.stringify({
        schema_version: 1, results: finalResult, results_sha256: 'b'.repeat(64), hold: 'held-replay',
        mesh_artifact: { member: 'mesh', bytes: 16 }, pressure_bases: [], radiation_impedance: null,
      }));
      if (String(input).endsWith('/mesh-replay')) return new Response(status === 200
        ? '{"exact":"retained package"}' : '{"detail":"Original mesh unavailable"}', { status });
      const payload = workspacePayload(init);
      writes.push(payload);
      return new Response(JSON.stringify({ directory: `/workspace/${payload.subdirectory}`,
        files: payload.members.map(({ relative_path }: { relative_path: string }) => `/workspace/${payload.subdirectory}/${relative_path}`) }));
    });
    if (status === 503) {
      await expect(archiveRunToWorkspace(job, preferencesStore.getSnapshot(), fetcher)).rejects.toThrow('Original mesh unavailable');
      expect(writes).toHaveLength(0);
      return;
    }
    await archiveRunToWorkspace(job, preferencesStore.getSnapshot(), fetcher);
    const members = writes.flatMap(write => write.members);
    const record = JSON.parse(await members.find(member => member.relative_path === 'run.json')!.blob.text());
    const replay = members.find(member => member.relative_path === '9_Mesh_Run.wgreplay');
    if (status === 200) {
      expect(await replay!.blob.text()).toBe('{"exact":"retained package"}');
      expect(record.artifacts.files).toContain('9_Mesh_Run.wgreplay');
      expect(record.artifacts.meshReplay).toEqual({ filename: '9_Mesh_Run.wgreplay' });
    } else {
      expect(replay).toBeUndefined();
      expect(record.artifacts.meshReplay).toEqual({ filename: null, reason: 'Original mesh unavailable' });
      expect(members.some(member => member.relative_path.endsWith('.msh'))).toBe(true);
    }
  });

  it('sends a 64 MiB workspace member as multipart binary without base64 inflation', async () => {
    const content = new Uint8Array(64 * 1024 * 1024);
    content[0] = 0x89;
    content[content.length - 1] = 0xff;
    let requestBody: FormData | undefined;
    const fetcher = vi.fn<typeof fetch>(async (_input, init) => {
      requestBody = init?.body as FormData;
      return new Response(JSON.stringify({ directory: '/workspace/large', files: ['/workspace/large/member.bin'] }), { status: 200 });
    });

    await writeWorkspaceFiles('large', [{
      filename: 'member.bin', blob: new Blob([content]),
    }], fetcher);

    expect(requestBody).toBeInstanceOf(FormData);
    const upload = requestBody!.get('file') as File;
    expect(upload.size).toBe(content.byteLength);
    expect(Array.from(requestBody!.keys())).toEqual([
      'subdirectory', 'existing', 'relative_path', 'file',
    ]);
    expect(requestBody!.has('content_base64')).toBe(false);
    const uploaded = new Uint8Array(await upload.arrayBuffer());
    expect(uploaded[0]).toBe(0x89);
    expect(uploaded[uploaded.length - 1]).toBe(0xff);
  });
});


it('writes through one write-export client: each caller keeps its policy and words', async () => {
  const member = [{ filename: 'one.csv', blob: new Blob(['saved']) }];
  const policies: string[] = [];
  const accepted = vi.fn<typeof fetch>(async (_input, init) => {
    policies.push(String((init!.body as FormData).get('existing')));
    return new Response(JSON.stringify({ directory: 'out', files: ['out/one.csv'] }));
  });
  await expect(writeToOutputFolder('design', member, accepted)).resolves.toEqual({ directory: 'out', files: ['out/one.csv'], replaced: [] });
  await expect(writeWorkspaceFiles('run', member, accepted)).resolves.toEqual({ directory: 'out', files: ['out/one.csv'], replaced: [] });
  expect(policies).toEqual(['overwrite', 'merge_identical']);
  const refused = () => vi.fn().mockResolvedValue(new Response('{}', { status: 500 }));
  await expect(writeToOutputFolder('design', member, refused())).rejects.toThrow('WG could not read the Runs folder. Choose the WG folder in Settings.');
  await expect(writeWorkspaceFiles('run', member, refused())).rejects.toThrow('WG could not save the export files. Check the export folder, then export again.');
});

it('marks lost Workspace publication response bodies as indeterminate', async () => {
  const broken = new Response(new ReadableStream({ start(controller) { controller.error(new Error('Response body connection lost')); } }), { status: 200 });
  const fetcher = vi.fn().mockResolvedValue(broken);
  await expect(writeWorkspaceFiles('run', [{ filename: 'one.csv', blob: new Blob(['saved']) }], fetcher)).rejects.toMatchObject({ publicationUncertain: true });
});

it('marks lost Workspace publication requests as indeterminate but preserves destination conflict classification', async () => {
  await expect(writeWorkspaceFiles('run', [{ filename: 'one.csv', blob: new Blob(['saved']) }], vi.fn().mockRejectedValue(new TypeError('Network lost')))).rejects.toMatchObject({ publicationUncertain: true });
  await expect(writeWorkspaceFiles('run', [{ filename: 'one.csv', blob: new Blob(['saved']) }], vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'Existing file' }), { status: 409 })))).rejects.toMatchObject({ destinationConflict: true });
});

it.each(['format', 'download bundle', 'workspace', 'automatic', 'mesh download', 'mesh save'] as const)(
  '%s registers one export through success or failure', async style => {
    for (const fail of [false, true]) {
      activityStore.reset();
      const first = deferred<Response>(); let calls = 0;
      const started = vi.spyOn(activityStore, 'start');
      const fetcher: typeof fetch = async () => {
        expect(activityStore.getSnapshot()).toHaveLength(1);
        if (++calls === 1) return first.promise;
        return new Response(JSON.stringify({ directory: 'out', files: ['out/horn.stl'] }));
      };
      const context = { jobStem: 'horn', preferences: preferencesStore.getSnapshot(), design: designForFamily('OSSE'), fetcher, saveBlob: vi.fn() };
      const job = { id: 'job', archive_folder: 'horn', archive_run: '1_horn', run_number: 1, label: 'horn', config_summary: {} };
      const pending = style === 'format' ? runExportFormat('stl', context)
        : style === 'download bundle' ? runExportBundle(context, ['stl'])
          : style === 'workspace' ? runWorkspaceExportBundle(context, ['stl'])
            : style === 'automatic' ? runAutomaticWorkspaceExport(context, ['stl'])
              : style === 'mesh download' ? downloadMeshArtifact(job, vi.fn(), fetcher, vi.fn())
                : saveMeshArtifactToWorkspace(job, vi.fn(), fetcher);
      expect(activityStore.getSnapshot()).toHaveLength(1);
      const settled = pending.catch(error => error);
      if (fail) first.reject(new Error('request failed'));
      else first.resolve(style === 'mesh save'
        ? new Response(JSON.stringify({ directory: 'out', files: ['out/1_horn.msh'] }))
        : new Response('geometry'));
      const outcome = await settled;
      if (!fail) expect(outcome).not.toBeInstanceOf(Error);
      expect(started).toHaveBeenCalledTimes(1);
      expect(activityStore.getSnapshot()).toHaveLength(0);
      expect(activityStore.typicalMs('export') === null).toBe(fail);
      started.mockRestore();
    }
  },
);

it.each(['stl', 'step'] as const)('Stop aborts %s and saves no file', async format => {
  activityStore.reset();
  const saveBlob = vi.fn();
  let signal: AbortSignal | null | undefined;
  const fetcher: typeof fetch = (_url, init) => new Promise((_resolve, reject) => {
    signal = init?.signal;
    signal?.addEventListener('abort', () => reject(signal?.reason), { once: true });
  });
  const pending = runExportFormat(format, {
    jobStem: 'horn', preferences: preferencesStore.getSnapshot(), design: designForFamily('OSSE'), fetcher, saveBlob,
  });
  const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  activityStore.getSnapshot()[0].cancel!();
  await rejected;
  expect(signal?.aborted).toBe(true);
  expect(saveBlob).not.toHaveBeenCalled();
  expect(activityStore.getSnapshot()).toEqual([]);
});

it('Stop ends a composed export without continuing with another format', async () => {
  activityStore.reset();
  const saveBlob = vi.fn(); const saveText = vi.fn();
  const fetcher: typeof fetch = (_url, init) => new Promise((_resolve, reject) => {
    const signal = init!.signal!;
    signal.addEventListener('abort', () => reject(signal.reason), { once: true });
  });
  const pending = runExportBundle({ jobStem: 'horn', preferences: preferencesStore.getSnapshot(),
    design: designForFamily('OSSE'), fetcher, saveBlob, saveText }, ['stl', 'mwg_config']);
  const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  activityStore.getSnapshot()[0].cancel!();
  await rejected;
  expect(saveBlob).not.toHaveBeenCalled();
  expect(saveText).not.toHaveBeenCalled();
  expect(activityStore.getSnapshot()).toEqual([]);
});

it('ends an archive activity on a failed snapshot read', async () => {
  activityStore.reset();
  const waiting = deferred<Response>();
  const pending = archiveRunToWorkspace({ id: 'job' } as JobItem, preferencesStore.getSnapshot(), () => waiting.promise);
  expect(activityStore.getSnapshot()).toMatchObject([{ kind: 'export', label: 'Archiving the run' }]);
  const rejected = expect(pending).rejects.toThrow('snapshot failed');
  waiting.reject(new Error('snapshot failed'));
  await rejected;
  expect(activityStore.getSnapshot()).toHaveLength(0);
  expect(activityStore.typicalMs('export')).toBeNull();
});

it('counts completed formats and keeps the activity through the final workspace write', async () => {
  activityStore.reset();
  const counts: Array<{ done: number; total: number }> = [];
  const unsubscribe = activityStore.subscribe(() => {
    const count = activityStore.getSnapshot()[0]?.progress;
    if (count && JSON.stringify(counts.at(-1)) !== JSON.stringify(count)) counts.push(count);
  });
  const written = deferred<Response>();
  const pending = runWorkspaceExportBundle({ jobStem: 'horn', preferences: preferencesStore.getSnapshot(),
    design: designForFamily('OSSE'), fetcher: async url => String(url).endsWith('/write-export') ? written.promise
      : new Response('geometry', { headers: { 'X-WG-Operation-Stages': JSON.stringify({ stages: ['resolve', 'mesh', 'check', 'write'], total: 4 }) } }),
  }, ['step', 'stl']);
  await vi.waitFor(() => expect(activityStore.getSnapshot()[0].progress).toEqual({ done: 2, total: 3 }));
  written.resolve(new Response(JSON.stringify({ directory: 'out', files: ['horn.step', 'horn.stl'] })));
  await pending;
  unsubscribe();
  expect(counts).toEqual([0, 1, 2, 3].map(done => ({ done, total: 3 })));
  expect(activityStore.getSnapshot()).toHaveLength(0);
});

it('counts the server stages of a standalone Fusion CSV pair', async () => {
  activityStore.reset();
  const response = deferred<Response>();
  const counts: number[] = [];
  const unsubscribe = activityStore.subscribe(() => {
    const done = activityStore.getSnapshot()[0]?.progress?.done;
    if (done !== undefined && counts.at(-1) !== done) counts.push(done);
  });
  const pending = runExportFormat('fusion_csv', { jobStem: 'horn', preferences: preferencesStore.getSnapshot(),
    design: designForFamily('OSSE'), saveBlob: vi.fn(), fetcher: () => response.promise,
  });
  expect(activityStore.getSnapshot()[0].progress).toEqual({ done: 0, total: 4 });
  response.resolve(Response.json({ files: ['profiles', 'slices'].map(kind => ({ filename: `horn_${kind}.csv`, text: kind })) }, {
    headers: { 'X-WG-Operation-Stages': JSON.stringify({ stages: ['resolve', 'mesh', 'check', 'write'], total: 4 }) },
  }));
  await pending;
  unsubscribe();
  expect(counts).toEqual([0, 1, 2, 3, 4]);
  expect(activityStore.getSnapshot()).toHaveLength(0);
});

it('a cross-realm abort stops a bundle before the next format', async () => {
  activityStore.reset();
  const reason = { name: 'AbortError' };
  const saveText = vi.fn();
  await expect(runExportBundle({ jobStem: 'horn', preferences: preferencesStore.getSnapshot(),
    design: designForFamily('OSSE'), fetcher: async () => { throw reason; }, saveText,
  }, ['stl', 'mwg_config'])).rejects.toBe(reason);
  expect(saveText).not.toHaveBeenCalled();
});

it.each([false, true])('MSH preparation can exceed 30 seconds and Stop stays available (stop=%s)', async stop => {
  vi.useFakeTimers();
  const timeout = vi.spyOn(AbortSignal, 'timeout').mockImplementation(ms => {
    const deadline = new AbortController();
    setTimeout(() => deadline.abort(new DOMException('Deadline expired', 'TimeoutError')), ms);
    return deadline.signal;
  });
  activityStore.reset();
  const response = deferred<Response>();
  let signal: AbortSignal | null | undefined;
  const saved = vi.fn();
  const fetcher: typeof fetch = async (url, init) => {
    if (String(url).startsWith('/api/operation-progress/')) return Response.json({ stages: ['resolve'], total: 4 });
    signal = init?.signal;
    signal?.addEventListener('abort', () => response.reject(signal?.reason), { once: true });
    return response.promise;
  };
  try {
    const pending = downloadMeshArtifact({ id: 'slow', run_number: 1, label: 'slow', config_summary: {} }, vi.fn(), fetcher, saved);
    const outcome = pending.catch(error => error);
    await vi.advanceTimersByTimeAsync(31_000);
    expect(signal?.aborted).toBe(false);
    expect(activityStore.getSnapshot()[0].label).toContain('Building geometry');
    if (stop) activityStore.getSnapshot()[0].cancel!();
    else response.resolve(new Response('exact msh bytes'));
    const result = await outcome;
    if (stop) {
      expect(result).toMatchObject({ name: 'AbortError' });
      expect(saved).not.toHaveBeenCalled();
    } else {
      expect(Array.from(new Uint8Array(await saved.mock.calls[0][0].arrayBuffer()))).toEqual(Array.from(new TextEncoder().encode('exact msh bytes')));
    }
    expect(activityStore.getSnapshot()).toEqual([]);
  } finally { timeout.mockRestore(); vi.useRealTimers(); }
});
