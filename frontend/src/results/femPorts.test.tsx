import { act } from 'react';
import { readFileSync } from 'node:fs';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vitest';
import { FemPortsPanel, portChartOption } from './FemPortsPanel';
import { femPortsOf, pressureAt, portRadiationTraces, portVelocityTraces, type FemPorts } from './femPorts';
import type { ResultPayload } from './types';
import { readChartTokens } from './EChart';

vi.mock('./EChart', async (original) => ({ ...await original<typeof import('./EChart')>(),
  EChart: ({ label }: { label: string }) => <div aria-label={label}/> }));

const data: FemPorts = {
  frequencies: [100, 200], drive_normalization: 'unit_acceleration', reference_voltage_rms_v: null,
  phase_convention: 'engineering_exp_plus_jwt', observation_angles_deg: [0, 45],
  interfaces: ['front', 'rear'].map((label, i) => ({
    id: `interface:${label}`, label, represented_area_m2: .01,
    average_normal_velocity: [{ real: 1 + i, imaginary: -2 }, { real: null, imaginary: null }],
    radiated_pressure: { horizontal: [[{ real: 1 + i, imaginary: -1 }, { real: 3, imaginary: 4 }],
      [{ real: 0, imaginary: 0 }, { real: null, imaginary: null }]],
    vertical: [[{ real: 4, imaginary: 0 }, { real: 5, imaginary: 0 }]] },
  })),
  other_exterior_radiated_pressure: { horizontal: [[{ real: 2, imaginary: 0 }]] },
  total_radiated_pressure: { horizontal: [[{ real: 5, imaginary: -2 }]], vertical: [] },
};
const result = { frequencies: data.frequencies, metadata: { fem_ports: data } } as ResultPayload;

describe('FEM port traces', () => {
  it('labels each interface and retains real, imaginary and missing velocity samples', () => {
    expect(portVelocityTraces(data)).toEqual([
      { name: 'front · Re', values: [1, null] }, { name: 'front · Im', values: [-2, null] },
      { name: 'rear · Re', values: [2, null] }, { name: 'rear · Im', values: [-2, null] },
    ]);
  });
  it('reads the selected observation, with port, other exterior sources and total radiation', () => {
    expect(portRadiationTraces(data, 'horizontal', 0, 'imaginary').map((s) => s.values[0])).toEqual([-1, -1, 0, -2]);
    const offAxis = portRadiationTraces(data, 'horizontal', 1, 'spl');
    expect(offAxis[0].values[0]).toBeCloseTo(20 * Math.log10(5 / 20e-6));
    expect(portRadiationTraces(data, 'horizontal', 0, 'phase')[0].values).toEqual([-45, null]);
    expect(portRadiationTraces(data, 'vertical', 0, 'real')[0].values).toEqual([4, null]);
    expect(offAxis[0].values[1]).toBeNull();
  });
  it('keeps old and interior-only results unchanged', () => {
    expect(femPortsOf({ metadata: {} } as ResultPayload)).toBeNull();
    const host = document.createElement('div');
    const root = createRoot(host);
    act(() => root.render(<FemPortsPanel result={{ metadata: {} } as ResultPayload}/>));
    expect(host.textContent).toBe('');
    act(() => root.unmount());
  });
  it('uses frequency-valued log axes and gaps in the existing chart renderer', () => {
    const option = portChartOption(data.frequencies, portVelocityTraces(data), 'm/s', readChartTokens());
    expect(option.xAxis).toMatchObject({ type: 'log' });
    expect(option.series).toMatchObject([{ data: [[100, 1], [200, null]], connectNulls: false }, {}, {}, {}]);
  });
  it('opens the port panels and changes observation and display controls', () => {
    const host = document.createElement('div');
    document.body.append(host);
    const root = createRoot(host);
    act(() => root.render(<FemPortsPanel result={result}/>));
    expect(host.textContent).toContain('Ports · 2 interfaces');
    const details = host.querySelector('details')!;
    act(() => { details.open = true; details.dispatchEvent(new Event('toggle')); });
    expect(host.querySelector('[aria-label="Port radiation by frequency"]')).not.toBeNull();
    expect(host.textContent).toContain('1 m/s² drive');
    expect(host.querySelector('[aria-label="Port radiation"] .result-title em')?.textContent).toBe('dB');
    expect(details.classList.contains('result-fem-ports')).toBe(true);
    for (const [label, value] of [['Port radiation plane', 'vertical'], ['Port radiation angle', '1'], ['Port radiation display', 'real'], ['Port radiation display', 'phase']]) {
      const control = host.querySelector<HTMLSelectElement>(`[aria-label="${label}"]`)!;
      act(() => { control.value = value; control.dispatchEvent(new Event('change', { bubbles: true })); });
      expect(control.value).toBe(value);
      if (value === 'real') expect(host.querySelector('[aria-label="Port radiation"] .result-title em')?.textContent).toBe('Pa');
    }
    expect(host.querySelector('[aria-label="Port radiation"] .result-title em')?.textContent).toBe('°');
    act(() => root.unmount());
    host.remove();
  });
});


