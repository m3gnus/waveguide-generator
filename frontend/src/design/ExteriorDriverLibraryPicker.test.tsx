import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ExteriorDriverLibraryPicker } from './DriverPicker';
import { ExteriorDriverFields, ExteriorDriverOptions } from './ExteriorDriverOptions';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { useDriverLibraryStore } from '../stores/driverLibrary';
import { type ExteriorTransducerForm } from '../stores/exteriorTransducer';

vi.mock('../api/drivers', () => ({
  searchDrivers: vi.fn(async () => ({ items: [], hiddenIncomplete: 0, matchesByKind: {} })),
  getDriver: vi.fn(),
  getDriverLibrary: vi.fn(),
}));

let host: HTMLDivElement;
let root: Root;
const base: ExteriorTransducerForm = { version: 1, motion_axis: [0, 0, 1] };
function Editor() {
  const channel = useCadReturnStore(store => store.driveChannels[0]);
  const form = channel.exterior_transducer!;
  const onChange = (next: ExteriorTransducerForm) => useCadReturnStore.getState().setExteriorTransducer(channel.id, next);
  return <><ExteriorDriverLibraryPicker channel={channel}/>
    <ExteriorDriverFields channelId={channel.id} form={form} onChange={onChange}/>
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
  act(() => row!.click());
}
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  resetCadReturnStore();
  useCadReturnStore.setState({ driveChannels: [{ id: 'driver', source_ids: ['front'], motion: 'axial', exterior_transducer: base }] });
  useDriverLibraryStore.setState({ status: 'ready', info: null, saved: [{
    id: 'saved', label: 'Library driver', based_on: 'database', base: { re_ohm: 4 }, overrides: {}, kind: 'lf', z_ohm: null, xo_min_hz: null,
  }] });
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  act(() => root.render(<Editor/>));
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.clearAllMocks(); });

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
