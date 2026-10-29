import { beforeEach, describe, expect, it } from 'vitest';
import { hydrateDesignDocument } from '../api/designIo';
import {
  HF_RADIATOR_ID, addWoofer, commitAssembly, deleteWoofer, emptyAssembly, moveToGroup, setDriver,
  setMemberProcessing, setPlacement, setWooferEnabled, updateWoofer, type DesignAssembly,
} from './assembly';
import { designForFamily, mergeDesignForFamily, resetDesignStore, serializeDesign, useDesignStore } from './design';
import { designContentKey } from './designContentKey';

let counter = 0;
const ids = () => `00000000-0000-4000-8000-${String(counter += 1).padStart(12, '0')}`;
const driver = { exterior_transducer: { version: 1, re_ohm: 6, le_h: 0.0005, bl_n_per_a: 7, mmd_kg: 0.02, cms_m_per_n: 0.001, rms_n_s_per_m: 1 } };
const fileSettings = { polar: { config: null }, solve: null };

function twoWoofers(): { assembly: DesignAssembly; first: string; second: string } {
  const start = addWoofer(emptyAssembly(ids), { anchor: 'below' }, ids);
  const next = addWoofer(start.assembly, { anchor: 'above', radiusMm: 65, shape: { kind: 'ath_cone' } }, ids);
  return { assembly: next.assembly, first: start.id, second: next.id };
}

describe('assembly identity', () => {
  beforeEach(() => { counter = 0; resetDesignStore(); });

  it('gives each new woofer a fresh UUID and its own signal group', () => {
    const { assembly, first, second } = twoWoofers();
    expect(assembly.woofers?.map(woofer => woofer.id)).toEqual([first, second]);
    expect(assembly.woofers?.map(woofer => woofer.label)).toEqual(['Woofer 1', 'Woofer 2']);
    expect(assembly.signal_groups?.map(group => group.members.map(member => member.radiator_id)))
      .toEqual([[HF_RADIATOR_ID], [first], [second]]);
    expect(new Set(assembly.signal_groups?.map(group => group.id)).size).toBe(3);
    expect(assembly.woofers?.[0].placement).toEqual({ anchor: 'below', gap_mm: 10 });
  });

  it('never lets a recreated label inherit a deleted woofer driver', () => {
    const { assembly, first } = twoWoofers();
    const withDriver = setDriver(assembly, first, driver);
    const deleted = deleteWoofer(withDriver, first);
    expect(deleted.drivers).toBeUndefined();
    const recreated = addWoofer(deleted, { anchor: 'below' }, ids);
    expect(recreated.assembly.woofers?.find(woofer => woofer.id === recreated.id)?.label).toBe('Woofer 1');
    expect(recreated.id).not.toBe(first);
    expect(recreated.assembly.drivers?.[recreated.id]).toBeUndefined();
  });

  it('keeps settings of unchanged identities when another woofer is edited or removed', () => {
    const { assembly, first, second } = twoWoofers();
    const configured = setMemberProcessing(setDriver(assembly, second, driver), second, { delay_ms: 0.25, invert: true });
    const edited = updateWoofer(deleteWoofer(configured, first), second, { radius_mm: 70 });
    expect(edited.drivers?.[second]).toEqual(driver);
    expect(edited.signal_groups?.find(group => group.members[0].radiator_id === second)?.members[0])
      .toEqual({ radiator_id: second, delay_ms: 0.25, invert: true });
  });

  it('disables without losing data and deletes relations, membership and crossover branches', () => {
    const { assembly, first, second } = twoWoofers();
    const related = setPlacement(assembly, second, { anchor: 'below', gap_mm: 5, spacing_to: { radiator_id: first, distance_mm: 180 } });
    const disabled = setWooferEnabled(related, first, false);
    expect(disabled.woofers?.[0]).toMatchObject({ id: first, enabled: false, radius_mm: 80 });
    const firstGroup = disabled.signal_groups!.find(group => group.members[0].radiator_id === first)!.id;
    const hfGroup = disabled.signal_groups![0].id;
    const crossed: DesignAssembly = { ...disabled, crossover: { enabled: true, spec: { members: [hfGroup, firstGroup], reference: firstGroup } } };
    const deleted = deleteWoofer(crossed, first);
    expect(deleted.woofers?.map(woofer => woofer.id)).toEqual([second]);
    expect(deleted.woofers?.[0].placement).toEqual({ anchor: 'below', gap_mm: 5 });
    expect(deleted.signal_groups?.some(group => group.id === firstGroup)).toBe(false);
    expect(deleted.crossover).toEqual({ enabled: false });
  });

  it('refuses dangling or cyclic spacing relations', () => {
    const { assembly, first, second } = twoWoofers();
    const related = setPlacement(assembly, second, { anchor: 'below', gap_mm: 5, spacing_to: { radiator_id: first, distance_mm: 180 } });
    expect(() => setPlacement(related, first, { anchor: 'below', gap_mm: 5, spacing_to: { radiator_id: second, distance_mm: 180 } })).toThrow(/cycle/);
    expect(() => setPlacement(related, first, { anchor: 'below', gap_mm: 5, spacing_to: { radiator_id: first, distance_mm: 1 } })).toThrow(/another woofer/);
  });

  it('moves radiators between groups and drops emptied groups', () => {
    const { assembly, first, second } = twoWoofers();
    const target = assembly.signal_groups!.find(group => group.members[0].radiator_id === first)!.id;
    const merged = moveToGroup(assembly, second, target, ids);
    expect(merged.signal_groups).toHaveLength(2);
    expect(merged.signal_groups!.find(group => group.id === target)!.members.map(member => member.radiator_id)).toEqual([first, second]);
    const split = moveToGroup(merged, second, null, ids);
    expect(split.signal_groups).toHaveLength(3);
    expect(split.signal_groups!.at(-1)).toMatchObject({ label: 'Woofer 2', members: [{ radiator_id: second }] });
  });
});

