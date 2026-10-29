import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { designForFamily, resetDesignStore, serializeDesign, useDesignStore, type CrossSectionStation, type FreeformPoint } from '../stores/design';
import type { ParameterDefinition } from './parameterRegistry';
import { stubProfileServer } from '../testing/customProfileServer';
import { CustomProfileLengthField, CustomProfilePaste, CustomProfileTable, EditableLookupTable, EditableStationTable, FREEFORM_IMPORT_HINT } from './FreeformEditors';

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

  it.each(['station'] as const)('applies the same limits to typed and scrubbed %s values and groups a scrub into one undo', (kind) => {
    const points: FreeformPoint[] = [{ t: 0, r: 12.7 }, { t: .25, r: 30, angle_deg: 20 }, { t: .75, r: 80 }, { t: 1, r: 140 }];
    const initial = { ...designForFamily('FREEFORM'), length: 200, profile_h: { points }, cross_sections: [
      { t: 0, shape: 'ellipse' as const }, { t: .25, shape: 'ellipse' as const }, { t: .75, shape: 'ellipse' as const }, { t: 1, shape: 'ellipse' as const },
    ] };
    useDesignStore.getState().replaceDocument(initial);
    const render = () => act(() => root.render(<EditableStationTable field={stationField} stations={useDesignStore.getState().design.cross_sections!}/>));
    render();
    const labelText = `${kind === 'station' ? 'Station' : ''} 2 position`.trim();
    const bad = '150';
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
    move(1000);
    expect(useDesignStore.getState().design).toEqual(initial);
    move(10); move(20);
    act(() => label.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 1 })));
    expect(useDesignStore.getState().design).not.toEqual(initial);
    expect(useDesignStore.temporal.getState().pastStates).toHaveLength(1);
    act(() => useDesignStore.getState().undo());
    expect(useDesignStore.getState().design).toEqual(initial);
  });

});

