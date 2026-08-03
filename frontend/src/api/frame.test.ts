import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { decodeFrame } from './frame';

describe('frame wrapper', () => {
  it('decodes a real shared multi-surface fixture into zero-copy typed arrays', () => {
    const bytes = readFileSync('../shared/frame-fixtures/good/multi-surface-preview.bin');
    const buffer = new ArrayBuffer(bytes.byteLength);
    new Uint8Array(buffer).set(bytes);
    const frame = decodeFrame(buffer);
    expect(frame.header.kind).toBe('preview');
    expect(frame.header.surfaces?.map((surface) => surface.role)).toEqual(['horn.inner', 'horn.outer']);
    expect(frame.sections.hornPositions).toBeInstanceOf(Float32Array);
    expect(frame.header.sections.find((section) => section.name === 'hornPositions')?.shape).toEqual([24, 3]);
    expect(frame.sections.hornPositions[0]).toBe(8);
  });
});
