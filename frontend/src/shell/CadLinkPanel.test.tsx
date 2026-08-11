import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadReturnIngestRecord, CadReturnListing } from '../api/cadlink';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { buildImportedSubmission, CadLinkPanel } from './CadLinkPanel';
import { jobsCoordinatorBridge } from './JobsCoordinator';

const listing: CadReturnListing = {
  items: [{
    name: 'speaker.wgreturn', bundlePath: 'wgreturn/speaker.wgreturn', modifiedAt: '2026-08-11T00:00:00Z', readable: true,
    documentName: 'Speaker', sourceCount: 1, instanceCount: 1,
    sources: [{ id: 'source-hf', role: 'HF', required: true, suggestedResolutionMm: 4, defaultDriveChannelId: 'drive-hf' }],
  }],
};
const record: CadReturnIngestRecord = {
  ingest_id: 'wgi_01J5A8QK3M9T2XVBH0RD7NWE6C', created_at: '', return_id: '', manifest_sha256: `sha256:${'1'.repeat(64)}`, artifact_sha256: `sha256:${'2'.repeat(64)}`, report_sha256: `sha256:${'3'.repeat(64)}`,
  acoustic_domain: 'free-space', scope: { status: 'clean', degraded_skip_count: 0 },
  sources: [{ id: 'source-hf', role: 'HF', required: true, instance_id: null, default_drive_channel_id: 'drive-hf', suggested_resolution_mm: 4 }],
  mesh_sizes: { rigid_size_mm: 4, transition_mm: 4, source_size_mm: { 'source-hf': 4 } }, skipped_source_ids: [],
  freshness: { verdict: 'per-instance', instances: [{ instance_id: 'instance-a', verdict: 'design_changed' }] },
  findings: [{ id: 'finding-a', kind: 'freshness', blocking: true, verdict: 'design_changed' }],
  symmetry: { planes: { x0: { accepted: true }, y0: { accepted: false } }, cut_planes: ['x0'] }, healing: { performed: false, mode: 'none' },
  sizing_estimate: { triangles: 1200 },
  polar_grid_derivation: {
    axes: {
      horizontal: { plane: 'x0', symmetry_accepted: true, minimum_deg: 0, maximum_deg: 180, may_widen_not_narrow: true },
      vertical: { plane: 'y0', symmetry_accepted: false, minimum_deg: -180, maximum_deg: 180, may_widen_not_narrow: true },
      diagonal: { plane: 'x0+y0', symmetry_accepted: false, minimum_deg: -180, maximum_deg: 180, may_widen_not_narrow: true },
    },
    cut_planes: ['x0'],
  },
  tag_map: {},
};

