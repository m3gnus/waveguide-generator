import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { preferencesStore } from '../prefs/preferences';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { workspaceModeStore } from '../stores/workspaceMode';
import { WorkspaceModeSwitch, workspaceModePaletteEntries } from './TopBar';
import { workspaceNavigation } from './Workspace';

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

  it('registers both palette commands', () => {
    const activate = vi.spyOn(workspaceNavigation, 'activate').mockReturnValue(true);
    const entries = workspaceModePaletteEntries();
    expect(entries.map((entry) => entry.label)).toEqual(['Mode: Parametric', 'Mode: CAD Link']);
    act(() => entries[1].run());
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(activate).toHaveBeenCalledWith('cadlink');
  });
});
