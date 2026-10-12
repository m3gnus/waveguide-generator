import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it } from 'vitest';
import { SelectRow, selectRowLayout } from './SelectRow';

describe('select row layout', () => {
  it('keeps short choices inline and stacks labels over 16 characters', () => {
    expect(selectRowLayout([])).toBe('inline');
    expect(selectRowLayout(['Yes', 'No', 'Uniform'])).toBe('inline');
    expect(selectRowLayout(['1234567890123456'])).toBe('inline');
    expect(selectRowLayout(['12345678901234567'])).toBe('stacked');
    expect(selectRowLayout(['Normal velocity', 'Axial rigid-piston velocity'])).toBe('stacked');
  });

  it('uses actual option text including dynamic suffixes and option labels', () => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    const host = document.createElement('div');
    const root = createRoot(host);
    try {
      act(() => root.render(<SelectRow><label htmlFor="choice">Choice</label><select id="choice"><option>Uniform</option></select></SelectRow>));
      expect(host.firstElementChild?.classList.contains('stacked-select-row')).toBe(false);
      act(() => root.render(<SelectRow><label htmlFor="choice">Choice</label><select id="choice"><>{['BEMPP'].map((name) => <option key={name}>{name}{' · checking OpenCL…'}</option>)}</></select></SelectRow>));
      expect(host.firstElementChild?.classList.contains('stacked-select-row')).toBe(true);
      act(() => root.render(<SelectRow><select><option label="Thickened waveguide (freestanding)">Short</option></select></SelectRow>));
      expect(host.firstElementChild?.classList.contains('stacked-select-row')).toBe(true);
    } finally {
      act(() => root.unmount());
    }
  });
});
