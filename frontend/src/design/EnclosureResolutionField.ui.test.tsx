import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { hydrateDesignDocument } from '../api/designIo';
import { designForFamily, mergeDesignForFamily, resetDesignStore, serializeDesign, useDesignStore } from '../stores/design';
import { EnclosureResolutionField, parseEnclosureResolution } from './EnclosureResolutionField';
import { PARAMETER_REGISTRY, fieldIsVisible } from './parameterRegistry';

const field = PARAMETER_REGISTRY.find(({ id }) => id === 'enclosure.front_resolution')!;

it('accepts one to four numbers, as the server does, and retains documented fallback values', () => {
  expect(parseEnclosureResolution('8')).toBe(8);
  expect(parseEnclosureResolution('8.5')).toBe(8.5);
  expect(parseEnclosureResolution('0,12,16,20')).toEqual([0,12,16,20]);
  // "8,12" means 8,12,12,12 on the server; a comma here always separates values.
  expect(parseEnclosureResolution('8,12')).toEqual([8,12]);
  expect(parseEnclosureResolution('2,5')).toEqual([2,5]);
  expect(parseEnclosureResolution('8,12,16')).toEqual([8,12,16]);
  expect(parseEnclosureResolution('1.234,5')).toEqual([1.234,5]);
  for (const invalid of ['', '1 234', '8,', 'NaN', '8,Infinity', '8,12,16,20,24', '8,p']) expect(parseEnclosureResolution(invalid)).toBeUndefined();
});

it('round-trips new controls and raw quadrant spellings through client hydration', () => {
  const wire = { formula:'OSSE', cross_section:{exponent:4,aspect_ratio:1.5}, enclosure:{depth:90,depth_margin_mm:7,front_resolution:[{value:8,raw:'8.00'},{value:12,raw:'12.0'}],back_resolution:[20,24,28,32]} };
  const hydrated = hydrateDesignDocument(wire);
  const serialized = serializeDesign(hydrated);
  expect(serialized.cross_section).toEqual(wire.cross_section);
  expect(serialized.enclosure).toMatchObject(wire.enclosure);
  expect(hydrateDesignDocument(serialized).enclosure).toEqual(hydrated.enclosure);
  expect(mergeDesignForFamily(hydrated, 'FREEFORM').cross_section).toEqual({exponent:2,aspect_ratio:1});
});

it('offers cross-section controls only for the supported formulas', () => {
  const sectionFields = PARAMETER_REGISTRY.filter(({ id }) => id.startsWith('cross_section.'));
  expect(sectionFields).toHaveLength(2);
  expect(field.section).toBe('Mesh');
  for (const family of ['OSSE','R-OSSE','ICW','FREEFORM','SOURCE-DISK'] as const) {
    expect(sectionFields.every((entry) => fieldIsVisible(entry, designForFamily(family)))).toBe(['OSSE','R-OSSE','ICW'].includes(family));
  }
});

it('shows all imported quadrant spellings and commits a valid edited list', () => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const design = hydrateDesignDocument({formula:'OSSE',enclosure:{front_resolution:[{value:8,raw:'8.00'},{value:12,raw:'12.0'},{value:16,raw:'16'},{value:20,raw:'20'}]}});
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  const commit = vi.fn();
  try {
    act(() => root.render(<EnclosureResolutionField field={field} design={design} value={design.enclosure.front_resolution} onCommit={commit}/>));
    const input = host.querySelector('input')!;
    expect(input.value).toBe('8.00,12.0,16,20');
    act(() => { input.focus(); });
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '8,');
      input.dispatchEvent(new Event('input', {bubbles:true}));
    });
    expect(input.getAttribute('aria-invalid')).toBe('true');
    act(() => input.blur());
    expect(commit).not.toHaveBeenCalled();
    act(() => { input.focus(); });
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '8,12,24,32');
      input.dispatchEvent(new Event('input', {bubbles:true}));
    });
    act(() => input.blur());
    expect(commit).toHaveBeenCalledWith([8,12,24,32]);
  } finally {
    act(() => root.unmount());
    host.remove();
  }
});

it('undo restores imported quadrant spellings after editing a new resolution field', () => {
  resetDesignStore();
  const imported = hydrateDesignDocument({ formula:'OSSE', enclosure:{ depth:90, front_resolution:[{value:8,raw:'8.00'},{value:12,raw:'12.0'}] } });
  useDesignStore.getState().replaceDocument(imported);
  useDesignStore.getState().updateValue('enclosure.front_resolution', [16,20,24,28]);
  expect((serializeDesign(useDesignStore.getState().design).enclosure as Record<string, unknown>).front_resolution).toEqual([16,20,24,28]);
  useDesignStore.getState().undo();
  expect((serializeDesign(useDesignStore.getState().design).enclosure as Record<string, unknown>).front_resolution).toEqual([{value:8,raw:'8.00'},{value:12,raw:'12.0'}]);
  resetDesignStore();
});
