import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadReturnIngestRecord } from '../api/cadlink';
import { buildImportedSubmission } from '../jobs/importedSubmission';
import { projectSubmittedImport } from '../jobs/submittedProjection';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { resetDocumentStore } from '../stores/document';
import { resetSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { JobsPreferencesSurface, ResultsPreferencesSurface } from './PreferencesSurface';
import { preferencesStore } from './preferences';

function readyCadRecord(ingestId: string): CadReturnIngestRecord {
  return {
    ingest_id: ingestId, manifest_sha256: `manifest:${ingestId}`, artifact_sha256: `artifact:${ingestId}`, report_sha256: `report:${ingestId}`,
    findings: [], evidence: { fem_air_volumes: [] }, polar_grid_derivation: {},
  } as unknown as CadReturnIngestRecord;
}

describe('preferences surfaces', () => {
  let host: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    localStorage.clear(); preferencesStore.resetForTests(); resetCadReturnStore(); resetDesignStore(); resetDocumentStore(); resetSolveOptionsStore(); workspaceModeStore.setMode('parametric');
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  });
  afterEach(() => { act(() => root.unmount()); vi.unstubAllGlobals(); host.remove(); workspaceModeStore.setMode('parametric'); });

  it('renders all smoothing, reference, export, automation, and format controls', async () => {
    await act(async () => { root.render(<ResultsPreferencesSurface/>); await Promise.resolve(); });
    expect(host.querySelector<HTMLSelectElement>('[aria-label="Smoothing"]')?.options).toHaveLength(11);
    expect(host.querySelector<HTMLSelectElement>('[aria-label="Map reference"]')?.options).toHaveLength(4);
    expect(host.querySelector<HTMLSelectElement>('[aria-label="Results layout count"]')?.options).toHaveLength(5);
    expect(host.querySelector('[aria-label="Export counter"]')).toBeNull();
    expect(host.querySelectorAll('[aria-label^="Manual export:"]')).toHaveLength(11);
    expect(host.querySelectorAll('[aria-label^="Automatic export:"]')).toHaveLength(11);
    expect(host.textContent).toContain('Preferred manual export formats');
    expect(host.textContent).toContain('Automatic export formats');
    expect(host.textContent).toContain('Auto-export completed jobs');
    expect(host.textContent).toContain('Auto-save solve mesh to Workspace');
  });

  it('edits manual and automatic formats independently and warns about an empty enabled auto list', async () => {
    await act(async () => { root.render(<ResultsPreferencesSurface/>); await Promise.resolve(); });
    const autoToggle = [...host.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')]
      .find((input) => input.parentElement?.textContent?.includes('Auto-export completed jobs'))!;
    act(() => autoToggle.click());
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('will not write any files');

    act(() => host.querySelector<HTMLInputElement>('[aria-label="Automatic export: Frequency Data CSV"]')!.click());
    expect(preferencesStore.getSnapshot().autoExportFormats).toEqual(['csv']);
    expect(preferencesStore.getSnapshot().exportFormats).toEqual(['csv', 'png']);
    expect(host.querySelector('[role="alert"]')).toBeNull();
  });

  it('renders design-tracking job naming, sort, and rating-filter controls', () => {
    act(() => root.render(<JobsPreferencesSurface now={new Date(2026, 7, 12, 12)}/>));
    expect(host.querySelector('[aria-label="Job design name"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="Next job version"]')).toBeNull();
    expect(host.querySelector('[aria-label="Prefix job name with date"]')).toBeNull();
    expect(host.querySelector<HTMLSelectElement>('[aria-label="Run-name date position"]')?.value).toBe('off');
    expect(host.querySelector<HTMLSelectElement>('[aria-label="Run-name date format"]')?.disabled).toBe(true);
    expect(host.querySelector<HTMLSelectElement>('[aria-label="Run-name number position"]')?.value).toBe('suffix');
    expect(host.querySelector<HTMLSelectElement>('[aria-label="Run-name number format"]')?.value).toBe('natural');
    expect(host.querySelector('.job-run-name-field')).not.toBeNull();
    expect(host.textContent).toContain('next · horn');
    expect(host.querySelector<HTMLSelectElement>('[aria-label="Default task sort"]')?.options).toHaveLength(4);
    expect(host.querySelector<HTMLSelectElement>('[aria-label="Minimum rating filter"]')?.options).toHaveLength(6);
  });

  it('previews the same decorated label while keeping the editable core undated', () => {
    const now = new Date(2026, 7, 12, 12);
    act(() => root.render(<JobsPreferencesSurface now={now}/>));
    const position = host.querySelector<HTMLSelectElement>('[aria-label="Run-name date position"]')!;
    const format = host.querySelector<HTMLSelectElement>('[aria-label="Run-name date format"]')!;

    act(() => {
      position.value = 'suffix';
      position.dispatchEvent(new Event('change', { bubbles: true }));
    });

    expect(host.querySelector<HTMLInputElement>('[aria-label="Job design name"]')?.value).toBe('horn');
    expect(host.querySelector('.job-name-preview')?.textContent).toContain('next · horn_260812');
    expect(preferencesStore.getSnapshot()).toMatchObject({ outputName: 'horn', runNameDatePosition: 'suffix' });
    expect(format.disabled).toBe(false);

    act(() => {
      format.value = 'yyyy-mm-dd';
      format.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(host.querySelector('.job-name-preview')?.textContent).toContain('next · horn_2026-08-12');
  });

  it('uses the imported projection for the CAD naming preview', () => {
    useCadReturnStore.setState({
      ingestRecord: readyCadRecord('wgi_first'), needsIngest: false,
      driveChannels: [{ id: 'drive', source_ids: ['source'], motion: 'normal' }],
      sourceSizesMm: { source: 2 }, rigidSizeMm: 5, transitionMm: 5,
    });
    workspaceModeStore.setMode('cad');
    const baseline = projectSubmittedImport(buildImportedSubmission(useCadReturnStore.getState()));
    preferencesStore.update({ outputName: 'cad-run', nameSourceProjection: baseline });
    act(() => root.render(<JobsPreferencesSurface/>));

    expect(host.querySelector('.job-name-preview')?.textContent).toContain('next · cad-run');
    act(() => useDesignStore.getState().updateField('R', 141));
    expect(host.querySelector('.job-name-preview')?.textContent).toContain('next · cad-run');
    act(() => useCadReturnStore.setState({ ingestRecord: readyCadRecord('wgi_second') }));
    expect(host.querySelector('.job-name-preview')?.textContent).toContain('next · cad-run2');
  });

  it('configures design-change numbering independently of the date', () => {
    act(() => root.render(<JobsPreferencesSurface/>));
    const position = host.querySelector<HTMLSelectElement>('[aria-label="Run-name number position"]')!;
    const format = host.querySelector<HTMLSelectElement>('[aria-label="Run-name number format"]')!;

    act(() => {
      position.value = 'off';
      position.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(format.disabled).toBe(true);

    act(() => {
      position.value = 'suffix';
      position.dispatchEvent(new Event('change', { bubbles: true }));
      format.value = '3-digit';
      format.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(preferencesStore.getSnapshot()).toMatchObject({
      runNameNumberPosition: 'suffix',
      runNameNumberFormat: '3-digit',
    });
  });

  // The gear popovers portal to <body>, so they are queried from the document
  // rather than from the render host. See AnchoredPanel for why.
  it('renders both preference groups as closeable gear popovers', async () => {
    const close = vi.fn();
    await act(async () => { root.render(<ResultsPreferencesSurface popover onClose={close}/>); await Promise.resolve(); });
    expect(document.body.querySelector('[aria-label="Results and export preferences"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="Results and export preferences"]')).toBeNull();
    act(() => document.body.querySelector<HTMLButtonElement>('[aria-label="Close results preferences"]')!.click());
    expect(close).toHaveBeenCalledOnce();
    act(() => root.render(<JobsPreferencesSurface popover onClose={close}/>));
    expect(document.body.querySelector('[aria-label="Job preferences"]')).not.toBeNull();
    act(() => document.body.querySelector<HTMLButtonElement>('[aria-label="Close job preferences"]')!.click());
    expect(close).toHaveBeenCalledTimes(2);
  });

  it('closes a gear popover on Escape and on a press outside it', async () => {
    const close = vi.fn();
    await act(async () => { root.render(<ResultsPreferencesSurface popover onClose={close}/>); await Promise.resolve(); });
    act(() => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); });
    expect(close).toHaveBeenCalledOnce();
    act(() => { document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true })); });
    expect(close).toHaveBeenCalledTimes(2);
  });

  it('keeps a press inside the popover from closing it', async () => {
    const close = vi.fn();
    await act(async () => { root.render(<ResultsPreferencesSurface popover onClose={close}/>); await Promise.resolve(); });
    const panel = document.body.querySelector('[aria-label="Results and export preferences"]')!;
    act(() => { panel.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true })); });
    expect(close).not.toHaveBeenCalled();
  });
});
