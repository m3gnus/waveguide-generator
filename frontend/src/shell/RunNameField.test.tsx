import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { preferencesStore } from '../prefs/preferences';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { workspaceModeStore } from '../stores/workspaceMode';
import { currentJobLabel } from './JobsCoordinator';
import { RunNameField } from './RunNameField';

function enter(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
  input.dispatchEvent(new InputEvent('input', { bubbles: true }));
}
let host: HTMLDivElement, root: Root;
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  resetDesignStore(); resetDocumentStore(); preferencesStore.resetForTests();
  workspaceModeStore.setMode('parametric');
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); });
describe('the parametric document name', () => {
  it('renames the whole document from the one name field', async () => {
    await act(async () => root.render(<RunNameField/>));
    const input = host.querySelector<HTMLInputElement>('[aria-label="Design name"]')!;
    act(() => input.focus());
    act(() => enter(input, 'winner'));
    act(() => input.blur());

    expect(host.querySelector('.run-name-preview')?.textContent).toContain('next · winner1');
    expect(input.title).toContain('Export a copy');
    expect(input.title).not.toContain('saves as');
    // The Export a copy filename follows the same edit -- this is the whole
    // point of the field.
    expect(useDocumentStore.getState()).toMatchObject({ designName: 'winner', filename: 'winner.cfg' });

    // Editing the geometry does not rename anything any more.
    act(() => useDesignStore.getState().updateField('R', 141));
    expect(host.querySelector('.run-name-preview')?.textContent).toContain('next · winner1');
  });

  it('shows the exact dated label that submission will use while editing only the name', async () => {
    const now = new Date(2026, 7, 12, 12);
    preferencesStore.update({ runNameDatePosition: 'prefix' });
    act(() => useDocumentStore.getState().setDesignName('horn'));
    await act(async () => root.render(<RunNameField now={now}/>));

    expect(host.querySelector<HTMLInputElement>('[aria-label="Design name"]')?.value).toBe('horn');
    expect(host.querySelector('.run-name-preview')?.textContent)
      .toBe(`next · ${currentJobLabel(undefined, now)}`);
  });
});
