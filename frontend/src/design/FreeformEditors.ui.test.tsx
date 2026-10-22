import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { designForFamily, resetDesignStore, serializeDesign, useDesignStore, type CrossSectionStation, type FreeformPoint } from '../stores/design';
import type { ParameterDefinition } from './parameterRegistry';
import { EditableLookupTable, EditablePointTable, EditableStationTable, FreeformPointImport, FREEFORM_IMPORT_HINT } from './FreeformEditors';

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
    useDesignStore.getState().replaceDocument({ ...designForFamily('LOOKUP'), lookup_profile: [[0, 12.7], [50, 20], [120, 140]] });
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
    useDesignStore.getState().replaceDocument({ ...designForFamily('LOOKUP'), lookup_profile: [[0, 12.7], [50, 20], [120, 140]] });
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
    // Far past the next row: held just short of it, so z stays strictly increasing.
    move(5000);
    expect(useDesignStore.getState().design.lookup_profile![1][0]).toBe(119.9999);
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
    expect(host.querySelector('.position-note')).toBeNull();
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
    expect(host.querySelector('.position-note')).toBeNull();
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

  it('offers circle at the throat and every station with no outline parameters', () => {
    const stored = () => useDesignStore.getState().design.cross_sections!;
    const render = () => act(() => root.render(<EditableStationTable field={stationField} stations={stored()}/>));
    act(() => useDesignStore.getState().updateValue('cross_sections', [
      { t: 0, shape: 'ellipse' }, { t: .5, shape: 'superellipse', exponent: 4 },
      { t: 1, shape: 'rounded_rectangle', corner_radius_mm: 10 },
    ]));
    render();
    const throat = host.querySelector<HTMLSelectElement>('select[aria-label="Station 1 shape"]')!;
    expect(throat.disabled).toBe(false);
    expect([...throat.options].map((option) => option.value)).toEqual(['circle', 'ellipse']);
    for (const index of [1, 2, 3]) {
      act(() => chooseShape(host, `Station ${index} shape`, 'circle'));
      render();
      expect(stored()[index - 1]).toEqual({ t: [0, .5, 1][index - 1], shape: 'circle' });
    }
    expect(host.textContent).not.toContain('exponent');
    expect(host.textContent).not.toContain('corner radius');
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
  it('keeps loaded positions byte-identical through render, focus, blur and save; edits only one station', () => {
    const stations: CrossSectionStation[] = [
      { t: 0, shape: 'ellipse' }, { t: .35123456789123456, shape: 'ellipse' },
      { t: .7123456789123456, shape: 'ellipse' }, { t: 1, shape: 'ellipse' },
    ];
    useDesignStore.getState().replaceDocument({ ...designForFamily('FREEFORM'), length: 200, cross_sections: stations });
    const before = JSON.stringify(serializeDesign(useDesignStore.getState().design));
    const render = () => act(() => root.render(<EditableStationTable field={stationField} stations={useDesignStore.getState().design.cross_sections!}/>));
    render();
    for (const label of ['Station 2 position', 'Station 2 position percent', 'Station 3 position']) {
      const input = inputWithLabel(host, label);
      act(() => input.focus()); act(() => input.blur());
    }
    expect(JSON.stringify(serializeDesign(useDesignStore.getState().design))).toBe(before);
    const input = inputWithLabel(host, 'Station 2 position');
    act(() => enterDraft(input, '70')); act(() => input.blur()); render();
    expect(useDesignStore.getState().design.cross_sections![1].t).toBe(.35);
    expect(useDesignStore.getState().design.cross_sections![2].t).toBe(stations[2].t);
    expect(host.textContent).toContain('70 mm from the throat — 35%');
    act(() => useDesignStore.getState().undo());
    expect(JSON.stringify(serializeDesign(useDesignStore.getState().design))).toBe(before);
  });

  it.each([[74.795, 35], [75.0587058, 35.1234]])('stores identical t for equivalent %s mm and %s percent entries, each as one undo step', (mm, percent) => {
    const initial = { ...designForFamily('FREEFORM'), length: 213.7, cross_sections: [
      { t: 0, shape: 'ellipse' as const }, { t: .5, shape: 'ellipse' as const }, { t: 1, shape: 'ellipse' as const },
    ] };
    const results: number[] = [];
    for (const [label, value] of [['Station 2 position', String(mm)], ['Station 2 position percent', String(percent)]]) {
      act(() => useDesignStore.getState().replaceDocument(initial));
      act(() => root.render(<EditableStationTable field={stationField} stations={useDesignStore.getState().design.cross_sections!}/>));
      const input = inputWithLabel(host, label);
      act(() => enterDraft(input, value)); act(() => input.blur());
      results.push(useDesignStore.getState().design.cross_sections![1].t);
      expect(useDesignStore.temporal.getState().pastStates).toHaveLength(1);
      act(() => useDesignStore.getState().undo());
      expect(useDesignStore.getState().design).toEqual(initial);
    }
    expect(results[0]).toBe(results[1]);
    expect(results[0]).toBe(percent / 100);
  });

  it('moves only the selected point by distance, rejects duplicates and crossings, and undoes the edit', () => {
    const points: FreeformPoint[] = [{ t: 0, r: 12.7 }, { t: .25, r: 30 }, { t: .75, r: 80 }, { t: 1, r: 140 }];
    useDesignStore.getState().replaceDocument({ ...designForFamily('FREEFORM'), length: 200, profile_h: { points } });
    const before = JSON.stringify(serializeDesign(useDesignStore.getState().design));
    const render = () => act(() => root.render(<EditablePointTable field={pointField} points={useDesignStore.getState().design.profile_h!.points}/>));
    render();
    const z = inputWithLabel(host, 'H point 1 z');
    act(() => z.focus()); act(() => z.blur());
    expect(JSON.stringify(serializeDesign(useDesignStore.getState().design))).toBe(before);
    act(() => host.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
    for (const [value, message] of [['150', 'match another point'], ['160', 'no crossing'], ['0', 'match another point']]) {
      const distance = inputWithLabel(host, 'H point 1 distance from previous');
      act(() => enterDraft(distance, value));
      expect(host.textContent).toContain(message);
      act(() => distance.blur());
      expect(useDesignStore.getState().design.profile_h!.points).toEqual(points);
    }
    const distance = inputWithLabel(host, 'H point 1 distance from previous');
    act(() => enterDraft(distance, '60')); act(() => distance.blur()); render();
    expect(useDesignStore.getState().design.profile_h!.points).toEqual([points[0], { t: .3, r: 30 }, points[2], points[3]]);
    expect(useDesignStore.temporal.getState().pastStates).toHaveLength(1);
    act(() => useDesignStore.getState().undo());
    expect(useDesignStore.getState().design.profile_h!.points).toEqual(points);
  });

  it.each(['station', 'point', 'distance', 'radius', 'angle'] as const)('applies the same limits to typed and scrubbed %s values and groups a scrub into one undo', (kind) => {
    const points: FreeformPoint[] = [{ t: 0, r: 12.7 }, { t: .25, r: 30, angle_deg: 20 }, { t: .75, r: 80 }, { t: 1, r: 140 }];
    const initial = { ...designForFamily('FREEFORM'), length: 200, profile_h: { points }, cross_sections: [
      { t: 0, shape: 'ellipse' as const }, { t: .25, shape: 'ellipse' as const }, { t: .75, shape: 'ellipse' as const }, { t: 1, shape: 'ellipse' as const },
    ] };
    useDesignStore.getState().replaceDocument(initial);
    const render = () => act(() => root.render(kind === 'station'
      ? <EditableStationTable field={stationField} stations={useDesignStore.getState().design.cross_sections!}/>
      : <EditablePointTable field={pointField} points={useDesignStore.getState().design.profile_h!.points}/>));
    render();
    if (kind === 'distance') act(() => host.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
    const labelText = kind === 'station' ? 'Station 2 position' : `H point 1 ${kind === 'point' ? 'z' : kind === 'distance' ? 'distance from previous' : kind}`;
    const bad = kind === 'radius' ? '0' : kind === 'angle' ? '90' : '150';
    const input = inputWithLabel(host, labelText);
    act(() => enterDraft(input, bad));
    expect(input.getAttribute('aria-invalid')).toBe('true');
    act(() => input.blur());
    expect(useDesignStore.getState().design).toEqual(initial);
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(globalThis, 'requestAnimationFrame').mockImplementation((callback) => { frames.push(callback); return frames.length; });
    vi.spyOn(globalThis, 'cancelAnimationFrame').mockImplementation(() => undefined);
    const label = [...host.querySelectorAll('label')].find((item) => item.textContent === labelText)!;
    Object.defineProperty(label, 'setPointerCapture', { value: vi.fn() });
    const move = (clientX: number) => {
      act(() => label.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, pointerId: 1, clientX })));
      act(() => frames.splice(0).forEach((frame) => frame(0))); render();
    };
    act(() => label.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, pointerId: 1, clientX: 0 })));
    move(kind === 'radius' ? -300 : kind === 'angle' ? 700 : 1000);
    expect(useDesignStore.getState().design).toEqual(initial);
    move(10); move(20);
    act(() => label.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 1 })));
    expect(useDesignStore.getState().design).not.toEqual(initial);
    expect(useDesignStore.temporal.getState().pastStates).toHaveLength(1);
    act(() => useDesignStore.getState().undo());
    expect(useDesignStore.getState().design).toEqual(initial);
  });

});

