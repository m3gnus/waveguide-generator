import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vitest';
import InteractiveBalloon, { buildBalloonGeometry } from './Balloon3D';
import { BalloonRenderer, balloonMissingReason, closestFrequencyIndex, ForwardBeamRenderer, hasBalloonData, sampleBalloonGrid } from './balloon';
import type { ResultPayload } from './types';

const fiberMocks = vi.hoisted(() => ({ canvas: vi.fn() }));
vi.mock('@react-three/fiber', () => ({
  Canvas: (props: unknown) => { fiberMocks.canvas(props); return <div data-testid="three-canvas"/>; },
  useThree: () => ({ camera: { position: { set: vi.fn() }, up: { set: vi.fn() }, lookAt: vi.fn(), updateProjectionMatrix: vi.fn() } }),
}));
vi.mock('@react-three/drei', () => ({ OrbitControls: () => null }));

function createImageData(width: number, height: number) {
  return { width, height, data: new Uint8ClampedArray(width * height * 4) };
}

describe('spherical result renderers', () => {
  it('retains the 3D drawing buffer for Copy PNG and Download PNG', () => {
    const result: ResultPayload = { frequencies: [1_000], balloon: { frequencies: [1_000], theta_deg: [0, 90], phi_deg: [0, 120, 240], spl_norm_db: [[[0, 0, 0], [-10, -12, -14]]] } };
    const host = document.createElement('div'); document.body.append(host);
    const root = createRoot(host);
    fiberMocks.canvas.mockClear();
    act(() => root.render(<InteractiveBalloon result={result} frequencyIndex={0}/>));
    expect(fiberMocks.canvas).toHaveBeenCalledWith(expect.objectContaining({
      gl: expect.objectContaining({ preserveDrawingBuffer: true }),
    }));
    act(() => root.unmount()); host.remove();
  });

  it('interpolates the wrapped regular balloon grid', () => {
    const theta = [0, 90];
    const phi = [0, 90, 180, 270];
    const grid = [[0, 0, 0, 0], [-10, -20, -30, -40]];
    expect(sampleBalloonGrid(theta, phi, grid, 45, 45)).toBeCloseTo(-7.5);
    expect(sampleBalloonGrid(theta, phi, grid, 90, 315)).toBeCloseTo(-25);
  });
  it('validates availability, starts nearest 1 kHz, and explains missing backend data', () => {
    const result: ResultPayload = { frequencies: [], metadata: { balloon_sampling: { status: 'backend_unsupported' } } };
    expect(hasBalloonData(result)).toBe(false);
    expect(closestFrequencyIndex([250, 800, 1600])).toBe(1);
    expect(balloonMissingReason(result, '3D Balloon')).toContain('this engine');
  });
  it('builds a closed coloured 3D surface with a repeated azimuth seam', () => {
    const geometry = buildBalloonGeometry([0, 90, 180], [0, 120, 240], [[0, 0, 0], [-6, -12, -18], [-30, -30, -30]]);
    expect(geometry.getAttribute('position').count).toBe(12);
    expect(geometry.getAttribute('normal').count).toBe(12);
    expect(geometry.getAttribute('color').count).toBe(12);
    expect(geometry.getIndex()?.count).toBe(36);
    const positions = geometry.getAttribute('position');
    expect(positions.getX(4)).toBeCloseTo(positions.getX(7));
    expect(positions.getY(4)).toBeCloseTo(positions.getY(7));
    geometry.dispose();
  });
  it('renders independent frequency sliders for Balloon and Forward Beam cards', () => {
    const result: ResultPayload = { frequencies: [500, 1_000], balloon: { frequencies: [500, 1_000], theta_deg: [0, 90], phi_deg: [0, 120, 240], spl_norm_db: [[[0, 0, 0], [-10, -12, -14]], [[0, 0, 0], [-20, -22, -24]]] } };
    const host = document.createElement('div'); document.body.append(host);
    const root = createRoot(host);
    const context = { scale: vi.fn(), clearRect: vi.fn(), fillRect: vi.fn(), beginPath: vi.fn(), arc: vi.fn(), fill: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(), createImageData, putImageData: vi.fn(), drawImage: vi.fn() };
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D);
    act(() => root.render(<><BalloonRenderer result={result}/><ForwardBeamRenderer result={result}/></>));
    expect(host.querySelector('[aria-label="Balloon frequency"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="Forward beam frequency"]')).not.toBeNull();
    act(() => root.unmount()); getContext.mockRestore(); host.remove();
  });
  it('paints the Forward Beam Map per pixel on the continuous ramp, not in ten flat bands', () => {
    // Level falls linearly off axis, so a banded map would show at most ten colours.
    const theta = [0, 30, 60, 90];
    const result: ResultPayload = { frequencies: [1_000], balloon: { frequencies: [1_000], theta_deg: theta, phi_deg: [0, 120, 240], spl_norm_db: [theta.map((angle) => [-angle / 3, -angle / 3, -angle / 3])] } };
    const host = document.createElement('div'); document.body.append(host);
    const root = createRoot(host);
    const putImageData = vi.fn();
    const context = { scale: vi.fn(), beginPath: vi.fn(), arc: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(), createImageData, putImageData, drawImage: vi.fn() };
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D);
    act(() => root.render(<ForwardBeamRenderer result={result}/>));
    const image = putImageData.mock.calls[0][0] as ImageData;
    const colours = new Set<string>();
    for (let offset = 0; offset < image.data.length; offset += 4) if (image.data[offset + 3] === 255) colours.add(image.data.slice(offset, offset + 3).join());
    expect(colours.size).toBeGreaterThan(40);
    // The disc is round: its corners stay transparent.
    expect(image.data[3]).toBe(0);
    act(() => root.unmount()); getContext.mockRestore(); host.remove();
  });
});
