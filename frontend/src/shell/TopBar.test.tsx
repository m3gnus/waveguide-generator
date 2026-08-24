import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import { preferencesStore } from '../prefs/preferences';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { designForFamily, resetDesignStore, serializeDesign, useDesignStore } from '../stores/design';
import { workspaceModeStore } from '../stores/workspaceMode';
import { WorkspaceModeSwitch, workspaceModePaletteEntries } from './TopBar';
import { workspaceNavigation } from './Workspace';

function solvedParametricJob(id: string, design: ReturnType<typeof designForFamily>): JobItem {
  return {
    id, run_number: 1, parent_job_id: null, label: id, rating: null,
    status: 'complete', progress: 1, stage: null, stage_message: null,
    created_at: '2026-08-08T00:00:00Z', queued_at: '2026-08-08T00:00:00Z',
    started_at: null, completed_at: '2026-08-08T00:00:01Z',
    config_summary: {}, solve_options: {} as JobItem['solve_options'],
    has_results: true, has_mesh_artifact: false, error_message: null,
    cancellation_requested: false, mesh_stats: null,
    script_snapshot: { version: 1, design: serializeDesign(design) },
    design_revision: 1, polar_grid: {}, exported_files: [],
    auto_export_completed_at: null, auto_export_formats: {},
    raw_results_file: null, mesh_artifact_file: null, log_tail: [],
  };
}

describe('workspace mode switch', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    preferencesStore.resetForTests();
    resetCadReturnStore();
    workspaceModeStore.setMode('parametric');
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    act(() => root.render(<WorkspaceModeSwitch/>));
  });

  afterEach(() => {
    act(() => root.unmount());
    workspaceModeStore.setMode('parametric');
    vi.restoreAllMocks();
    host.remove();
  });

  it('routes first-time CAD Link users directly to the setup workflow', () => {
    const activate = vi.spyOn(workspaceNavigation, 'activate').mockReturnValue(true);
    const group = host.querySelector('[role="radiogroup"]')!;
    const radios = group.querySelectorAll<HTMLButtonElement>('[role="radio"]');
    expect([...radios].map((button) => button.textContent)).toEqual(['Parametric', 'CAD Link']);
    expect(radios[0].getAttribute('aria-checked')).toBe('true');
    expect(radios[1].disabled).toBe(false);

    act(() => radios[1].click());
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(radios[1].getAttribute('aria-checked')).toBe('true');
    expect(activate).toHaveBeenCalledWith('cadlink');
  });

  it('stays in place when prepared CAD geometry is already available', () => {
    useCadReturnStore.setState({ ingestRecord: { ingest_id: 'wgi_ready' } as never });
    const activate = vi.spyOn(workspaceNavigation, 'activate').mockReturnValue(true);
    const cad = host.querySelectorAll<HTMLButtonElement>('[role="radio"]')[1];

    act(() => cad.click());

    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(activate).not.toHaveBeenCalled();
  });

  // One workflow, one name. Which application sits on the far end of it is a
  // preference, so the mode must read the same either way -- otherwise no
  // document, screenshot, or support answer can name it.
  it('names the mode CAD Link under Onshape as well as Fusion', () => {
    act(() => workspaceModeStore.setMode('cad'));
    act(() => preferencesStore.update({ cadApplication: 'onshape' }));
    const radios = host.querySelectorAll<HTMLButtonElement>('[role="radio"]');
    expect([...radios].map((button) => button.textContent)).toEqual(['Parametric', 'CAD Link']);
    expect(radios[1].disabled).toBe(false);
    // A preferences change must not eject the user from the mode they chose.
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
  });

  // Opening a project from the CAD Link registry replaces the working design
  // in the store, so a plain toggle back to Parametric used to present that
  // CAD project's design as if it were the user's own work.
  it('restores the latest solved parametric design when leaving a CAD-opened project', () => {
    resetDesignStore();
    const cadProject = designForFamily('OSSE');
    cadProject.L = 111;
    const working = designForFamily('OSSE');
    working.L = 321;
    act(() => useDesignStore.getState().replaceDesign(cadProject, { loadSource: 'cad-project-switch' }));
    act(() => workspaceModeStore.setMode('cad'));
    vi.spyOn(jobsSocket, 'getSnapshot').mockReturnValue({
      connection: 'connected', epoch: 1, cursor: 1, error: null,
      jobs: [solvedParametricJob('restore-me', working)],
    } as JobsSnapshot);

    const parametric = host.querySelectorAll<HTMLButtonElement>('[role="radio"]')[0];
    act(() => parametric.click());

    expect(workspaceModeStore.getSnapshot().mode).toBe('parametric');
    expect(useDesignStore.getState().design.L).toBe(321);
    resetDesignStore();
  });

  it('keeps the working design on a plain toggle when no CAD flow replaced it', () => {
    resetDesignStore();
    const working = designForFamily('OSSE');
    working.L = 321;
    act(() => useDesignStore.getState().replaceDesign(working));
    act(() => workspaceModeStore.setMode('cad'));
    const snapshotSpy = vi.spyOn(jobsSocket, 'getSnapshot');

    const parametric = host.querySelectorAll<HTMLButtonElement>('[role="radio"]')[0];
    act(() => parametric.click());

    expect(workspaceModeStore.getSnapshot().mode).toBe('parametric');
    expect(useDesignStore.getState().design.L).toBe(321);
    expect(snapshotSpy).not.toHaveBeenCalled();
    resetDesignStore();
  });

  it('registers both palette commands', () => {
    const activate = vi.spyOn(workspaceNavigation, 'activate').mockReturnValue(true);
    const entries = workspaceModePaletteEntries();
    expect(entries.map((entry) => entry.label)).toEqual(['Mode: Parametric', 'Mode: CAD Link']);
    act(() => entries[1].run());
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(activate).toHaveBeenCalledWith('cadlink');
  });
});
