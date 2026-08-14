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

  it('routes first-time Fusion CAD users directly to the setup workflow', () => {
    const activate = vi.spyOn(workspaceNavigation, 'activate').mockReturnValue(true);
    const group = host.querySelector('[role="radiogroup"]')!;
    const radios = group.querySelectorAll<HTMLButtonElement>('[role="radio"]');
    expect([...radios].map((button) => button.textContent)).toEqual(['Parametric', 'Fusion CAD']);
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

  it('disables CAD under Onshape with the Fusion-only reason and exits a stale CAD mode', () => {
    act(() => workspaceModeStore.setMode('cad'));
    act(() => preferencesStore.update({ cadApplication: 'onshape' }));
    const radios = host.querySelectorAll<HTMLButtonElement>('[role="radio"]');
    expect([...radios].map((button) => button.textContent)).toEqual(['Parametric', 'CAD']);
    expect(radios[1].disabled).toBe(true);
    expect(radios[1].title).toContain('return leg is Fusion-only');
    expect(workspaceModeStore.getSnapshot().mode).toBe('parametric');
  });

  it('registers both palette commands and applies the Onshape gate there too', () => {
    const activate = vi.spyOn(workspaceNavigation, 'activate').mockReturnValue(true);
    const fusion = workspaceModePaletteEntries('fusion360');
    expect(fusion.map((entry) => entry.label)).toEqual(['Mode: Parametric', 'Mode: Fusion CAD']);
    act(() => fusion[1].run());
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(activate).toHaveBeenCalledWith('cadlink');

    const onshape = workspaceModePaletteEntries('onshape');
    expect(onshape[1]).toMatchObject({ disabled: true, detail: 'The CAD return leg is Fusion-only.' });
  });
});
