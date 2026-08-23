import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadRealizedDimensions, CadReturnBundle, CadReturnIngestRecord } from '../api/cadlink';
import { sharedDelayMode } from '../results/crossoverSpec';
import { buildImportedSubmission, importedSubmissionBlocker } from '../jobs/importedSubmission';
import { cadLinkCoordinatorBridge } from '../shell/CadLinkCoordinator';
import { buildParameterPaletteEntries } from '../shell/TopBar';
import { workspaceNavigation } from '../shell/workspaceNavigation';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetCadPreparationStore, useCadPreparationStore } from '../stores/cadPreparation';
import { hydrateDesignDocument } from '../api/designIo';
import { designForFamily, resetDesignStore, serializeDesign, useDesignStore } from '../stores/design';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { FusionParameterDrift, ParamPanel, RealizedDimensionsSection, domainName, parameterRevealRequest, requestParameterReveal, resolveOuterBodyMode, symmetrySummary } from './ParamPanel';

/**
 * The panel reads capabilities and symmetry through React Query, exactly as
 * `ReactPanelRenderer` mounts it behind `AppQueryProvider`. A per-test client
 * keeps one test's cached answers out of the next one's, and no retries keep a
 * failed jsdom fetch from rescheduling after the test has torn the root down.
 */
let queryClient: QueryClient;

const cadRecord = {
  ingest_id: 'wgi_mode_test', manifest_sha256: 'sha256:manifest', artifact_sha256: 'sha256:artifact', report_sha256: 'sha256:report',
  findings: [], evidence: { fem_air_volumes: [{ required: true }] },
  symmetry: { cut_planes: ['x0'], planes: {} },
  polar_grid_derivation: { axes: { vertical: { minimum_deg: -180, maximum_deg: 180, symmetry_accepted: false } } },
  role_findings: [{ kind: 'source-area-drift', source_id: 'source-mf' }],
} as unknown as CadReturnIngestRecord;

const cadBundle = {
  name: 'speaker.wgreturn', bundlePath: 'wgreturn/speaker.wgreturn', modifiedAt: '2026-08-13T12:00:00Z', readable: true,
  documentName: 'Speaker', requestId: null, sourceCount: 2, instanceCount: 1,
  sources: [
    { id: 'source-hf', role: 'HF', required: true, suggestedResolutionMm: 2, defaultDriveChannelId: 'drive-hf' },
    { id: 'source-mf', role: 'MF', required: false, suggestedResolutionMm: 4, defaultDriveChannelId: 'drive-mf' },
  ],
} satisfies CadReturnBundle;

