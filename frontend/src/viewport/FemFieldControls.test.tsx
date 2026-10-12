import { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vitest';
import { FemFieldControls } from './FemFieldControls';
import type { FemDisplayMode, FemFieldRendering } from './FemFieldView';
import type { FieldPlaneValueWindow } from './fieldPlaneColor';
import type { FemPlaneScope, FemPlaneQuantity } from '../api/femField';
import { decodeFemFieldPayload } from './femField';
import { femFieldFixture } from './test-fixtures/femField';
import { useFieldPlaneStore } from './fieldPlaneStore';

function Harness({ coupled = true }: { coupled?: boolean }) {
  const [scope, setScope] = useState<FemPlaneScope>('interior');
  const [quantity, setQuantity] = useState<FemPlaneQuantity>('pressure');
  const [mode, setMode] = useState<FemDisplayMode>('spl');
  const [rendering, setRendering] = useState<FemFieldRendering>('smooth');
  const [window, setWindow] = useState<FieldPlaneValueWindow | null>(null);
  const payload = femFieldFixture();
  if (!coupled) payload.plane_unavailable_reason = 'Exterior and combined planes unavailable: BEM traces exceed the retention limit.';
  payload.plane_scopes = coupled ? ['interior', 'exterior', 'combined'] : ['interior'];
  const field = { ...decodeFemFieldPayload(payload), payload };
  return <FemFieldControls field={{ field, error: null, loading: false, frequencies: [100], channels: ['front'],
    frequencyIndex: 0, channel: 'front', setFrequencyIndex: vi.fn(), setChannel: vi.fn(), retry: vi.fn() }}
    planeResult={{ field: null, error: null, loading: false, retry: vi.fn() }}
    scope={scope} setScope={setScope} quantity={quantity} setQuantity={setQuantity}
    displayMode={mode} setDisplayMode={setMode} manualWindow={window} setManualWindow={setWindow}
    rendering={rendering} setRendering={setRendering} sequential={['#000000', '#ffffff']}/>;
}

function mounted(coupled = true) {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.createElement('div'); const root = createRoot(host);
  act(() => root.render(<Harness coupled={coupled}/>));
  const select = (label: string, value: string) => act(() => {
    const input = host.querySelector<HTMLSelectElement>(`[aria-label="${label}"]`)!;
    input.value = value; input.dispatchEvent(new Event('change', { bubbles: true }));
  });
  return { host, select, cleanup: () => act(() => root.unmount()) };
}

describe('coupled FEM controls', () => {
  it('selects exterior and combined planes, velocity, all displays and manual limits', () => {
    const { host, select, cleanup } = mounted();
    try {
      select('FEM plane region', 'exterior'); expect(host.textContent).toContain('Coupled exterior field');
      select('FEM plane region', 'combined'); expect(host.textContent).toContain('Combined acoustic field');
      for (const mode of ['spl', 'phase', 'normalized', 'real', 'imaginary']) {
        select('FEM field display mode', mode);
        expect(host.querySelector<HTMLSelectElement>('[aria-label="FEM field display mode"]')!.value).toBe(mode);
      }
      const checkbox = host.querySelector<HTMLInputElement>('[aria-label="FEM manual colour limits"]')!;
      act(() => checkbox.click());
      expect(host.querySelector('[aria-label="FEM colour minimum"]')).not.toBeNull();
      expect(host.querySelector('[aria-label="FEM colour maximum"]')).not.toBeNull();
      act(() => checkbox.click()); expect(host.querySelector('[aria-label="FEM colour minimum"]')).toBeNull();
      select('FEM field quantity', 'velocity_normal'); expect(host.textContent).toContain('Velocity per unit acceleration ((m/s) per m/s²)');
      expect(host.textContent).not.toContain('Pressure per unit acceleration');
      expect(host.querySelector<HTMLOptionElement>('[aria-label="FEM field display mode"] option[value="spl"]')!.disabled).toBe(true);
    } finally { cleanup(); }
  });
  it('keeps old interior sidecars available while disabling unretained exterior scopes', () => {
    const { host, cleanup } = mounted(false);
    try {
      expect(host.textContent).toContain('BEM traces exceed the retention limit');
      expect(host.querySelector<HTMLOptionElement>('[aria-label="FEM plane region"] option[value="exterior"]')!.disabled).toBe(true);
      expect(host.querySelector<HTMLOptionElement>('[aria-label="FEM plane region"] option[value="interior"]')!.disabled).toBe(false);
    } finally { cleanup(); }
  });
  it('phase animation switches a real display to instantaneous', () => {
    useFieldPlaneStore.getState().setAnimating(false);
    const { host, select, cleanup } = mounted();
    try {
      select('FEM field display mode', 'real');
      act(() => host.querySelector<HTMLButtonElement>('[aria-label="Play FEM phase animation"]')!.click());
      expect(host.querySelector<HTMLSelectElement>('[aria-label="FEM field display mode"]')!.value).toBe('instantaneous');
    } finally { cleanup(); useFieldPlaneStore.getState().setAnimating(false); }
  });
});
