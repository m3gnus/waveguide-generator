import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { ExteriorDriverFields, ExteriorDriverOptions } from './ExteriorDriverOptions';
import { exteriorTransducerError, type ExteriorTransducerForm } from '../stores/exteriorTransducer';

let host: HTMLDivElement;
let root: Root;
let form: ExteriorTransducerForm;
function render() {
  root.render(<><ExteriorDriverFields channelId="driver" form={form} onChange={next => { form = next; render(); }}/><ExteriorDriverOptions channelId="driver" form={form} onChange={next => { form = next; render(); }}/></>);
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

it('edits semi-inductance in mH with SI storage and caches disabled values', () => {
  expect(form).not.toHaveProperty('semi_inductance');
  act(() => input('Semi-inductance').click());
  expect(form.version).toBe(2);
  expect(exteriorTransducerError(form)).toContain('Re′');
  for (const [label, value] of [['Re′', '5.5'], ['Leb', '.1'], ['Le', '2'], ['Ke', '30'], ['Rss', '20']]) enter(label, value);
  expect(form.semi_inductance).toEqual({ enabled: true, re_prime_ohm: 5.5, leb_h: .0001, le_h: .002, ke_semi_h: .03, rss_ohm: 20 });
  expect(exteriorTransducerError(form)).toBeNull();
  expect(host.textContent).toContain('mH/√s');
  expect(host.textContent).toContain('0.0001 H');
  expect(host.textContent).toContain('0.002 H');
  expect(host.textContent).toContain('0.03 H/√s');
  act(() => input('Semi-inductance').click());
  expect(form.version).toBe(1);
  expect(form).not.toHaveProperty('semi_inductance');
  expect(input('Re′')).toBeNull();
  act(() => input('Semi-inductance').click());
  expect(input('Re′').value).toBe('5.5');
  expect(input('Leb').value).toBe('0.1');
  expect(input('Le').value).toBe('2');
  expect(input('Ke').value).toBe('30');
});

it('edits and validates the sealed volume and explains mesh-derived area', () => {
  act(() => input('Lumped sealed rear chamber').click());
  expect(exteriorTransducerError(form)).toContain('Rear chamber volume');
  expect(host.textContent).toContain('front-only diaphragm');
  expect(host.textContent).toContain('untag exterior rear faces');
  expect(host.textContent).toContain('litres');
  expect(host.textContent).toContain('unmeshed rear chamber');
  enter('Rear chamber volume', '-1');
  expect(exteriorTransducerError(form)).not.toBeNull();
  enter('Rear chamber volume', '10');
  expect(form.lumped_sealed_rear_chamber).toEqual({ enabled: true, volume_m3: .01 });
  expect(host.textContent).toContain('0.01 m³');
  // A lumped chamber needs the driver's declared Sd.
  expect(exteriorTransducerError(form)).toContain('Enter Sd in the driver options');
  enter('Rear chamber volume', '');
  expect(exteriorTransducerError(form)).not.toBeNull();
  act(() => input('Lumped sealed rear chamber').click());
  expect(exteriorTransducerError(form)).toBeNull();
});


it('edits simple inductance in mH and echoes its SI value', () => {
  expect(input('Inductance').value).toBe('0.5');
  enter('Inductance', '1.2');
  expect(form.le_h).toBe(.0012);
  expect(host.textContent).toContain('Inductance (mH)');
  expect(host.textContent).toContain('0.0012 H');
});

it('returns to byte-identical v1 after all optional models are unchecked', () => {
  const original = JSON.stringify(form);
  act(() => input('Semi-inductance').click());
  act(() => input('Lumped sealed rear chamber').click());
  enter('Rear chamber volume', '10');
  act(() => input('Semi-inductance').click());
  expect(form.version).toBe(2);
  act(() => input('Lumped sealed rear chamber').click());
  expect(form.version).toBe(1);
  expect(JSON.stringify(form)).toBe(original);
  expect(exteriorTransducerError(form)).toBeNull();
  act(() => input('Lumped sealed rear chamber').click());
  expect(input('Rear chamber volume').value).toBe('10');
});

it('keeps v2 when ratings remain and returns to v1 when they are cleared', () => {
  enter('Rated power', '100');
  enter('Xmax', '2');
  act(() => input('Semi-inductance').click());
  act(() => input('Semi-inductance').click());
  expect(form.version).toBe(2);
  expect(form.power_w).toBe(100);
  expect(form.xmax_mm).toBe(2);
  enter('Rated power', '');
  expect(form.version).toBe(2);
  enter('Xmax', '');
  expect(form.version).toBe(1);
});

it('stores typed Sd in m², validates positivity, and returns to v1 when cleared', () => {
  const original = JSON.stringify(form);
  enter('Sd', '220');
  expect(form.sd_m2).toBe(.022);
  expect(form.version).toBe(2);
  expect(exteriorTransducerError(form)).toBeNull();
  expect(host.textContent).toContain('Declared Sd controls chamber stiffness');
  enter('Sd', '0');
  expect(exteriorTransducerError(form)).toContain('positive Sd');
  enter('Sd', '');
  expect(form.version).toBe(1);
  expect(JSON.stringify(form)).toBe(original);
});
