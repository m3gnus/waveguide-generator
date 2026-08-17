import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resetDesignStore, useDesignStore, type CrossSectionStation, type FreeformPoint } from '../stores/design';
import type { ParameterDefinition } from './parameterRegistry';
import { EditablePointTable, EditableStationTable } from './FreeformEditors';

const pointField: ParameterDefinition = {
  id: 'freeform.pointsH', legacyKey: 'profile_h.points', section: 'Profile Dimensions',
  label: 'Horizontal points', kind: 'table',
};

const stationField: ParameterDefinition = {
  id: 'freeform.crossSections', legacyKey: 'cross_sections', section: 'Profile Dimensions',
  label: 'Cross sections', kind: 'table',
};

function inputWithLabel(host: HTMLElement, text: string): HTMLInputElement {
  const label = [...host.querySelectorAll('label')].find((candidate) => candidate.textContent === text);
  if (!label?.htmlFor) throw new Error(`Missing field label: ${text}`);
  const input = host.ownerDocument.getElementById(label.htmlFor) as HTMLInputElement | null;
  if (!input) throw new Error(`Missing input for: ${text}`);
  return input;
}

function enterDraft(input: HTMLInputElement, value: string): void {
  input.focus();
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

describe('FREEFORM numeric tables', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetDesignStore();
    useDesignStore.getState().setFamily('FREEFORM');
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    vi.restoreAllMocks();
    host.remove();
  });

  it('explains and reverts invalid or blank point values without updating the design', () => {
    const points: FreeformPoint[] = [{ t: 0, r: 12.7 }, { t: .5, r: 60 }, { t: 1, r: 140 }];
    const updateValue = vi.spyOn(useDesignStore.getState(), 'updateValue');
    act(() => root.render(<EditablePointTable field={pointField} points={points}/>));
    const z = inputWithLabel(host, 'H point 1 z');

    act(() => enterDraft(z, '999'));
    expect(host.querySelector('[role="alert"]')?.textContent).toBe('Must be between 1 and 119 mm.');
    act(() => z.blur());
    expect(z.value).toBe('60.0000');
    expect(updateValue).not.toHaveBeenCalled();

    act(() => enterDraft(z, ''));
    expect(host.querySelector('[role="alert"]')?.textContent).toBe('This value is required.');
    act(() => z.blur());
    expect(z.value).toBe('60.0000');
    expect(updateValue).not.toHaveBeenCalled();
  });

  it('explains and reverts out-of-range, blank, or unparsable station values without updating the design', () => {
    const stations: CrossSectionStation[] = [
      { t: 0, shape: 'ellipse' },
      { t: .5, shape: 'superellipse', exponent: 4 },
      { t: 1, shape: 'ellipse' },
    ];
    const updateValue = vi.spyOn(useDesignStore.getState(), 'updateValue');
    act(() => root.render(<EditableStationTable field={stationField} stations={stations}/>));
    const exponent = inputWithLabel(host, 'Station 2 exponent');

    act(() => enterDraft(exponent, '20'));
    expect(host.querySelector('[role="alert"]')?.textContent).toBe('Must be between 2 and 16.');
    act(() => exponent.blur());
    expect(exponent.value).toBe('4.00');
    expect(updateValue).not.toHaveBeenCalled();

    act(() => enterDraft(exponent, ''));
    expect(host.querySelector('[role="alert"]')?.textContent).toBe('This value is required.');
    act(() => exponent.blur());
    expect(exponent.value).toBe('4.00');
    expect(updateValue).not.toHaveBeenCalled();

    act(() => enterDraft(exponent, 'not-a-number'));
    expect(host.querySelector('[role="alert"]')?.textContent).toBe('Enter a valid number.');
    act(() => exponent.blur());
    expect(exponent.value).toBe('4.00');
    expect(updateValue).not.toHaveBeenCalled();
  });
});
