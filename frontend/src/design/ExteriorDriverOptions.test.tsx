import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { ExteriorDriverOptions } from './ExteriorDriverOptions';
import { exteriorTransducerError, type ExteriorTransducerForm } from '../stores/exteriorTransducer';

let host: HTMLDivElement;
let root: Root;
let form: ExteriorTransducerForm;
function render() {
  root.render(<ExteriorDriverOptions channelId="driver" form={form} onChange={next => { form = next; render(); }}/>);
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
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  form = { version: 1, motion_axis: [0, 0, 1], re_ohm: 6, le_h: .0005,
    bl_n_per_a: 7, mmd_kg: .02, cms_m_per_n: .001, rms_n_s_per_m: 1.5 };
  act(render);
});
afterEach(() => { act(() => root.unmount()); host.remove(); });

it('edits semi-inductance in engine SI units and retains values when disabled', () => {
  expect(form).not.toHaveProperty('semi_inductance');
  act(() => input('Semi-inductance').click());
  expect(form.version).toBe(2);
  expect(exteriorTransducerError(form)).toContain('Re′');
  for (const [label, value] of [['Re′', '5.5'], ['Leb', '.0001'], ['Le', '.002'], ['Ke', '.03'], ['Rss', '20']]) enter(label, value);
  expect(form.semi_inductance).toEqual({ enabled: true, re_prime_ohm: 5.5, leb_h: .0001, le_h: .002, ke_semi_h: .03, rss_ohm: 20 });
  expect(exteriorTransducerError(form)).toBeNull();
  expect(host.textContent).toContain('H/√s');
  act(() => input('Semi-inductance').click());
  expect(form.semi_inductance?.enabled).toBe(false);
  expect(form.semi_inductance?.re_prime_ohm).toBe(5.5);
  expect(input('Re′')).toBeNull();
  act(() => input('Semi-inductance').click());
  expect(input('Re′').value).toBe('5.5');
});

it('edits and validates the sealed volume and explains mesh-derived area', () => {
  act(() => input('Lumped sealed rear chamber').click());
  expect(exteriorTransducerError(form)).toContain('Rear chamber volume');
  expect(host.textContent).toContain('10%');
  expect(host.textContent).toContain('unmeshed rear chamber');
  enter('Rear chamber volume', '-1');
  expect(exteriorTransducerError(form)).not.toBeNull();
  enter('Rear chamber volume', '.01');
  expect(form.lumped_sealed_rear_chamber).toEqual({ enabled: true, volume_m3: .01 });
  expect(exteriorTransducerError(form)).toBeNull();
  enter('Rear chamber volume', '');
  expect(exteriorTransducerError(form)).not.toBeNull();
  act(() => input('Lumped sealed rear chamber').click());
  expect(exteriorTransducerError(form)).toBeNull();
});
