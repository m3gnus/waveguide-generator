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

    expect(host.querySelector('.run-name-preview')?.textContent).toContain('Next · winner1');
    expect(input.title).toContain('design file');
    expect(input.title).not.toContain('saves as');
    // The design file's name follows the same edit -- this is the whole
    // point of the field.
    expect(useDocumentStore.getState()).toMatchObject({ designName: 'winner', filename: 'winner.cfg' });

    // Editing the geometry does not rename anything any more.
    act(() => useDesignStore.getState().updateField('R', 141));
    expect(host.querySelector('.run-name-preview')?.textContent).toContain('Next · winner1');
  });

  it('shows the exact dated label that submission will use while editing only the name', async () => {
    const now = new Date(2026, 7, 12, 12);
    preferencesStore.update({ runNameDatePosition: 'prefix' });
    act(() => useDocumentStore.getState().setDesignName('horn'));
    await act(async () => root.render(<RunNameField now={now}/>));

    expect(host.querySelector<HTMLInputElement>('[aria-label="Design name"]')?.value).toBe('horn');
    expect(host.querySelector('.run-name-preview')?.textContent)
      .toBe(`Next · ${currentJobLabel(undefined, now)}`);
  });

  it('sets the next run number for the design on screen and hides it when numbering is off', async () => {
    act(() => useDocumentStore.getState().setDesignName('horn'));
    await act(async () => root.render(<RunNameField/>));
    const number = host.querySelector<HTMLInputElement>('[aria-label="Next run number"]')!;
    expect(number.value).toBe('1');
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
      setter.call(number, '7');
      number.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(host.querySelector('.run-name-preview')!.textContent).toBe('Next · horn7');
    expect(preferencesStore.getSnapshot()).toMatchObject({ runSequenceName: 'horn', runSequenceNext: 7 });
    act(() => preferencesStore.update({ runNameNumberPosition: 'off' }));
    expect(host.querySelector('[aria-label="Next run number"]')).toBeNull();
    expect(host.querySelector('.run-name-preview')!.textContent).toBe('Next · horn');
  });

  it('places the Job preferences button beside the design name', async () => {
    await act(async () => root.render(<RunNameField><button aria-label="Job preferences"/></RunNameField>));
    expect(host.querySelector('.run-name-row [aria-label="Design name"]')).not.toBeNull();
    expect(host.querySelector('.run-name-row [aria-label="Job preferences"]')).not.toBeNull();
  });
});
