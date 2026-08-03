import { Box3, Vector3 } from 'three';
import { describe, expect, it, vi } from 'vitest';
import { cameraFitKey, canRenderWebGL, installContextLossFallback } from './ViewportCanvas';

describe('viewport renderer guards', () => {
  it('probes an actual WebGL2 context rather than constructor globals', () => {
    const supported = { getContext: vi.fn(() => ({})) } as unknown as HTMLCanvasElement;
    const unsupported = { getContext: vi.fn(() => null) } as unknown as HTMLCanvasElement;
    expect(canRenderWebGL(() => supported)).toBe(true);
    expect(canRenderWebGL(() => unsupported)).toBe(false);
    expect(supported.getContext).toHaveBeenCalledWith('webgl2', expect.any(Object));
  });

  it('routes WebGL context loss to the renderer fallback', () => {
    const canvas = document.createElement('canvas');
    const failure = vi.fn();
    installContextLossFallback(canvas, failure);
    const event = new Event('webglcontextlost', { cancelable: true });
    canvas.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    expect(failure).toHaveBeenCalledWith('WebGL2 context was lost');
  });

  it('changes the camera fit key when bounds change without a nonce change', () => {
    const first = new Box3(new Vector3(0, 0, 0), new Vector3(1, 1, 1));
    const second = new Box3(new Vector3(10, 0, 0), new Vector3(12, 2, 2));
    expect(cameraFitKey(first, 3)).not.toBe(cameraFitKey(second, 3));
    expect(cameraFitKey(first, 3)).not.toBe(cameraFitKey(first, 4));
  });
});