describe('FREEFORM shared import and point controls', () => {
  let host: HTMLDivElement;
  let root: Root;
  const paste = (text: string) => act(() => {
    const textarea = host.querySelector('textarea')!;
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(textarea, text);
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  });
  const importButton = () => host.querySelector<HTMLButtonElement>('.point-paste button')!;
  const choose = (value: string) => act(() => host.querySelector<HTMLInputElement>(`input[value="${value}"]`)!.click());
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetDesignStore();
    useDesignStore.getState().setFamily('FREEFORM');
    useDesignStore.temporal.getState().clear();
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    act(() => root.render(<FreeformPointImport />));
  });
  afterEach(() => { act(() => root.unmount()); host.remove(); });

  it('explains the format and disabled import, and shows no summary before a valid parse (B2, B7)', () => {
    expect(host.textContent).toContain(FREEFORM_IMPORT_HINT);
    expect(importButton().disabled).toBe(true);
    expect(host.textContent).toContain('Paste valid points to import.');
    expect(importButton().getAttribute('aria-describedby')).toBe(host.querySelector('.disabled-reason')!.id);
    expect(host.querySelector('.paste-meta')).toBeNull();
    paste('not points');
    expect(host.querySelector('[role="alert"]')).not.toBeNull();
    expect(host.querySelector('.paste-meta')).toBeNull();
    expect(host.textContent).not.toContain('—');
  });

  it('clears parse and length errors as the paste or length choice changes (B1)', () => {
    paste('0 12.7\n10 30');
    expect(importButton().disabled).toBe(true);
    expect(host.textContent).toContain('Imported length must be between 20 and 1000 mm.');
    act(() => host.querySelectorAll<HTMLInputElement>('fieldset')[1].querySelectorAll('input')[1].click());
    expect(host.querySelector('[role="alert"]')).toBeNull();
    expect(importButton().disabled).toBe(false);
    paste('0 bad\n120 200');
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('non-numeric');
    paste('0 12.7\n120 200');
    expect(host.querySelector('[role="alert"]')).toBeNull();
    expect(importButton().disabled).toBe(false);
  });

  it('forces Both for compact two-plane data and retains both distinct profiles (B3)', () => {
    choose('V');
    paste('0;1.5;1.5\n6;5;4\n12;20;10');
    expect(host.textContent).toContain('This z;H;V paste contains both planes, so it imports into Both.');
    expect(host.querySelector<HTMLInputElement>('input[value="both"]')!.checked).toBe(true);
    expect([...host.querySelectorAll<HTMLInputElement>('fieldset:first-of-type input')].every((input) => input.disabled)).toBe(true);
    expect(importButton().textContent).toBe('Import into both profiles');
    act(() => importButton().click());
    const design = useDesignStore.getState().design;
    expect(design.profile_h!.points).toEqual([{ t: 0, r: 15 }, { t: .5, r: 50 }, { t: 1, r: 200 }]);
    expect(design.profile_v!.points).toEqual([{ t: 0, r: 15 }, { t: .5, r: 40 }, { t: 1, r: 100 }]);
  });

  it('explains conflicting compact throat radii without changing the design (B5)', () => {
    const before = serializeDesign(useDesignStore.getState().design);
    paste('0;1.5;2\n12;20;10');
    expect(host.textContent).toContain('The throat must be one circle.');
    expect(importButton().disabled).toBe(true);
    expect(serializeDesign(useDesignStore.getState().design)).toEqual(before);
  });

  it.each(['H', 'V'])('mirrors a %s-only imported throat, preserves the other plane and undoes atomically (B5, B8)', (plane) => {
    const before = structuredClone(useDesignStore.getState().design);
    choose(plane);
    paste('0 18 15\n50 60\n200 180 65');
    act(() => importButton().click());
    const design = useDesignStore.getState().design;
    const selected = plane === 'H' ? design.profile_h! : design.profile_v!;
    const other = plane === 'H' ? design.profile_v! : design.profile_h!;
    const oldOther = plane === 'H' ? before.profile_v! : before.profile_h!;
    expect(selected.points).toEqual([{ t: 0, r: 18, angle_deg: 15 }, { t: .25, r: 60 }, { t: 1, r: 180, angle_deg: 65 }]);
    expect(other.points).toEqual([{ ...oldOther.points[0], r: 18 }, ...oldOther.points.slice(1)]);
    expect(design.length).toBe(200);
    expect(host.querySelector('textarea')!.value).toBe('');
    expect(host.textContent).toContain(`Imported 3 points into the ${plane === 'H' ? 'horizontal' : 'vertical'} profile. Undo restores the previous profile.`);
    expect(importButton().disabled).toBe(true);
    act(() => importButton().click());
    expect(useDesignStore.temporal.getState().pastStates).toHaveLength(1);
    act(() => useDesignStore.getState().undo());
    expect(useDesignStore.getState().design).toEqual(before);
  });

  it('groups named radios, hides equal-length choices and stretches normalized points to the kept length (B6)', () => {
    paste('0 12.7\n60 60\n120 200');
    expect(host.querySelectorAll('fieldset')).toHaveLength(1);
    paste('0 12.7\n100 60\n200 200');
    const fieldsets = [...host.querySelectorAll('fieldset')];
    expect(fieldsets).toHaveLength(2);
    for (const fieldset of fieldsets) {
      const names = [...fieldset.querySelectorAll('input')].map((input) => input.name);
      expect(names[0]).toBeTruthy(); expect(new Set(names).size).toBe(1);
      expect(fieldset.querySelector('legend')).not.toBeNull();
      expect(fieldset.querySelectorAll('.ui-check').length).toBe(names.length);
    }
    expect(fieldsets[0].querySelector('input')!.name).not.toBe(fieldsets[1].querySelector('input')!.name);
    expect(host.textContent).toContain('Use pasted length (200 mm)');
    expect(host.textContent).toContain('Keep 120 mm and stretch the pasted points to fit');
    act(() => fieldsets[1].querySelectorAll('input')[1].click());
    act(() => importButton().click());
    expect(useDesignStore.getState().design.length).toBe(120);
    expect(useDesignStore.getState().design.profile_h!.points[1].t).toBe(.5);
  });

  it('can apply single-plane data to Both without changing stations', () => {
    const stations = structuredClone(useDesignStore.getState().design.cross_sections);
    choose('both'); paste('0 16\n60 45\n120 200');
    act(() => importButton().click());
    const design = useDesignStore.getState().design;
    expect(design.profile_h!.points).toEqual(design.profile_v!.points);
    expect(design.cross_sections).toEqual(stations);
  });

  it('shows the empty state, inserts in the largest gap and uses auto angles and a ToggleRow', () => {
    const render = () => act(() => root.render(<EditablePointTable field={pointField} points={useDesignStore.getState().design.profile_h!.points} />));
    render();
    expect(host.querySelector('[role="group"]')?.getAttribute('aria-label')).toBe(pointField.label);
    expect(host.textContent).toContain('No spline points. The curve runs from throat to mouth, shaped by the throat and mouth angles. Add point inserts one in the largest gap.');
    expect([...host.querySelectorAll('th')].map((th) => th.textContent)).toEqual(['z (mm)', 'r (mm)', 'Angle (°)', '']);
    expect(host.querySelector('.point-paste')).toBeNull();
    act(() => host.querySelector<HTMLButtonElement>('.editable-parameter-table > button')!.click());
    render();
    expect(useDesignStore.getState().design.profile_h!.points[1].t).toBe(.5);
    expect(host.textContent).not.toContain('No spline points.');
    expect(inputWithLabel(host, 'H point 1 angle').placeholder).toBe('auto');
    expect(host.querySelector('.toggle-row')?.textContent).toContain('Show spacing column');
    act(() => host.querySelector<HTMLInputElement>('.toggle-row input')!.click());
    expect(host.querySelector('th:nth-child(2)')?.textContent).toBe('Spacing (mm)');
    expect(host.querySelector('button[aria-label="Remove H point 1"]')?.getAttribute('type')).toBe('button');
  });

  it('explains why Add point is disabled at the limit (B7)', () => {
    const points = Array.from({ length: 64 }, (_, index) => ({ t: index / 63, r: 12.7 + index }));
    act(() => root.render(<EditablePointTable field={pointField} points={points} />));
    expect(host.querySelector<HTMLButtonElement>('.editable-parameter-table > button')!.disabled).toBe(true);
    expect(host.textContent).toContain('A profile supports at most 62 spline points. Remove a point before adding another.');
  });
});