describe('assembly in the design document', () => {
  beforeEach(() => { counter = 0; resetDesignStore(); });

  it('is one project history step per edit, with Undo and Redo', () => {
    const before = structuredClone(useDesignStore.getState().design);
    const { assembly, first } = twoWoofers();
    commitAssembly(assembly);
    commitAssembly(setWooferEnabled(assembly, first, false));
    expect(useDesignStore.getState().design.assembly?.woofers?.[0].enabled).toBe(false);
    useDesignStore.getState().undo();
    expect(useDesignStore.getState().design.assembly).toEqual(assembly);
    useDesignStore.getState().undo();
    expect(useDesignStore.getState().design).toEqual(before);
    useDesignStore.getState().redo();
    expect(useDesignStore.getState().design.assembly).toEqual(assembly);
  });

  it('records a drag as one history step', () => {
    const { assembly, first } = twoWoofers();
    commitAssembly(assembly);
    const store = useDesignStore.getState();
    store.beginDrag();
    for (const gap of [11, 12, 13]) commitAssembly(setPlacement(assembly, first, { anchor: 'below', gap_mm: gap }), 'drag');
    useDesignStore.getState().endDrag();
    expect(useDesignStore.getState().design.assembly?.woofers?.[0].placement.gap_mm).toBe(13);
    useDesignStore.getState().undo();
    expect(useDesignStore.getState().design.assembly).toEqual(assembly);
  });

  it('keeps other expressions and absent fields when the assembly changes', () => {
    const design = designForFamily('OSSE');
    design._expressions = { L: { value: 120, raw: '100 + 20' } };
    design._absent = ['s1'];
    useDesignStore.getState().replaceDocument(design);
    commitAssembly(twoWoofers().assembly);
    const current = useDesignStore.getState().design;
    expect(current._expressions).toEqual({ L: { value: 120, raw: '100 + 20' } });
    expect(current._absent).toEqual(['s1']);
  });

  it('serializes, reopens and keys the assembly; a horn-only wire is unchanged', () => {
    const plain = designForFamily('R-OSSE');
    const plainWire = serializeDesign(plain);
    expect(plainWire).not.toHaveProperty('assembly');
    const { assembly, first } = twoWoofers();
    const design = { ...designForFamily('R-OSSE'), assembly: setDriver(assembly, first, driver) };
    const wire = serializeDesign(design);
    expect(wire.assembly).toEqual(design.assembly);
    const reopened = hydrateDesignDocument(JSON.parse(JSON.stringify(wire)));
    expect(reopened.assembly).toEqual(design.assembly);
    expect(reopened._absent ?? []).not.toContainEqual(expect.stringMatching(/^assembly/));
    expect(designContentKey(reopened, 'x', fileSettings)).toBe(designContentKey(design, 'x', fileSettings));
    expect(designContentKey(design, 'x', fileSettings)).not.toBe(designContentKey(plain, 'x', fileSettings));
  });

  it('carries the assembly across horn formula changes', () => {
    const { assembly } = twoWoofers();
    const design = { ...designForFamily('R-OSSE'), assembly };
    expect(mergeDesignForFamily(design, 'OSSE').assembly).toEqual(assembly);
    expect(mergeDesignForFamily(design, 'OSSE-NATIVE').assembly).toEqual(assembly);
  });
});
