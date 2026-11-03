import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ExteriorDriverLibraryPicker } from './DriverPicker';
import { ExteriorDriverFields, ExteriorDriverOptions } from './ExteriorDriverOptions';
import { ExteriorMmdSuggestion } from './ExteriorMmdSuggestion';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { useDriverLibraryStore } from '../stores/driverLibrary';
import { type ExteriorTransducerForm } from '../stores/exteriorTransducer';
import { suggestMmd } from '../api/drivers';
import { workspaceModeStore } from '../stores/workspaceMode';

vi.mock('../api/drivers', () => ({
  searchDrivers: vi.fn(async () => ({ items: [], hiddenIncomplete: 0, matchesByKind: {} })),
  getDriver: vi.fn(),
  getDriverLibrary: vi.fn(),
  suggestMmd: vi.fn(),
}));

let host: HTMLDivElement;
let root: Root;
const base: ExteriorTransducerForm = { version: 1, motion_axis: [0, 0, 1] };
function Editor() {
  const channel = useCadReturnStore(store => store.driveChannels[0]);
  const form = channel.exterior_transducer!;
  const onChange = (next: ExteriorTransducerForm) => useCadReturnStore.getState().setExteriorTransducer(channel.id, next);
  return <><ExteriorDriverLibraryPicker channel={channel}/>
    <ExteriorDriverFields channelId={channel.id} form={form} onChange={onChange}
      mmdNote={<ExteriorMmdSuggestion channelId={channel.id}/>}/>
    <ExteriorDriverOptions channelId={channel.id} form={form} onChange={onChange}/></>;
}
function input(label: string) {
  return host.querySelector<HTMLInputElement>(`[aria-label="${label} for driver"]`)!;
}
function enter(label: string, value: string) {
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input(label), value);
    input(label).dispatchEvent(new Event('input', { bubbles: true }));
  });
}
async function pick(label: string) {
  await act(async () => {
    const search = host.querySelector<HTMLInputElement>('[role="combobox"]')!;
    search.blur(); search.focus();
  });
  const row = [...host.querySelectorAll<HTMLButtonElement>('[role="option"]')]
    .find(option => option.textContent?.includes(label));
  expect(row).toBeDefined();
  await act(async () => row!.click());
}
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  resetCadReturnStore();
  workspaceModeStore.setImportedInput('cad');
  useCadReturnStore.setState({ driveChannels: [{ id: 'driver', source_ids: ['front'], motion: 'axial', exterior_transducer: base }] });
  useDriverLibraryStore.setState({ status: 'ready', info: null, saved: [{
    id: 'saved', label: 'Library driver', based_on: 'database', base: { re_ohm: 4 }, overrides: {}, kind: 'lf', z_ohm: null, xo_min_hz: null,
  }] });
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  act(() => root.render(<Editor/>));
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.resetAllMocks(); });

it('preserves every typed field and shows no library note after choosing hand entry', async () => {
  for (const [label, value] of [['Resistance', '6'], ['Inductance', '.1'], ['Force factor', '7'],
    ['Bare moving mass (Mmd)', '.02'], ['Compliance', '.001'], ['Mechanical resistance', '1'], ['Sd', '200']]) enter(label, value);
  const typed = useCadReturnStore.getState().driveChannels[0].exterior_transducer;
  await pick('Enter T/S manually');
  expect(useCadReturnStore.getState().driveChannels[0].exterior_transducer).toEqual(typed);
  expect(host.querySelector('[role="status"]')).toBeNull();
});

it('derives the Sd note from the store and distinguishes chamber requirement from a hint', async () => {
  await pick('Library driver');
  expect(host.textContent).toContain('You can enter Sd from the datasheet for acoustic impedance');
  act(() => input('Lumped sealed rear chamber').click());
  expect(host.textContent).toContain('Enter Sd in the driver options to use a lumped rear chamber');
  enter('Sd', '200');
  expect(host.querySelector('[role="status"]')).toBeNull();
  enter('Sd', '');
  expect(host.querySelector('[role="status"]')).not.toBeNull();
  await pick('Enter T/S manually');
  expect(host.querySelector('[role="status"]')).toBeNull();
});

it.each([true, false])('clears semi-inductance and its editor cache on replacement (enabled=%s)', async enabled => {
  act(() => input('Semi-inductance').click());
  for (const [label, value] of [['Re′', '5'], ['Leb', '.1'], ['Le', '2'], ['Ke', '30'], ['Rss', '20']]) enter(label, value);
  if (!enabled) act(() => input('Semi-inductance').click());
  act(() => input('Lumped sealed rear chamber').click());
  enter('Rear chamber volume', '10');
  await pick('Library driver');
  const form = useCadReturnStore.getState().driveChannels[0].exterior_transducer!;
  expect(form).not.toHaveProperty('semi_inductance');
  expect(form.motion_axis).toEqual([0, 0, 1]);
  expect(form.lumped_sealed_rear_chamber).toEqual({ enabled: true, volume_m3: .01 });
  expect(input('Semi-inductance').checked).toBe(false);
  act(() => input('Semi-inductance').click());
  expect(input('Re′').value).toBe('');
  expect(input('Leb').value).toBe('');
});