it('omits other exterior sources when the engine supplies no column', () => {
  const ports = { ...data, other_exterior_radiated_pressure: undefined };
  expect(portRadiationTraces(ports, 'horizontal', 0, 'real').map((t) => t.name)).toEqual(['front', 'rear', 'Total']);
  expect(portRadiationTraces(data, 'horizontal', 0, 'real').map((t) => t.name)).toContain('Other exterior sources');
});

it('decodes lossless packed pressure in frequency and observation order with gaps', () => {
  const bytes = new Uint8Array(4 * 16);
  const view = new DataView(bytes.buffer);
  [1, -2, 3, -4, 5, -6, NaN, NaN].forEach((v, i) => view.setFloat64(i * 8, v, true));
  const matrix = { encoding: 'base64' as const, dtype: 'complex128' as const, byte_order: 'little' as const, order: 'C' as const, shape: [2, 2] as [number, number],
    content_base64: btoa(String.fromCharCode(...bytes)) };
  const ports = { ...data, total_radiated_pressure: { horizontal: matrix } };
  expect(portRadiationTraces(ports, 'horizontal', 0, 'real').at(-1)?.values).toEqual([1, 5]);
  expect(portRadiationTraces(ports, 'horizontal', 1, 'imaginary').at(-1)?.values).toEqual([-4, null]);
  expect(portRadiationTraces(ports, 'horizontal', 2, 'real').at(-1)?.values).toEqual([null, null]);
});

it('disables observation controls when there are no polar planes', () => {
  const host = document.createElement('div');
  const root = createRoot(host);
  act(() => root.render(<FemPortsPanel result={{ ...result, metadata: { fem_ports: { ...data, total_radiated_pressure: {} } } }}/>));
  const details = host.querySelector('details')!;
  act(() => { details.open = true; details.dispatchEvent(new Event('toggle')); });
  expect(host.querySelector<HTMLSelectElement>('[aria-label="Port radiation plane"]')?.disabled).toBe(true);
  expect(host.querySelector<HTMLSelectElement>('[aria-label="Port radiation angle"]')?.disabled).toBe(true);
  act(() => root.unmount());
});

it('explains port availability on a combined channel', () => {
  const host = document.createElement('div');
  const root = createRoot(host);
  const combined = { frequencies: data.frequencies, metadata: { derived_from_channels: ['left', 'right'],
    fem: { interface_diagnostics: { interfaces: [{ id: 'interface:front' }] } } } } as ResultPayload;
  act(() => root.render(<FemPortsPanel result={combined}/>));
  expect(host.textContent).toContain('Ports are available per drive channel.');
  expect(host.querySelector('details')).toBeNull();
  act(() => root.unmount());
});

it('bounds the port disclosure in short flex docks while allowing its own scroll', () => {
  const css = new TextDecoder().decode(readFileSync('src/styles/app.css'));
  const rule = css.match(/\.result-fem-ports \{([^}]+)\}/)?.[1];
  expect(rule).toContain('flex: none');
  expect(rule).toContain('max-height: min(300px, 45%)');
  expect(rule).toContain('overflow: auto');
});


it.each([
  { encoding: 'hex' }, { dtype: 'complex64' }, { byte_order: 'big' }, { byte_order: undefined },
  { order: 'F' }, { shape: [2, 3] }, { shape: [-1, 2] }, { shape: [2, 2.5] },
  { content_base64: 'invalid!' }, { content_base64: btoa('short') },
  { content_base64: btoa('x'.repeat(80)) },
])('rejects the whole port block and renders a note for malformed packing %j', mutation => {
  const packed = { encoding: 'base64', dtype: 'complex128', byte_order: 'little', order: 'C',
    shape: [2, 2], content_base64: btoa('x'.repeat(64)), ...mutation };
  for (const location of ['total', 'port', 'other']) {
    const ports = structuredClone(data);
    if (location === 'total') ports.total_radiated_pressure.horizontal = packed as never;
    if (location === 'port') ports.interfaces[0].radiated_pressure.horizontal = packed as never;
    if (location === 'other') ports.other_exterior_radiated_pressure!.horizontal = packed as never;
    const invalid = { ...result, metadata: { fem_ports: ports } };
    expect(femPortsOf(invalid)).toBeNull();
    const host = document.createElement('div');
    const root = createRoot(host);
    act(() => root.render(<FemPortsPanel result={invalid}/>));
    expect(host.textContent).toBe('Port data is invalid.');
    expect(host.querySelector('details')).toBeNull();
    act(() => root.unmount());
  }
});

it('guards negative and fractional frequency indices for packed pressure', () => {
  const packed = { encoding: 'base64' as const, dtype: 'complex128' as const,
    byte_order: 'little' as const, order: 'C' as const, shape: [2, 2] as [number, number],
    content_base64: btoa('x'.repeat(64)) };
  expect(pressureAt(packed, -1, 0)).toBeUndefined();
  expect(pressureAt(packed, .5, 0)).toBeUndefined();
  expect(pressureAt(packed, 2, 0)).toBeUndefined();
  expect(femPortsOf({ ...result, metadata: { fem_ports: { ...data,
    total_radiated_pressure: { horizontal: packed } } } })).not.toBeNull();
});