describe('Custom profile table, insertion, paste and Length', () => {
  let host: HTMLDivElement;
  let root: Root;
  const hField: ParameterDefinition = { id: 'freeform.interiorH', legacyKey: 'interiorH', path: 'profile_h.points', section: 'Profile', label: 'Horizontal profile', kind: 'table' };
  const vField: ParameterDefinition = { ...hField, id: 'freeform.interiorV', legacyKey: 'interiorV', path: 'profile_v.points', label: 'Vertical profile' };
  const lengthField: ParameterDefinition = { id: 'freeform.length', legacyKey: 'length', path: 'length', section: 'Profile', label: 'Length', kind: 'number', min: 20, max: 1_000 };
  const button = (text: string) => [...host.querySelectorAll('button')].find((candidate) => candidate.textContent === text)!;
  const settle = async () => { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); }); };
  async function accept() {
    await vi.waitFor(async () => { await settle(); expect(button('Accept')?.disabled).toBe(false); });
    act(() => button('Accept').click());
  }
  const commitDraft = (label: string, value: string) => { const input = inputWithLabel(host, label); act(() => enterDraft(input, value)); act(() => input.blur()); };
  function load(design = designForFamily('FREEFORM')) {
    act(() => useDesignStore.getState().replaceDocument(design));
  }

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetDesignStore();
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  });
  afterEach(() => { act(() => root.unmount()); host.remove(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it('shows every anchor with endpoints, joins, angles and the resolved state', async () => {
    stubProfileServer();
    const design = designForFamily('FREEFORM');
    design.profile_h!.points = [{ t: 0, r: 12.7 }, { t: .5, r: 60, join: 'corner', angle_deg: 25, handle_out: [6, 3] }, { t: 1, r: 140 }];
    load(design);
    act(() => root.render(<CustomProfileTable field={hField} plane="H" />));
    await vi.waitFor(async () => { await settle(); expect(host.querySelector('[data-anchor-index="1"] .anchor-state')?.textContent).toContain('pinned out'); });
    expect([...host.querySelectorAll('th')].map((th) => th.textContent)).toEqual(['z (mm)', 'r (mm)', 'Join', 'In (°)', 'Out (°)', 'State', '']);
    expect(host.querySelectorAll('tbody tr')).toHaveLength(3);
    expect(inputWithLabel(host, 'H throat z').disabled).toBe(true);
    expect(inputWithLabel(host, 'H mouth z').disabled).toBe(true);
    expect(inputWithLabel(host, 'H throat angle in').disabled).toBe(true);
    expect(inputWithLabel(host, 'H mouth angle out').disabled).toBe(true);
    expect(inputWithLabel(host, 'H point 1 z').value).toBe('60.0000');
    expect(inputWithLabel(host, 'H point 1 angle in').value).toBe('25.00');
    expect(inputWithLabel(host, 'H point 1 angle out').placeholder).toBe('= in');
    expect(host.querySelector<HTMLSelectElement>('select[aria-label="H point 1 join"]')!.value).toBe('corner');
    expect(host.querySelector('[data-anchor-index="0"] .anchor-state')?.textContent).toContain('profile throat angle');
    expect(host.querySelector('button[aria-label="Delete H throat"]')).toBeNull();
    expect(host.textContent).toContain('Separate vertical profile');
    expect(host.querySelector('.custom-profile-scroll table')).not.toBeNull();
  });

  it('inserts on the curve by default, previews both measurements, and accepts as one Undo step', async () => {
    const server = stubProfileServer();
    load();
    const initial = serializeDesign(useDesignStore.getState().design);
    act(() => root.render(<CustomProfileTable field={hField} plane="H" />));
    commitDraft('Insert H anchor at z', '40');
    expect(host.querySelector<HTMLSelectElement>('select[aria-label="H insertion mode"]')!.value).toBe('exact');
    act(() => button('Add point').click());
    await vi.waitFor(async () => { await settle(); expect(host.textContent).toContain('Generated profile change'); expect(host.textContent).toContain('largest radius change 0 mm'); });
    expect(host.textContent).toContain('H: curve deviation 0.125 mm at z = 40 mm over the full profile');
    expect(host.textContent).toContain('Sampled estimate over 16390 z values, not a certified maximum.');
    expect(host.textContent).toContain('Exact insertion at z = 40 mm in H');
    const insert = server.mock.calls.find(([url]) => String(url).endsWith('/insert'))!;
    expect(JSON.parse(String(insert[1]?.body))).toMatchObject({ plane: 'H', z_mm: 40, mode: 'exact' });
    expect(serializeDesign(useDesignStore.getState().design)).toEqual(initial);
    await accept();
    const points = useDesignStore.getState().design.profile_h!.points;
    expect(points).toHaveLength(3);
    expect(points[1]).toEqual({ t: 40 / 120, r: 50, join: 'smooth', handle_in: [-5, -2], handle_out: [5, 2] });
    expect(useDesignStore.temporal.getState().pastStates).toHaveLength(1);
    expect(host.textContent).toContain('Undo restores the previous profile.');
    act(() => useDesignStore.getState().undo());
    expect(serializeDesign(useDesignStore.getState().design)).toEqual(initial);
    act(() => useDesignStore.getState().redo());
    expect(useDesignStore.getState().design.profile_h!.points[1].handle_out).toEqual([5, 2]);
  });

  it('cancels without touching the document or history and refuses invalid input locally', async () => {
    stubProfileServer();
    const design = designForFamily('FREEFORM');
    design.profile_h!.points = [{ t: 0, r: 12.7 }, { t: .5, r: 60 }, { t: 1, r: 140 }];
    load(design);
    act(() => root.render(<CustomProfileTable field={hField} plane="H" />));
    const radius = inputWithLabel(host, 'H point 1 radius');
    act(() => enterDraft(radius, '0'));
    expect(radius.getAttribute('aria-invalid')).toBe('true');
    expect(host.textContent).toContain('Radius must be greater than 0 mm.');
    act(() => radius.blur());
    const z = inputWithLabel(host, 'H point 1 z');
    act(() => enterDraft(z, '130'));
    expect(host.textContent).toContain('strictly between its neighbours');
    act(() => z.blur());
    commitDraft('H point 1 radius', '70');
    await vi.waitFor(async () => { await settle(); expect(button('Accept')).toBeTruthy(); });
    act(() => button('Cancel').click());
    expect(useDesignStore.getState().design.profile_h!.points[1].r).toBe(60);
    expect(useDesignStore.temporal.getState().pastStates).toHaveLength(0);
  });

  it('shows a server refusal and keeps the current design', async () => {
    stubProfileServer({ refuse: 'FREEFORM profileH span 1 has a handle that reverses z' });
    load();
    act(() => root.render(<CustomProfileTable field={hField} plane="H" />));
    act(() => { const select = host.querySelector<HTMLSelectElement>('select[aria-label="H mouth join"]')!; Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(select, 'corner'); select.dispatchEvent(new Event('change', { bubbles: true })); });
    await vi.waitFor(async () => { await settle(); expect(host.textContent).toContain('Not valid: FREEFORM profileH span 1 has a handle that reverses z'); });
    expect(button('Accept').disabled).toBe(true);
    expect(useDesignStore.getState().design.profile_h!.points[1]).not.toHaveProperty('join');
  });

  it('deletes an interior anchor and releases its neighbours\' facing handles after preview', async () => {
    stubProfileServer();
    const design = designForFamily('FREEFORM');
    design.profile_h!.points = [{ t: 0, r: 12.7, handle_out: [3, 1] }, { t: .5, r: 60 }, { t: 1, r: 140, handle_in: [-4, -2] }];
    load(design);
    act(() => root.render(<CustomProfileTable field={hField} plane="H" />));
    act(() => host.querySelector<HTMLButtonElement>('button[aria-label="Delete H point 1"]')!.click());
    await vi.waitFor(async () => { await settle(); expect(host.textContent).toContain('Released the facing handles'); });
    await accept();
    expect(useDesignStore.getState().design.profile_h!.points).toEqual([{ t: 0, r: 12.7 }, { t: 1, r: 140 }]);
  });

  it('pastes lookup-style rows as corners by default and explains semicolon rows', async () => {
    stubProfileServer();
    load();
    act(() => root.render(<CustomProfilePaste />));
    expect(host.textContent).toContain(FREEFORM_IMPORT_HINT);
    const paste = (text: string) => act(() => {
      const textarea = host.querySelector('textarea')!;
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(textarea, text);
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
    });
    paste('0;12,7\n120;140');
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('for z and r in mm, separate them with a comma, tab or space');
    paste('z_mm,r_mm\n0,12.7\n60,40\n120,140');
    expect(host.querySelector<HTMLInputElement>('input[name$="-join"]:checked')!.parentElement!.textContent).toContain('Corners');
    act(() => button('Import into the horizontal profile').click());
    await accept();
    const design = useDesignStore.getState().design;
    expect(design.profile_h!.points).toEqual([{ t: 0, r: 12.7, join: 'corner' }, { t: .5, r: 40, join: 'corner' }, { t: 1, r: 140, join: 'corner' }]);
    expect(design.profile_v!.points[0].r).toBe(12.7);
    expect(useDesignStore.temporal.getState().pastStates).toHaveLength(1);
  });

  it('offers only Stretch profile or Move mouth for a Length change, each previewed', async () => {
    stubProfileServer();
    const design = designForFamily('FREEFORM');
    design.profile_h!.points = [{ t: 0, r: 12.7 }, { t: .5, r: 60, handle_out: [10, 2] }, { t: 1, r: 140 }];
    load(design);
    act(() => root.render(<CustomProfileLengthField field={lengthField} />));
    commitDraft('Length', '50');
    expect(useDesignStore.getState().design.length).toBe(120);
    act(() => button('Move mouth').click());
    await vi.waitFor(async () => { await settle(); expect(host.textContent).toContain('at or beyond the new mouth'); });
    act(() => button('Close').click());
    commitDraft('Length', '240');
    act(() => button('Stretch profile').click());
    await accept();
    const stretched = useDesignStore.getState().design;
    expect(stretched.length).toBe(240);
    expect(stretched.profile_h!.points[1]).toEqual({ t: .5, r: 60, handle_out: [20, 2] });
    expect(useDesignStore.temporal.getState().pastStates).toHaveLength(1);
    commitDraft('Length', '300');
    act(() => button('Move mouth').click());
    await accept();
    expect(Math.abs(useDesignStore.getState().design.profile_h!.points[1].t * 300 - 120)).toBeLessThan(1e-12);
    expect(useDesignStore.getState().design.profile_h!.points[1].handle_out).toEqual([20, 2]);
  });

  it('separates V from a new shared design without changing it, keeps V when shared again, and reconciles the throat explicitly', async () => {
    stubProfileServer();
    useDesignStore.getState().setFamily('FREEFORM');
    useDesignStore.temporal.getState().clear();
    expect(useDesignStore.getState().design.shared_profile).toBe(true);
    act(() => root.render(<><CustomProfileTable field={hField} plane="H" /><CustomProfileTable field={vField} plane="V" /></>));
    expect(host.textContent).toContain('Profile anchors (both planes)');
    // Change the H throat while shared: the inactive V keeps its stored throat.
    const toggle = () => host.querySelector<HTMLInputElement>('.profile-separate-vertical input[type="checkbox"]')!;
    commitDraft('H throat radius', '15');
    await accept();
    expect(useDesignStore.getState().design.profile_v!.points[0].r).toBe(12.7);
    act(() => toggle().click());
    expect(host.textContent).toContain('Use H throat for V first');
    act(() => button('Use H throat for V').click());
    await accept();
    expect(useDesignStore.getState().design.profile_v!.points[0].r).toBe(15);
    expect(useDesignStore.getState().design.shared_profile).toBe(true);
    act(() => toggle().click());
    await accept();
    expect(useDesignStore.getState().design.shared_profile).toBe(false);
    expect(useDesignStore.getState().design.profile_v).toEqual(useDesignStore.getState().design.profile_h);
    act(() => toggle().click());
    await accept();
    expect(useDesignStore.getState().design.shared_profile).toBe(true);
    expect(useDesignStore.getState().design.profile_v!.points[0].r).toBe(15);
    // Each logical operation was one Undo step.
    expect(useDesignStore.temporal.getState().pastStates).toHaveLength(4);
  });

  it('asks whether to restore a different stored V or start it from H, never overwriting it silently', async () => {
    stubProfileServer();
    const design = { ...designForFamily('FREEFORM'), shared_profile: true };
    design.profile_v!.points = [{ t: 0, r: 12.7 }, { t: .5, r: 33 }, { t: 1, r: 90 }];
    load(design);
    act(() => root.render(<CustomProfileTable field={hField} plane="H" />));
    act(() => host.querySelector<HTMLInputElement>('.profile-separate-vertical input[type="checkbox"]')!.click());
    expect(button('Restore stored vertical profile')).toBeTruthy();
    act(() => button('Restore stored vertical profile').click());
    await vi.waitFor(async () => { await settle(); expect(host.textContent).toContain('Restores the stored vertical profile.'); });
    act(() => button('Cancel').click());
    expect(useDesignStore.getState().design.shared_profile).toBe(true);
    act(() => host.querySelector<HTMLInputElement>('.profile-separate-vertical input[type="checkbox"]')!.click());
    act(() => button('Start from horizontal').click());
    await accept();
    expect(useDesignStore.getState().design.shared_profile).toBe(false);
    expect(useDesignStore.getState().design.profile_v).toEqual(design.profile_h);
  });
});
