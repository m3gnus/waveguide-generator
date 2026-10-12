import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { designForFamily, resetDesignStore, useDesignStore, type CrossSectionStation, type FreeformPoint } from '../stores/design';
import type { ParameterDefinition } from './parameterRegistry';
import { EditableLookupTable, EditablePointTable, EditableStationTable } from './FreeformEditors';

const pointField: ParameterDefinition = {
  id: 'freeform.pointsH', legacyKey: 'profile_h.points', section: 'Profile',
  label: 'Horizontal points', kind: 'table',
};

const stationField: ParameterDefinition = {
  id: 'freeform.crossSections', legacyKey: 'cross_sections', section: 'Profile',
  label: 'Cross sections', kind: 'table',
};

function inputWithLabel(host: HTMLElement, text: string): HTMLInputElement {
  const label = [...host.querySelectorAll('label')].find((candidate) => candidate.textContent === text);
  if (!label?.htmlFor) throw new Error(`Missing field label: ${text}`);
  const input = host.ownerDocument.getElementById(label.htmlFor) as HTMLInputElement | null;
  if (!input) throw new Error(`Missing input for: ${text}`);
  return input;
}

function chooseShape(host: HTMLElement, label: string, value: CrossSectionStation['shape']): void {
  const select = host.querySelector<HTMLSelectElement>(`select[aria-label="${label}"]`);
  if (!select) throw new Error(`Missing select: ${label}`);
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set?.call(select, value);
  select.dispatchEvent(new Event('change', { bubbles: true }));
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

  it('edits, adds, removes and pastes LOOKUP pairs without changing their units', () => {
    act(() => useDesignStore.getState().setFamily('LOOKUP'));
    const field: ParameterDefinition = { id: 'lookup.profile', legacyKey: 'lookupProfile', section: 'Profile', label: 'Lookup profile points', kind: 'table' };
    const render = () => act(() => root.render(<EditableLookupTable field={field} points={useDesignStore.getState().design.lookup_profile!}/>));
    render();
    expect(host.querySelectorAll('tbody tr')).toHaveLength(2);
    expect(host.querySelector<HTMLButtonElement>('[aria-label="Remove lookup point 1"]')!.disabled).toBe(true);
    const z = inputWithLabel(host, 'Lookup point 1 z');
    act(() => enterDraft(z, '120'));
    expect(host.querySelector('[role="alert"]')?.textContent).toBe('lookupProfile z values must be strictly increasing');
    act(() => z.blur());
    expect(useDesignStore.getState().design.lookup_profile![0][0]).toBe(0);
    const radius = inputWithLabel(host, 'Lookup point 1 radius');
    act(() => enterDraft(radius, '15'));
    act(() => radius.blur());
    expect(useDesignStore.getState().design.lookup_profile![0][1]).toBe(15);
    render();
    act(() => [...host.querySelectorAll('button')].find((button) => button.textContent === 'Add point')!.click());
    expect(useDesignStore.getState().design.lookup_profile).toEqual([[0, 15], [60, 77.5], [120, 140]]);
    render();
    act(() => host.querySelector<HTMLButtonElement>('[aria-label="Remove lookup point 2"]')!.click());
    expect(useDesignStore.getState().design.lookup_profile).toHaveLength(2);
    render();
    const paste = host.querySelector<HTMLTextAreaElement>('[aria-label="Paste lookup points"]')!;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(paste, 'z,r\n0,18\n40,25\n100,20');
      paste.dispatchEvent(new Event('input', { bubbles: true }));
    });
    act(() => [...host.querySelectorAll('button')].find((button) => button.textContent === 'Import points')!.click());
    expect(useDesignStore.getState().design.lookup_profile).toEqual([[0, 18], [40, 25], [100, 20]]);
    act(() => useDesignStore.getState().undo());
    expect(useDesignStore.getState().design.lookup_profile).toEqual([[0, 15], [120, 140]]);
  });

  it('keeps the z input mounted across its own commit and warns about non-positive radii', () => {
    useDesignStore.getState().loadDesign({ ...designForFamily('LOOKUP'), lookup_profile: [[0, 12.7], [50, 20], [120, 140]] });
    const field: ParameterDefinition = { id: 'lookup.profile', legacyKey: 'lookupProfile', section: 'Profile', label: 'Lookup profile points', kind: 'table' };
    const render = () => act(() => root.render(<EditableLookupTable field={field} points={useDesignStore.getState().design.lookup_profile!}/>));
    render();
    const before = inputWithLabel(host, 'Lookup point 2 z');
    act(() => before.focus());
    act(() => enterDraft(before, '60'));
    act(() => before.blur());
    render();
    expect(useDesignStore.getState().design.lookup_profile![1][0]).toBe(60);
    expect(inputWithLabel(host, 'Lookup point 2 z')).toBe(before);
    expect(host.querySelector('.field-warning')).toBeNull();
    const radius = inputWithLabel(host, 'Lookup point 2 radius');
    act(() => enterDraft(radius, '0'));
    act(() => radius.blur());
    render();
    expect(host.querySelector('.field-warning')?.textContent).toContain('Radius at point 2 is zero or negative');
  });

  it('keeps a z scrub inside its neighbours and makes it one undo step', () => {
    useDesignStore.getState().loadDesign({ ...designForFamily('LOOKUP'), lookup_profile: [[0, 12.7], [50, 20], [120, 140]] });
    const field: ParameterDefinition = { id: 'lookup.profile', legacyKey: 'lookupProfile', section: 'Profile', label: 'Lookup profile points', kind: 'table' };
    const render = () => act(() => root.render(<EditableLookupTable field={field} points={useDesignStore.getState().design.lookup_profile!}/>));
    render();
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(globalThis, 'requestAnimationFrame').mockImplementation((callback) => { frames.push(callback); return frames.length; });
    vi.spyOn(globalThis, 'cancelAnimationFrame').mockImplementation(() => undefined);
    const label = [...host.querySelectorAll('label')].find((item) => item.textContent === 'Lookup point 2 z')!;
    Object.defineProperty(label, 'setPointerCapture', { value: vi.fn() });
    const move = (clientX: number) => {
      act(() => label.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, pointerId: 1, clientX })));
      act(() => frames.splice(0).forEach((frame) => frame(0)));
      render();
    };
    act(() => label.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, pointerId: 1, clientX: 0 })));
    move(100);
    expect(useDesignStore.getState().design.lookup_profile![1][0]).toBe(60);
    // Far past the next row: clamped onto it, and a duplicate z is refused.
    move(5000);
    expect(useDesignStore.getState().design.lookup_profile![1][0]).toBe(60);
    move(150);
    expect(useDesignStore.getState().design.lookup_profile![1][0]).toBe(65);
    act(() => label.dispatchEvent(new PointerEvent('lostpointercapture', { bubbles: true, pointerId: 1 })));
    act(() => useDesignStore.getState().undo());
    expect(useDesignStore.getState().design.lookup_profile).toEqual([[0, 12.7], [50, 20], [120, 140]]);
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

  it('stores the value a newly chosen station shape shows, and drops the one it no longer takes', () => {
    const stored = () => useDesignStore.getState().design.cross_sections!;
    const render = () => act(() => root.render(<EditableStationTable field={stationField} stations={stored()}/>));
    act(() => useDesignStore.getState().updateValue('cross_sections', [{ t: 0, shape: 'ellipse' }, { t: 1, shape: 'ellipse' }]));
    render();

    act(() => chooseShape(host, 'Station 2 shape', 'rounded_rectangle'));
    expect(stored()[1]).toEqual({ t: 1, shape: 'rounded_rectangle', corner_radius_mm: 10 });
    render();
    expect(inputWithLabel(host, 'Station 2 corner radius').value).toBe('10.00');

    act(() => chooseShape(host, 'Station 2 shape', 'superellipse'));
    expect(stored()[1]).toEqual({ t: 1, shape: 'superellipse', exponent: 4 });
    render();
    expect(inputWithLabel(host, 'Station 2 exponent').value).toBe('4.00');

    act(() => chooseShape(host, 'Station 2 shape', 'ellipse'));
    expect(stored()[1]).toEqual({ t: 1, shape: 'ellipse' });
  });

  it('shows the value the mesher will use for a loaded station that omits its parameter', () => {
    const stations: CrossSectionStation[] = [
      { t: 0, shape: 'ellipse' },
      { t: .5, shape: 'superellipse' },
      { t: 1, shape: 'rounded_rectangle' },
    ];
    act(() => root.render(<EditableStationTable field={stationField} stations={stations}/>));

    // The mesher's own default exponent, not a friendlier-looking 4.
    expect(inputWithLabel(host, 'Station 2 exponent').value).toBe('2.00');
    // The mesher has no default corner radius and rejects the station, so the
    // field is empty and says so rather than showing a value that is not there.
    expect(inputWithLabel(host, 'Station 3 corner radius').value).toBe('');
    expect(host.querySelector('[role="alert"]')?.textContent).toBe('This value is required.');
  });
});