function setCadReady(): void {
  useCadReturnStore.setState({
    selectedBundle: cadBundle,
    ingestRecord: cadRecord,
    needsIngest: false,
    sourceSizesMm: { 'source-hf': 2, 'source-mf': 4 },
    rigidSizeMm: 5,
    transitionMm: 4,
    driveChannels: [
      { id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal' },
      { id: 'drive-mf', source_ids: ['source-mf'], motion: 'normal' },
    ],
    channelDrivers: { 'drive-hf': { enabled: true, fields: {} } },
    exteriorOnly: true,
    // Left unset on purpose: a multi-driver return combines by default.
    combineEnabled: null,
  });
}

function withQueryClient(children: ReactNode) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

describe('outer-body precedence', () => {
  it('matches all four server resolution branches', () => {
    const design = designForFamily('OSSE');
    // A fresh design carries ATH's 5 mm default wall (config_parser.py:296,
    // verified against ath.exe), so it starts freestanding, not bare.
    expect(design.mesh.wall_thickness).toBe(5);
    expect(resolveOuterBodyMode(design)).toBe('freestanding');
    design.mesh.wall_thickness = 0;
    expect(resolveOuterBodyMode(design)).toBe('bare');
    design.mesh.wall_thickness = 5;
    expect(resolveOuterBodyMode(design)).toBe('freestanding');
    design.enclosure.depth = 280;
    expect(resolveOuterBodyMode(design)).toBe('enclosure');
    design.simulation.sim_type = 'infinite-baffle';
    expect(resolveOuterBodyMode(design)).toBe('infinite-baffle');
  });

  // The server keeps the field nullable for lossless CFG round-trips, so a
  // design opened from a .cfg with no Mesh.WallThickness arrives as null on the
  // wire. Unset is not zero: it is ATH's 5 mm, and both sides must say so or a
  // file that was never bare would open as "Bare shell".
  // Counterpart: server/tests/test_outer_body_mode.py.
  it('reads an omitted wall thickness as the 5 mm default, not as a bare shell', () => {
    const hydrated = hydrateDesignDocument({
      ...serializeDesign(designForFamily('OSSE')),
      mesh: { ...serializeDesign(designForFamily('OSSE')).mesh as object, wall_thickness: null },
    });
    expect(hydrated._absent).toContain('mesh.wall_thickness');
    expect(hydrated.mesh.wall_thickness).toBe(5);
    expect(resolveOuterBodyMode(hydrated)).toBe('freestanding');
  });
});

describe('ParamPanel inventory UX', () => {
  let host: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    localStorage.clear();
    resetDesignStore();
    resetCadReturnStore();
    resetCadPreparationStore();
    resetSolveOptionsStore();
    workspaceModeStore.setMode('parametric');
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    act(() => root.render(withQueryClient(<ParamPanel tab="geometry" />)));
  });
  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    queryClient.clear();
    workspaceModeStore.setMode('parametric');
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('does not apply a FREEFORM conversion after the user cancels it', async () => {
    const originalFormula = useDesignStore.getState().design.formula;
    let resolveConversion!: (response: Response) => void;
    const conversion = new Promise<Response>((resolve) => { resolveConversion = resolve; });
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      if (String(input).startsWith('/api/design/convert')) return conversion;
      return Promise.resolve(new Response('not found', { status: 404 }));
    }));
    const family = host.querySelector<HTMLSelectElement>('#family')!;
    act(() => {
      family.value = 'FREEFORM';
      family.dispatchEvent(new Event('change', { bubbles: true }));
    });
    const buttons = () => [...host.querySelectorAll<HTMLButtonElement>('[aria-label="Switch to FREEFORM"] button')];
    act(() => buttons().find((button) => button.textContent === 'Convert current design')!.click());
    expect(host.textContent).toContain('Converting…');
    act(() => buttons().find((button) => button.textContent === 'Cancel')!.click());

    await act(async () => {
      resolveConversion(new Response(JSON.stringify({ design: designForFamily('FREEFORM') }), { status: 200 }));
      await conversion;
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(useDesignStore.getState().design.formula).toBe(originalFormula);
    expect(host.querySelector('[aria-label="Switch to FREEFORM"]')).toBeNull();
  });

  it('filters across labels and ATH/v1 keys, including a mode-hidden field', () => {
    const input = host.querySelector<HTMLInputElement>('#parameter-filter-geometry')!;
    const setInputValue = (value: string) => Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, value);
    act(() => {
      setInputValue('zMapPoints');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    const entries = host.querySelectorAll('[data-parameter-id]');
    expect(entries).toHaveLength(1);
    expect(entries[0].getAttribute('data-parameter-id')).toBe('mesh.z_map_points');
    expect(host.textContent).toContain('normally hidden by the active mode');
  });

  it('keeps each dock panel filter scoped to its own parameter category', () => {
    const input = host.querySelector<HTMLInputElement>('#parameter-filter-geometry')!;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, 'mesh');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(host.querySelector('[data-parameter-id="mesh.angular_segments"]')).not.toBeNull();
    expect(host.querySelector('[data-parameter-id="mesh.throat_resolution"]')).toBeNull();
  });

  it('uses the dock tab as its only category switcher', () => {
    expect(host.querySelector('[role="tablist"]')).toBeNull();
    expect(localStorage.getItem('wg-param-active-tab')).toBeNull();
    expect(host.querySelector('[data-param-tab="geometry"]')).not.toBeNull();
    expect(host.textContent).not.toContain('complete design inventory');
  });

  it('preserves the complete parametric section set', () => {
    act(() => useDesignStore.getState().setFamily('OSSE'));
    const titles = () => [...host.querySelectorAll<HTMLElement>('[data-section]')].map((section) => section.dataset.section);
    expect(titles()).toEqual(['Model Type', 'Profile Dimensions', 'Throat Extension', 'Morph Target', 'Wall & Enclosure', 'Guiding Curve', 'Surface sampling']);
    expect(host.querySelector('[data-section="Realized dimensions"]')).toBeNull();
    act(() => root.render(withQueryClient(<ParamPanel tab="simulation" />)));
    expect(titles()).toEqual(['Frequency Sweep', 'Directivity Map', 'Source Definition', 'Solve options', 'Solve & export mesh', 'Output & Passthrough']);
    expect(host.querySelector('#solve-engine')).not.toBeNull();
    expect(host.querySelector('#solve-symmetry')).not.toBeNull();
  });

  it('keeps Solve domain authoritative and demotes stored ATH quadrants to a read-only passthrough', () => {
    act(() => root.render(withQueryClient(<ParamPanel tab="simulation" />)));
    const sections = [...host.querySelectorAll<HTMLElement>('[data-section]')];
    const solveMesh = sections.find((section) => section.dataset.section === 'Solve & export mesh')!;
    const passthrough = sections.find((section) => section.dataset.section === 'Output & Passthrough')!;
    expect(sections.map((section) => section.dataset.section)).toEqual([
      'Frequency Sweep', 'Directivity Map', 'Source Definition', 'Solve options', 'Solve & export mesh', 'Output & Passthrough',
    ]);
    const stored = passthrough.querySelector<HTMLElement>('[data-parameter-id="mesh.quadrants"]')!;

    expect(solveMesh.querySelector('#solve-symmetry')).not.toBeNull();
    expect(solveMesh.querySelector('[data-parameter-id="mesh.quadrants"]')).toBeNull();
    expect(host.querySelector('.quadrants')).toBeNull();
    expect([...host.querySelectorAll('button')].some((button) => /^Q[1-4]$/.test(button.textContent ?? ''))).toBe(false);
    expect(stored.textContent).toContain('Stored ATH Mesh.Quadrants');
    expect(stored.textContent).toContain('WG overwrites it on solve and ignores it on export.');
    expect(stored.querySelector('input, select, button, textarea')).toBeNull();

    const before = structuredClone(useDesignStore.getState().design.quadrants);
    const storedValue = useDesignStore.getState().design.mesh.quadrants;
    const select = solveMesh.querySelector<HTMLSelectElement>('#solve-symmetry')!;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set?.call(select, 'quarter');
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(useSolveOptionsStore.getState().options().symmetry).toBe('quarter');
    expect(useDesignStore.getState().design.quadrants).toEqual(before);
    expect(useDesignStore.getState().design.mesh.quadrants).toBe(storedValue);
  });

  it('finds and reveals the semantic Solve domain control through rail and palette search', async () => {
    act(() => root.render(withQueryClient(<ParamPanel tab="simulation" />)));
    const filter = host.querySelector<HTMLInputElement>('#parameter-filter-simulation')!;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(filter, 'Solve domain');
      filter.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(host.querySelector('#solve-symmetry')).not.toBeNull();

    const design = useDesignStore.getState().design;
    const entry = buildParameterPaletteEntries(design.formula, { mode: 'parametric', design })
      .find((candidate) => candidate.id === 'parametric-control-solve-domain')!;
    await act(async () => {
      entry.run();
      await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    });

    expect(host.querySelector<HTMLInputElement>('#parameter-filter-simulation')?.value).toBe('Solve domain');
    expect(document.activeElement).toBe(host.querySelector('#solve-symmetry'));
  });

  it('renders only the CAD workspace section set and trims forced solve options', () => {
    act(() => {
      useDesignStore.getState().setFamily('OSSE');
      setCadReady();
      workspaceModeStore.setMode('cad');
    });
    const titles = () => [...host.querySelectorAll<HTMLElement>('[data-section]')].map((section) => section.dataset.section);
    expect(titles()).toEqual(['Linked design', 'Profile Dimensions', 'Throat Extension', 'Morph Target', 'Wall & Enclosure', 'Guiding Curve', 'Realized dimensions']);
    expect(host.querySelector('[data-section="Model Type"]')).toBeNull();
    expect(host.textContent).not.toContain('Surface sampling');

    act(() => root.render(withQueryClient(<ParamPanel tab="simulation" />)));
    expect(titles()).toEqual(['Frequency Sweep', 'Directivity Map', 'Drive channels & drivers', 'Passive cardioid', 'Crossover', 'Solve options', 'Mesh detail']);
    for (const hidden of ['Source Definition', 'Solve & export mesh', 'Output & Passthrough']) expect(host.textContent).not.toContain(hidden);
    expect(host.querySelector('#solve-engine')).toBeNull();
    expect(host.querySelector('#solve-symmetry')).toBeNull();
    expect(host.textContent).toContain('Metal · full 3-D · free space');
    expect(host.textContent).toContain('Ingested cut planesx0');
    expect(host.textContent).toContain('Effective grid −180° … 180°');
  });

  it('drops the waveguide definition for geometry that was authored in CAD', () => {
    act(() => {
      useDesignStore.getState().setFamily('OSSE');
      setCadReady();
      // How a Fusion-authored return resolves: a project of its own, with no
      // WG design behind it. There is no horn of WG's here to describe.
      useCadReturnStore.setState({
        ingestRecord: {
          ...cadRecord,
          project: { lineage_id: 'wgl_cad_authored', design_id: null },
        } as unknown as CadReturnIngestRecord,
      });
      workspaceModeStore.setMode('cad');
    });
    const titles = () => [...host.querySelectorAll<HTMLElement>('[data-section]')].map((section) => section.dataset.section);
    expect(titles()).toEqual(['Linked design', 'Realized dimensions']);
    for (const gone of ['Profile Dimensions', 'Throat Extension', 'Morph Target', 'Wall & Enclosure', 'Guiding Curve']) {
      expect(host.querySelector(`[data-section="${gone}"]`)).toBeNull();
    }
  });

  it('renders manifest interface roles as read-only per-instance facts and omits informational roles', () => {
    const snapshot: CadRealizedDimensions = {
      state: 'current', instanceId: 'instance-a', exportId: 'wge_4',
      parameters: [
        { instanceId: 'instance-a', name: 'wg_tritonia_v_throat_dia', value: 25.4, unit: 'mm', role: 'interface' },
        { instanceId: 'instance-a', name: 'wg_tritonia_v_mouth_w', value: 348.75, unit: 'mm', role: 'interface' },
        { instanceId: 'instance-a', name: 'wg_tritonia_v_mouth_h', value: 584.3, unit: 'mm', role: 'interface' },
        { instanceId: 'instance-a', name: 'wg_tritonia_v_depth', value: 284.6, unit: 'mm', role: 'interface' },
        { instanceId: 'instance-a', name: 'wg_tritonia_v_wall_t', value: 5, unit: 'mm', role: 'interface' },
        { instanceId: 'instance-a', name: 'wg_tritonia_v_vertical_offset', value: -12, unit: 'mm', role: 'interface' },
        { instanceId: 'instance-a', name: 'wg_tritonia_v_enc_w', value: 344, unit: 'mm', role: 'interface' },
        { instanceId: 'instance-a', name: 'wg_tritonia_v_enc_h', value: 579, unit: 'mm', role: 'interface' },
        { instanceId: 'instance-a', name: 'wg_tritonia_v_enc_depth', value: 280, unit: 'mm', role: 'interface' },
        { instanceId: 'instance-a', name: 'wg_tritonia_v_future_api_value', value: 12, unit: 'mm', role: 'interface' },
        { instanceId: 'instance-a', name: 'wg_tritonia_v_coverage_h', value: 90, unit: null, role: 'informational' },
      ],
    };

    act(() => root.render(<RealizedDimensionsSection snapshot={snapshot}/>));

    const rows = host.querySelectorAll<HTMLElement>('[data-role="interface"]');
    expect(rows).toHaveLength(10);
    expect([...rows].every((row) => row.dataset.instanceId === 'instance-a')).toBe(true);
    expect(host.textContent).toContain('Realized throat diameter');
    expect(host.textContent).toContain('Mouth width');
    expect(host.textContent).toContain('Mouth height');
    expect(host.textContent).toContain('Realized depth');
    expect(host.textContent).toContain('Wall thickness');
    expect(host.textContent).toContain('Vertical offset');
    expect(host.textContent).toContain('Enclosure width');
    expect(host.textContent).toContain('Enclosure height');
    expect(host.textContent).toContain('Enclosure depth');
    expect(host.textContent).toContain('wg_tritonia_v_future_api_value');
    expect(host.textContent).not.toContain('coverage_h');
    expect(host.textContent).toContain('the values WG published to CAD and the cabinet references');
    expect(host.querySelector('input')).toBeNull();
  });

  it('marks only realized fields named by the Fusion drift contract', () => {
    const snapshot: CadRealizedDimensions = {
      state: 'current', instanceId: 'instance-a', exportId: 'wge_4',
      parameters: [
        { instanceId: 'instance-a', name: 'wg_tritonia_v_depth', value: 190, unit: 'mm', role: 'interface' },
        { instanceId: 'instance-a', name: 'wg_tritonia_v_enc_depth', value: 280, unit: 'mm', role: 'interface' },
      ],
    };

    act(() => root.render(<RealizedDimensionsSection
      snapshot={snapshot}
      driftedParameters={['wg_tritonia_v_depth']}
    />));

    const edited = host.querySelectorAll<HTMLElement>('[data-locally-edited="true"]');
    expect(edited).toHaveLength(1);
    expect(edited[0].textContent).toContain('Realized depth');
    expect(edited[0].textContent).toContain('Edited in Fusion');
    expect(edited[0].textContent).not.toContain('Enclosure depth');
  });

  it('names every edited Fusion parameter even when no realized row can match it', () => {
    act(() => root.render(<FusionParameterDrift
      parameterDriftCount={2}
      driftedParameters={['wg_tritonia_v_depth', 'wg_tritonia_v_mouth_overshoot']}
    />));

    expect(host.textContent).toContain('2 managed parameters have local edits');
    expect([...host.querySelectorAll('li')].map((item) => item.textContent)).toEqual([
      'wg_tritonia_v_depth',
      'wg_tritonia_v_mouth_overshoot',
    ]);

    act(() => root.render(<FusionParameterDrift parameterDriftCount={1}/>));
    expect(host.textContent).toContain('1 managed parameter has local edits');
    expect(host.querySelector('ul')).toBeNull();
  });

  it('tells the truth for no link, pre-capture, missing-registry, and stale-export states', () => {
    const empty = (state: CadRealizedDimensions['state']): CadRealizedDimensions => ({
      state, instanceId: state === 'no_link' ? null : 'instance-a', exportId: state === 'no_link' ? null : 'wge_4', parameters: [],
    });
    const absent: Array<[CadRealizedDimensions['state'], string]> = [
      ['no_link', 'No CAD link yet'],
      ['not_captured', 'This CAD link predates parameter capture'],
      ['export_missing', 'not available in this WG registry'],
    ];
    for (const [state, copy] of absent) {
      act(() => root.render(<RealizedDimensionsSection snapshot={empty(state)}/>));
      expect(host.textContent).toContain(copy);
      expect(host.querySelector('.realized-dimension-list')).toBeNull();
    }

    act(() => root.render(<RealizedDimensionsSection snapshot={{
      state: 'stale', instanceId: 'instance-a', exportId: 'wge_4',
      parameters: [{ instanceId: 'instance-a', name: 'wg_tritonia_v_depth', value: 190, unit: 'mm', role: 'interface' }],
    }}/>));
    expect(host.textContent).toContain('From an older CAD export');
    expect(host.textContent).toContain('historical, not current dimensions');
    expect(host.querySelector('.realized-dimension-list.stale')).not.toBeNull();
    expect(host.textContent).toContain('190mm');
  });

  it('renders every moved CAD control in the Simulation rail and submits its visible crossover state', () => {
    const ingest = vi.spyOn(cadLinkCoordinatorBridge.getSnapshot(), 'ingest').mockResolvedValue(undefined);
    act(() => {
      setCadReady();
      workspaceModeStore.setMode('cad');
      root.render(withQueryClient(<ParamPanel tab="simulation" />));
    });

    for (const id of ['cad-force-full-domain', 'cad-combine', 'cad-combine-level', 'cad-combine-align', 'cad-exterior-only', 'skip-source-mf', 'drift-source-mf']) {
      expect(host.querySelector(`#${id}`), id).not.toBeNull();
    }
    expect(host.querySelector('[aria-label="Drive channel for source-hf"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="Motion for drive-hf"]')).not.toBeNull();
    expect(host.textContent).toContain('Cabinet & waveguide');
    expect(host.textContent).toContain('Size transition');
    expect(host.textContent).toContain('HF source');
    expect(host.textContent).toContain('MF source');
    expect(host.textContent).toContain('Drive voltage');

    const align = host.querySelector<HTMLInputElement>('#cad-combine-align')!;
    expect(align.checked).toBe(true);
    act(() => align.click());
    expect(sharedDelayMode(useCadReturnStore.getState().combineSpec!)).toBe('manual');
    expect(buildImportedSubmission(useCadReturnStore.getState()).geometry.combine?.channels?.['drive-mf'].delay)
      .toEqual({ mode: 'manual', ms: 0 });
    expect(importedSubmissionBlocker()).toBeNull();

    const forceFull = host.querySelector<HTMLInputElement>('#cad-force-full-domain')!;
    act(() => forceFull.click());
    expect(useCadPreparationStore.getState().symmetryMode).toBe('full');
    expect(useCadReturnStore.getState().ingestStaleReason).toContain('symmetry preparation mode changed');

    const rebuild = [...host.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === 'Rebuild mesh')!;
    act(() => rebuild.click());
    expect(ingest).toHaveBeenCalledOnce();
  });

  it('combines by default, labelled by band, and says which default each field would use', () => {
    act(() => {
      setCadReady();
      workspaceModeStore.setMode('cad');
      root.render(withQueryClient(<ParamPanel tab="simulation" />));
    });

    const combine = host.querySelector<HTMLInputElement>('#cad-combine')!;
    expect(useCadReturnStore.getState().combineEnabled).toBeNull();
    expect(combine.checked).toBe(true);
    expect(host.textContent).toContain('MF → HF');
    expect(host.textContent).toContain('1000 Hz default.');
    expect(host.textContent).not.toContain('Untouched crossover defaults');
    expect(buildImportedSubmission(useCadReturnStore.getState()).geometry.combine?.channels?.['drive-mf'].lp)
      .toEqual({ family: 'lr', order: 4, fc_hz: 1_000 });

    // A default the sweep cannot carry says so rather than blocking the solve:
    // the server refuses a crossover outside the solved band.
    act(() => useCadReturnStore.setState({
      selectedBundle: {
        ...cadBundle,
        sources: cadBundle.sources.map((source) => (
          source.id === 'source-mf' ? { ...source, role: 'LF' } : { ...source, role: 'MF' }
        )),
      },
    }));
    expect(host.textContent).toContain('LF → MF');
    expect(host.textContent).toContain('100 Hz default is outside the sweep; using 2000 Hz.');

    act(() => combine.click());
    expect(useCadReturnStore.getState().combineEnabled).toBe(false);
    expect(host.querySelector('#cad-combine-align')).toBeNull();
    expect(buildImportedSubmission(useCadReturnStore.getState()).geometry).not.toHaveProperty('combine');
  });

  it('edits the passive-cardioid campaign in the rail, in cm² over an m² wire', () => {
    act(() => {
      setCadReady();
      workspaceModeStore.setMode('cad');
      root.render(withQueryClient(<ParamPanel tab="simulation" />));
    });
    const section = () => host.querySelector<HTMLElement>('[data-section="Passive cardioid"]')!;
    const area = (label: string) => section().querySelector<HTMLInputElement>(`[aria-label="${label} in cm²"]`)!;
    const type = (input: HTMLInputElement, value: string) => act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, value);
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });

    // Nothing but the switch until the campaign is asked for.
    expect(section().querySelector('[aria-label="Rear volume in L"]')).toBeNull();
    act(() => host.querySelector<HTMLInputElement>('#cad-passive-cardioid')!.click());
    expect(section().textContent).toContain('roughly 20 seconds');
    // Enabled but empty is a refusal, never a quiet pre-campaign submission.
    expect(importedSubmissionBlocker()).toContain('Rear volume');

    type(section().querySelector<HTMLInputElement>('[aria-label="Rear volume in L"]')!, '6');
    type(section().querySelector<HTMLInputElement>('[aria-label="Port length in mm"]')!, '25');
    type(area('Physical port area'), '500');
    type(area('BEM port area'), '94.71859930646809');
    type(section().querySelector<HTMLInputElement>('[aria-label="Foam resistance in Pa·s/m³"]')!, '10000');
    expect(importedSubmissionBlocker()).toBeNull();
    expect(buildImportedSubmission(useCadReturnStore.getState()).geometry).toMatchObject({
      passive_cardioid_rear_volume_l: 6,
      passive_cardioid_port_length_mm: 25,
      model_port_area_m2: 0.05,
      port_area_source: 'user',
      passive_cardioid_foam_resistance_pa_s_m3: 10_000,
      passive_cardioid_invert_port: true,
      passive_cardioid_coupled: false,
    });
    expect(useCadReturnStore.getState().passiveCardioid.bemPortAreaM2).toBeCloseTo(0.009471859930646809, 15);
    // Typed cm² must survive the m² round trip on screen: 0.00037 × 1e4 is
    // 3.6999999999999997, and the measured aperture must keep every digit.
    type(area('Physical port area'), '3.7');
    expect(area('Physical port area').value).toBe('3.7');
    expect(area('BEM port area').value).toBe('94.71859930646809');
    type(area('Physical port area'), '500');

    // Choosing the BEM provenance drives the physical area rather than letting
    // the two drift into the server's rel_tol=1e-12 refusal.
    const provenance = host.querySelector<HTMLSelectElement>('#cad-cardioid-port-area-source')!;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set?.call(provenance, 'bem_aperture');
      provenance.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(area('Physical port area').disabled).toBe(true);
    const geometry = buildImportedSubmission(useCadReturnStore.getState()).geometry;
    expect(geometry.model_port_area_m2).toBe(geometry.bem_port_area_m2);
  });

  it('keeps the reserved coupled channel id out of the assignable drive channels', () => {
    act(() => {
      useCadReturnStore.setState({
        selectedBundle: {
          ...cadBundle,
          sources: [
            { id: 'source-mf', role: 'MF', required: true, suggestedResolutionMm: 4, defaultDriveChannelId: 'passive_cardioid' },
            { id: 'source-port', role: 'MF', required: true, suggestedResolutionMm: 4, defaultDriveChannelId: 'drive-port' },
          ],
        },
        ingestRecord: cadRecord,
        needsIngest: false,
        driveChannels: [
          { id: 'passive_cardioid', source_ids: ['source-mf'], motion: 'normal' },
          { id: 'drive-port', source_ids: ['source-port'], motion: 'normal' },
        ],
      });
      workspaceModeStore.setMode('cad');
      root.render(withQueryClient(<ParamPanel tab="simulation" />));
    });
    const options = () => [...host.querySelectorAll<HTMLOptionElement>('[aria-label="Drive channel for source-mf"] option')]
      .map((option) => option.value);
    expect(options()).toContain('passive_cardioid');

    act(() => useCadReturnStore.getState().setPassiveCardioid({
      enabled: true, coupled: true, rearVolumeL: 6, portLengthMm: 25,
      modelPortAreaM2: 0.05, bemPortAreaM2: 0.0094, foamResistancePaSM3: 10_000,
    }));
    expect(options()).toEqual(['drive-port']);
    // The channel that already claims the id predates the choice, so the
    // prevention is backed by a refusal the user can act on.
    expect(host.querySelector('[data-section="Passive cardioid"] [role="alert"]')?.textContent)
      .toContain('Reassign that source');
  });

  it('shows the CAD simulation empty state without hiding formula editing on Geometry', () => {
    act(() => workspaceModeStore.setMode('cad'));
    expect(host.querySelector('[data-section="Linked design"]')).not.toBeNull();
    expect(host.querySelector('[data-section="Profile Dimensions"]')).not.toBeNull();
    act(() => root.render(withQueryClient(<ParamPanel tab="simulation" />)));
    expect(host.textContent).toContain('Prepare CAD geometry to unlock these inputs');
    expect(host.textContent).toContain('Driver T/S, crossover, sweep');
    expect(host.textContent).toContain('Open CAD Link setup');
    expect(host.querySelector('[data-section]')).toBeNull();
  });

  it('writes the CAD sweep without changing design.simulation and surfaces an invalid range', () => {
    act(() => {
      setCadReady();
      workspaceModeStore.setMode('cad');
      root.render(withQueryClient(<ParamPanel tab="simulation" />));
    });
    const designStart = useDesignStore.getState().design.simulation.f1;
    const sweepSection = host.querySelector<HTMLElement>('[data-section="Frequency Sweep"]')!;
    const row = [...sweepSection.querySelectorAll<HTMLElement>('.field-row')]
      .find((item) => item.querySelector('.field-name')?.textContent === 'Sweep start')!;
    const input = row.querySelector<HTMLInputElement>('input')!;
    act(() => {
      input.focus();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, '750');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    act(() => input.blur());
    expect(useCadReturnStore.getState().frequencyStartHz).toBe(750);
    expect(useDesignStore.getState().design.simulation.f1).toBe(designStart);

    act(() => useCadReturnStore.getState().setSweep({ frequencyEndHz: 500 }));
    expect(sweepSection.textContent).toContain('Enter a valid explicit frequency sweep.');
  });

  it('filters the CAD rail through the same control descriptors as the palette', () => {
    act(() => {
      setCadReady();
      workspaceModeStore.setMode('cad');
      root.render(withQueryClient(<ParamPanel tab="simulation" />));
    });
    const matchingSections = (query: string) => {
      const input = host.querySelector<HTMLInputElement>('#parameter-filter-simulation')!;
      act(() => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, query);
        input.dispatchEvent(new Event('input', { bubbles: true }));
      });
      return [...host.querySelectorAll<HTMLElement>('[data-section]')].map((section) => section.dataset.section);
    };

    expect(matchingSections('frequencyStartHz')).toEqual(['Frequency Sweep']);
    expect(matchingSections('sd_cm2')).toEqual(['Drive channels & drivers']);
    expect(matchingSections('LR4')).toEqual(['Crossover']);
    expect(matchingSections('surface sizing')).toEqual(['Mesh detail']);
  });

  it('omits field-count labels while retaining informative summaries', () => {
    const summaries = [...host.querySelectorAll<HTMLElement>('.section-summary')].map((element) => element.textContent);
    expect(summaries).toContain(useDesignStore.getState().design.formula);
    expect(summaries).not.toContain(expect.stringMatching(/^\d+ (?:fields?|controls|options)$/));
  });

  it('reveals and focuses a parameter routed from the command palette', async () => {
    act(() => root.render(withQueryClient(<ParamPanel tab="simulation" />)));
    await act(async () => {
      requestParameterReveal({ id: 'simulation.f1', tab: 'simulation', query: 'Sweep start' });
      await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    });
    const entry = host.querySelector<HTMLElement>('[data-parameter-id="simulation.f1"]')!;
    expect(entry).not.toBeNull();
    expect(document.activeElement).toBe(entry.querySelector('input'));
    expect(host.querySelector<HTMLInputElement>('#parameter-filter-simulation')?.value).toBe('Sweep start');
  });

  it('routes the moved ATH quadrants palette entry to its read-only passthrough row', async () => {
    const activate = vi.spyOn(workspaceNavigation, 'activate').mockReturnValue(true);
    act(() => root.render(withQueryClient(<ParamPanel tab="simulation" />)));
    const design = useDesignStore.getState().design;
    const storedQuadrants = buildParameterPaletteEntries(design.formula, {
      mode: 'parametric', design,
    }).find((entry) => entry.id === 'parameter-mesh.quadrants')!;

    expect(storedQuadrants.label).toBe('Stored ATH Mesh.Quadrants');
    await act(async () => {
      storedQuadrants.run();
      await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    });

    expect(activate).toHaveBeenCalledWith('simulation');
    const target = host.querySelector<HTMLElement>('[data-parameter-id="mesh.quadrants"]')!;
    expect(target.closest('[data-section]')?.getAttribute('data-section')).toBe('Output & Passthrough');
    expect(document.activeElement).toBe(target);
    expect(host.querySelector<HTMLInputElement>('#parameter-filter-simulation')?.value).toBe('Stored ATH Mesh.Quadrants');
  });

  it('switches to CAD mode and claims a non-registry palette reveal', async () => {
    const activate = vi.spyOn(workspaceNavigation, 'activate').mockReturnValue(true);
    act(() => {
      setCadReady();
      root.render(withQueryClient(<ParamPanel tab="simulation" />));
    });
    const design = useDesignStore.getState().design;
    const sweepStart = buildParameterPaletteEntries(design.formula, {
      mode: 'cad', design, cadReturnReady: true,
    }).find((entry) => entry.id === 'cad-control-cad.frequency.start')!;

    await act(async () => {
      sweepStart.run();
      await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    });

    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(activate).toHaveBeenCalledWith('simulation');
    const target = host.querySelector<HTMLElement>('[data-control-reveal-id="cad.frequency.start"]')!;
    expect(target).not.toBeNull();
    expect(document.activeElement).toBe(target.querySelector('input'));
    expect(host.querySelector<HTMLInputElement>('#parameter-filter-simulation')?.value).toBe('Sweep start');
  });

  it('persists section collapse state', () => {
    act(() => root.render(withQueryClient(<ParamPanel tab="simulation" />)));
    const source = host.querySelector<HTMLElement>('[data-section="Source Definition"]')!;
    act(() => source.querySelector<HTMLButtonElement>('.section-head')!.click());
    // One map rather than a key per section, so this travels with every other
    // durable setting instead of living only in this browser.
    expect(JSON.parse(localStorage.getItem('wg-param-sections')!)).toMatchObject({
      'Source Definition': false,
    });
    expect(source.classList.contains('closed')).toBe(true);
  });

  it('changes outer-body modes in one mutation and restores the last wall thickness', () => {
    const select = host.querySelector<HTMLSelectElement>('#outer-body-mode')!;
    const choose = (value: string) => act(() => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set?.call(select, value);
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    const before = useDesignStore.getState().designRevision;
    choose('freestanding');
    expect(useDesignStore.getState().designRevision).toBe(before + 1);
    expect(useDesignStore.getState().design.mesh.wall_thickness).toBe(5);
    expect(useDesignStore.getState().design.enclosure.depth).toBe(0);

    act(() => useDesignStore.getState().updateValue('mesh.wall_thickness', 8));
    choose('enclosure');
    expect(useDesignStore.getState().design.mesh.wall_thickness).toBe(0);
    expect(useDesignStore.getState().design.enclosure.depth).toBe(280);
    choose('freestanding');
    expect(useDesignStore.getState().design.mesh.wall_thickness).toBe(8);
    expect(useDesignStore.getState().design.enclosure.depth).toBe(0);
  });

  it('plainly reports the infinite-baffle override', () => {
    act(() => useDesignStore.getState().updateValue('simulation.sim_type', 'infinite-baffle'));
    expect(host.textContent).toContain('Infinite baffle simulation overrides the outer body.');
    expect(host.textContent).toContain('Resolved mode');
  });

  it('rejects prospective inverted frequency bounds before committing', () => {
    act(() => root.render(withQueryClient(<ParamPanel tab="simulation" />)));
    const entry = host.querySelector<HTMLElement>('[data-parameter-id="simulation.f1"]')!;
    const input = entry.querySelector<HTMLInputElement>('input')!;
    act(() => {
      input.focus();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, '18000');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(input.getAttribute('aria-invalid')).toBe('true');
    act(() => input.blur());
    expect(useDesignStore.getState().design.simulation.f1).toBe(400);
  });

  it('enforces the legacy Source.Velocity 1/2 domain', () => {
    act(() => root.render(withQueryClient(<ParamPanel tab="simulation" />)));
    act(() => useDesignStore.getState().setSourceConvention('legacy'));
    const entry = host.querySelector<HTMLElement>('[data-parameter-id="source.velocity"]')!;
    const input = entry.querySelector<HTMLInputElement>('input')!;
    act(() => {
      input.focus();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, '3');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(input.getAttribute('aria-invalid')).toBe('true');
    act(() => input.blur());
    expect(useDesignStore.getState().design.source.velocity).toBe(1);
  });

  it('treats the optional maximum-edge guard as unset and lets a value be cleared', () => {
    act(() => root.render(withQueryClient(<ParamPanel tab="simulation" />)));
    const entry = host.querySelector<HTMLElement>('[data-parameter-id="schema-gap.max_edge"]')!;
    const input = entry.querySelector<HTMLInputElement>('input')!;
    const setInputValue = (value: string) => Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, value);

    expect(input.value).toBe('');
    expect(input.getAttribute('aria-invalid')).toBe('false');
    act(() => {
      input.focus();
      setInputValue('8.5');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    act(() => input.blur());
    expect(useDesignStore.getState().design.mesh.max_edge).toBe(8.5);

    act(() => {
      input.focus();
      setInputValue('');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(input.getAttribute('aria-invalid')).toBe('false');
    act(() => input.blur());
    expect(useDesignStore.getState().design.mesh.max_edge).toBeNull();
  });

  it('renders the solve/directivity contracts and editable FREEFORM tables', () => {
    act(() => root.render(withQueryClient(<ParamPanel tab="simulation" />)));
    expect(host.querySelector('[data-section="Solve options"]')).not.toBeNull();
    expect(host.querySelector('[data-section="Directivity Map"]')).not.toBeNull();
    for (const id of ['solve-engine', 'mesh-validation-mode', 'design-solve-frequency-spacing', 'solve-verbose', 'polar-angle-start', 'polar-angle-end', 'polar-angle-step', 'polar-distance', 'polar-norm-angle', 'polar-diagonal-angle', 'polar-observation-origin', 'polar-spherical-sampling', 'polar-field-plane']) {
      expect(host.querySelector(`#${id}`), id).not.toBeNull();
    }
    act(() => {
      useDesignStore.getState().setFamily('FREEFORM');
      root.render(withQueryClient(<ParamPanel tab="geometry" />));
    });
    expect(host.querySelectorAll('.editable-parameter-table')).toHaveLength(3);
    expect(host.querySelectorAll('.point-paste textarea')).toHaveLength(2);
    expect(host.textContent).not.toContain('tangent scale');
    expect(host.textContent).not.toContain('Spline overshoot');
    expect(host.querySelector('input[aria-label$=" strength"]')).toBeNull();
    expect(host.querySelector<HTMLSelectElement>('select[aria-label="Station 1 shape"]')?.value).toBe('ellipse');
  });

  it('states the λ/6 mesh limit for the sweep this design will actually solve', () => {
    act(() => root.render(withQueryClient(<ParamPanel tab="simulation" />)));
    const hint = () => host.querySelector<HTMLElement>('[data-parameter-id="mesh.mouth_resolution"] .lambda-hint');
    expect(hint()?.textContent).toBe('λ/6 at 16 kHz ≈ 3.57 mm');

    // 3.2 mm resolves the seed design's 16 kHz sweep end; the hint used to flag
    // it amber against a 20 kHz limit this design never asked for.
    act(() => useDesignStore.getState().updateValue('mesh.mouth_resolution', 3.2));
    expect(hint()?.classList.contains('warning')).toBe(false);

    act(() => useDesignStore.getState().updateValue('simulation.f2', 20_000));
    expect(hint()?.textContent).toBe('λ/6 at 20 kHz ≈ 2.86 mm');
    expect(hint()?.classList.contains('warning')).toBe(true);

    // An explicit list replaces the design's range, so the mesh has to answer to
    // the list instead.
    act(() => {
      useSolveOptionsStore.getState().setFrequencyMode('list');
      useSolveOptionsStore.getState().setFrequencyListText('500, 1000, 8000');
    });
    expect(hint()?.textContent).toBe('λ/6 at 8 kHz ≈ 7.15 mm');
    expect(hint()?.classList.contains('warning')).toBe(false);

    act(() => useSolveOptionsStore.getState().setFrequencyListText('8000, 500'));
    expect(hint()).toBeNull();
  });

  it('changes FREEFORM length without moving or dropping normalized anchors', () => {
    act(() => {
      useDesignStore.getState().setFamily('FREEFORM');
      useDesignStore.getState().updateValue('profile_h.points', [
        { t: 0, r: 12.7 }, { t: 70 / 120, r: 60 }, { t: 1, r: 140 },
      ]);
    });
    const before = structuredClone(useDesignStore.getState().design.profile_h!.points);
    const input = host.querySelector<HTMLInputElement>('[data-parameter-id="freeform.length"] input')!;
    act(() => {
      input.focus();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, '60');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    act(() => input.blur());
    expect(useDesignStore.getState().design.length).toBe(60);
    expect(useDesignStore.getState().design.profile_h!.points).toEqual(before);
  });
});

describe('automatic solve domain', () => {
  it('names each ATH quadrant mask the way the solver reduces it', () => {
    expect(domainName(1)).toBe('Quarter domain');
    expect(domainName(12)).toBe('Half domain (XZ)');
    expect(domainName(14)).toBe('Half domain (YZ)');
    expect(domainName(1234)).toBe('Full domain');
  });

  it('reports only the planes that were actually rejected, and says so when none were', () => {
    expect(symmetrySummary({
      quadrants: 1, xz: true, yz: true, reasons: { xz: [], yz: [] },
      tolerance_mm: 0.01, relative_tolerance: 2e-4,
    })).toBe('Both mirror planes hold.');
    expect(symmetrySummary({
      quadrants: 12, xz: true, yz: false,
      reasons: { xz: ['unused'], yz: ['guiding curve is rotated'] },
      tolerance_mm: 0.01, relative_tolerance: 2e-4,
    })).toBe('guiding curve is rotated');
  });
});

describe('parameter reveal requests', () => {
  it('is claimed by its own tab and left alone by the other', () => {
    requestParameterReveal({ id: 'rosse.R', tab: 'geometry', query: 'Mouth radius' });
    expect(parameterRevealRequest.claim('simulation')).toBeNull();
    expect(parameterRevealRequest.claim('geometry')).toMatchObject({ id: 'rosse.R' });
    // Claiming consumes it, so a later mount does not re-apply a stale filter.
    expect(parameterRevealRequest.claim('geometry')).toBeNull();
  });

  it('is readable by a panel that only mounts after the request was made', () => {
    // The palette activates a tab and then routes, so the panel that must act
    // on the request frequently does not exist when it is made.
    requestParameterReveal({ id: 'simulation.f1', tab: 'simulation', query: 'Sweep start' });
    expect(parameterRevealRequest.getSnapshot()).toMatchObject({ tab: 'simulation' });
    let notified = 0;
    const unsubscribe = parameterRevealRequest.subscribe(() => { notified += 1; });
    requestParameterReveal({ id: 'rosse.R', tab: 'geometry', query: 'Mouth radius' });
    expect(notified).toBe(1);
    unsubscribe();
    parameterRevealRequest.claim('geometry');
  });
});