it('does not claim a library Sd is absent when its populated value is later cleared', async () => {
  act(() => useDriverLibraryStore.setState({ saved: [{ ...useDriverLibraryStore.getState().saved[0],
    base: { re_ohm: 4, sd_cm2: 200 } }] }));
  await pick('Library driver');
  expect(input('Sd').value).toBe('200');
  enter('Sd', '');
  expect(host.querySelector('[role="status"]')).toBeNull();
});

it('retains disabled semi-inductance values while hand-editing ordinary fields', () => {
  act(() => input('Semi-inductance').click());
  enter('Re′', '5');
  act(() => input('Semi-inductance').click());
  enter('Resistance', '6');
  enter('Sd', '200');
  act(() => input('Semi-inductance').click());
  expect(input('Re′').value).toBe('5');
});

const derived = { method: 'mms_free_air' as const, mms_g: 17.9, sd_cm2: 124.7, mmd_kg: .0162940347065,
  correction_kg: .0016059652935, correction_fraction: .08971873148, rho_kg_m3: 1.2041 };
const buttons = () => [...host.querySelectorAll<HTMLButtonElement>('button')];

it.each(['library', 'manual'])('offers %s Mms explicitly, accepts once, edits normally and undoes acceptance', async source => {
  vi.mocked(suggestMmd).mockResolvedValue({ suggestion: derived });
  if (source === 'library') {
    act(() => useDriverLibraryStore.setState({ saved: [{ ...useDriverLibraryStore.getState().saved[0],
      base: { re_ohm: 6.4, sd_cm2: 124.7, mms_g: 17.9 } }] }));
    await pick('Library driver');
  } else {
    enter('Mms', '17.9'); enter('Sd', '124.7');
  }
  await act(async () => {});
  expect(suggestMmd).toHaveBeenLastCalledWith(17.9, 124.7);
  expect(input('Bare moving mass (Mmd)').value).toBe('');
  expect(host.textContent).toContain('Suggested Mmd 16.29 g');
  expect(host.textContent).toContain('Mms minus 1.61 g free-air load (9.0% of Mms).');
  // The suggestion renders in the T/S grid directly after the Mmd field it fills.
  expect(input('Bare moving mass (Mmd)').closest('label')!.nextElementSibling!.classList.contains('mmd-suggest')).toBe(true);
  const accept = buttons().find(button => button.textContent === 'Use suggested Mmd')!;
  act(() => accept.click());
  expect(input('Bare moving mass (Mmd)').valueAsNumber).toBe(derived.mmd_kg);
  expect(useCadReturnStore.getState().driveChannels[0].exterior_driver_inputs?.derived_mmd).toEqual(derived);
  expect(buttons().find(button => button.textContent === 'Use suggested Mmd')).toBeUndefined();
  act(() => buttons().find(button => button.textContent === 'Undo Mmd suggestion')!.click());
  expect(input('Bare moving mass (Mmd)').value).toBe('');
  expect(useCadReturnStore.getState().driveChannels[0].exterior_driver_inputs?.derived_mmd).toBeUndefined();
  await act(async () => {});
  act(() => buttons().find(button => button.textContent === 'Use suggested Mmd')!.click());
  enter('Bare moving mass (Mmd)', '.02');
  expect(input('Bare moving mass (Mmd)').valueAsNumber).toBe(0.02);
  expect(host.textContent).toContain('Mmd edited by hand. The suggestion was 16.29 g, from Mms 17.9 g and Sd 124.7 cm².');
});

it('withholds a refused correction and displays its reason', async () => {
  vi.mocked(suggestMmd).mockResolvedValue({ suggestion: null, reason: 'Correction exceeds 50% of Mms; check Sd/Mms' });
  enter('Mms', '55'); enter('Sd', '850');
  await act(async () => {});
  expect(host.textContent).toContain('No Mmd suggestion. Correction exceeds 50% of Mms; check Sd/Mms. Enter Mmd from the datasheet.');
  expect(buttons().find(button => button.textContent === 'Use suggested Mmd')).toBeUndefined();
  expect(input('Bare moving mass (Mmd)').value).toBe('');
});

it('keeps a manufacturer Mmd and never requests a correction for it', async () => {
  act(() => useDriverLibraryStore.setState({ saved: [{ ...useDriverLibraryStore.getState().saved[0],
    base: { mmd_g: 16, mms_g: 17.9, sd_cm2: 124.7 } }] }));
  await pick('Library driver');
  expect(input('Bare moving mass (Mmd)').value).toBe('0.016');
  expect(suggestMmd).not.toHaveBeenCalled();
});

it('ignores late responses when a driver or its inputs change', async () => {
  let finish!: (value: { suggestion: typeof derived }) => void;
  vi.mocked(suggestMmd).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }))
    .mockResolvedValue({ suggestion: null, reason: 'Refused new inputs' });
  enter('Mms', '17.9'); enter('Sd', '124.7');
  enter('Mms', '1');
  await act(async () => { finish({ suggestion: derived }); });
  expect(host.textContent).toContain('Refused new inputs');
  expect(buttons().find(button => button.textContent === 'Use suggested Mmd')).toBeUndefined();
});