describe('CadLinkPanel', () => {
  let host: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetCadReturnStore(); resetSolveOptionsStore();
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(String(input).endsWith('/returns') ? listing : record), { status: 200, headers: { 'Content-Type': 'application/json' } })));
  });
  afterEach(() => { act(() => root.unmount()); vi.unstubAllGlobals(); host.remove(); });

  const renderAndSelect = async () => {
    await act(async () => { root.render(<CadLinkPanel/>); await Promise.resolve(); await Promise.resolve(); });
    const bundle = host.querySelector<HTMLButtonElement>('.cad-bundle-list button')!;
    act(() => bundle.click());
    return bundle;
  };

  const clickIngest = async () => {
    const ingest = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent === 'Ingest bundle' || button.textContent === 'Re-ingest')!;
    await act(async () => { ingest.click(); await Promise.resolve(); await Promise.resolve(); });
    return ingest;
  };

  it('runs listing → ingest → blocking acknowledgement → solve enabled', async () => {
    await renderAndSelect();
    await clickIngest();
    const solve = [...host.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === 'Solve CAD import')!;
    expect(solve.disabled).toBe(true);
    expect(host.textContent).toContain('Acknowledge 1 blocking finding');
    const acknowledgement = host.querySelector<HTMLInputElement>('.cad-findings input[type="checkbox"]')!;
    act(() => { acknowledgement.click(); });
    expect(solve.disabled).toBe(false);
    expect([...host.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === 'Re-ingest')?.disabled).toBe(false);
  });

  it('builds acknowledgement wires from the current record, filters skipped sizes, and emits range/list sweep shapes', () => {
    useCadReturnStore.getState().selectBundle(listing.items[0]);
    useCadReturnStore.getState().applyIngest(record);
    useCadReturnStore.getState().acknowledge('finding-a', true);
    useCadReturnStore.setState({
      sourceSizesMm: { 'source-hf': 3.25, optional: 9 },
      skippedSourceIds: ['optional'],
    });
    useCadReturnStore.getState().setSweep({ frequencyStartHz: 250, frequencyEndHz: 12_000, frequencyCount: 31 });

    const range = buildImportedSubmission(useCadReturnStore.getState());
    expect(range.geometry.acknowledged_findings).toEqual([`${record.report_sha256}:finding-a`]);
    expect(range.geometry.mesh.source_size_mm).toEqual({ 'source-hf': 3.25 });
    expect(range.options).toMatchObject({ frequency_range: [250, 12_000], num_frequencies: 31 });
    expect(range.options).not.toHaveProperty('frequencies_hz');

    useSolveOptionsStore.getState().setFrequencyMode('list');
    useSolveOptionsStore.getState().setFrequencyListText('300 700 1500');
    const list = buildImportedSubmission(useCadReturnStore.getState());
    expect(list.options.frequencies_hz).toEqual([300, 700, 1_500]);
    expect(list.options).not.toHaveProperty('frequency_range');
    expect(list.options).not.toHaveProperty('num_frequencies');
  });

  it('refuses to build an imported submission without an ingestion record', () => {
    expect(() => buildImportedSubmission(useCadReturnStore.getState())).toThrow('Ingest a CAD return');
  });

  it('widens the polar request to the derivation instead of submitting a narrowing grid', () => {
    useCadReturnStore.getState().selectBundle(listing.items[0]);
    useCadReturnStore.getState().applyIngest(record);
    const submission = buildImportedSubmission(useCadReturnStore.getState());
    const polar = submission.options.polar_config as {
      angle_range: [number, number, number];
      enabled_axes: string[];
    };
    // The record pins vertical and diagonal (rejected mirror planes): the
    // default 0..180/37 grid must widen to a full circle at the same 5° step,
    // with every pinned axis enabled.
    expect(polar.angle_range[0]).toBe(-180);
    expect(polar.angle_range[1]).toBe(180);
    expect(polar.angle_range[2]).toBe(73);
    expect(polar.enabled_axes).toEqual(expect.arrayContaining(['vertical', 'diagonal']));
  });

  it('size change → re-ingest → solve submits the new report acknowledgement wire', async () => {
    const runImported = vi.fn().mockResolvedValue(undefined);
    vi.spyOn(jobsCoordinatorBridge, 'getSnapshot').mockReturnValue({
      ...jobsCoordinatorBridge.getSnapshot(), runImported,
    });
    let ingestCount = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/returns')) return new Response(JSON.stringify(listing), { status: 200 });
      ingestCount += 1;
      const next = {
        ...record,
        ingest_id: `${record.ingest_id.slice(0, -1)}${ingestCount}`,
        report_sha256: `sha256:${String(ingestCount).repeat(64)}`,
        mesh_sizes: {
          ...record.mesh_sizes,
          source_size_mm: { 'source-hf': ingestCount === 1 ? 4 : 2.5 },
        },
      };
      return new Response(JSON.stringify(next), { status: 200 });
    }));
    await renderAndSelect();
    await clickIngest();
    act(() => useCadReturnStore.getState().setSourceSize('source-hf', 2.5));
    await clickIngest();
    act(() => host.querySelector<HTMLInputElement>('.cad-findings input[type="checkbox"]')!.click());
    await act(async () => {
      [...host.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === 'Solve CAD import')!.click();
      await Promise.resolve();
    });

    expect(runImported).toHaveBeenCalledOnce();
    expect(runImported.mock.calls[0][0].geometry).toMatchObject({
      mesh: { source_size_mm: { 'source-hf': 2.5 } },
      acknowledged_findings: [`sha256:${'2'.repeat(64)}:finding-a`],
    });
  });

  it('marks a changed refreshed bundle stale, preserves sizing edits, and disables Solve', async () => {
    let listingCount = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/returns')) {
        listingCount += 1;
        const body = listingCount === 1 ? listing : { items: [{
          ...listing.items[0], modifiedAt: '2026-08-11T01:00:00Z',
          sources: [{ ...listing.items[0].sources[0], suggestedResolutionMm: 2.75 }],
        }] };
        return new Response(JSON.stringify(body), { status: 200 });
      }
      return new Response(JSON.stringify(record), { status: 200 });
    }));
    await renderAndSelect();
    await clickIngest();
    act(() => useCadReturnStore.getState().setSourceSize('source-hf', 2.5));
    const refresh = [...host.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === 'Refresh')!;
    await act(async () => { refresh.click(); await Promise.resolve(); await Promise.resolve(); });

    expect(host.textContent).toContain('source inventory or source sizing suggestions changed');
    expect(useCadReturnStore.getState().sourceSizesMm['source-hf']).toBe(2.5);
    expect([...host.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === 'Solve CAD import')?.disabled).toBe(true);
    expect([...host.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === 'Re-ingest')?.disabled).toBe(false);
  });

  it('renders an unreadable listing row disabled with the server reason', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ items: [{
      ...listing.items[0], readable: false, documentName: null, sourceCount: null, instanceCount: null,
      sources: [], reason: 'suggested resolution must be positive',
    }] }), { status: 200 })));
    await act(async () => { root.render(<CadLinkPanel/>); await Promise.resolve(); await Promise.resolve(); });
    const row = host.querySelector<HTMLButtonElement>('.cad-bundle-list button')!;
    expect(row.disabled).toBe(true);
    expect(row.textContent).toContain('suggested resolution must be positive');
    expect(row.title).toBe('suggested resolution must be positive');
  });

  it.each([
    ['unlinked', { verdict: 'unlinked', instances: [] }, 'Unlinked return'],
    ['unknown', { verdict: 'per-instance', instances: [{ instance_id: 'instance-a', verdict: 'unknown' }] }, 'Freshness could not be established'],
  ])('renders %s freshness copy', async (_name, freshness, copy) => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(
      String(input).endsWith('/returns') ? listing : { ...record, freshness },
    ), { status: 200 })));
    await renderAndSelect();
    await clickIngest();
    expect(host.textContent).toContain(copy);
  });

  it('discovers area-drift overrides from structured refusal data', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/returns')) return new Response(JSON.stringify(listing), { status: 200 });
      return new Response(JSON.stringify({
        detail: { message: 'Role resolution refused.', area_drift_sources: ['source-hf'] },
      }), { status: 422 });
    }));
    await renderAndSelect();
    await clickIngest();
    expect(host.textContent).toContain('Allow recorded area drift');
  });
});
