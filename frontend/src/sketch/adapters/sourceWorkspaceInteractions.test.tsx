import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { Workspace } from '../workspace/Workspace';
import type { WorkspaceSession } from '../workspace/types';
import type { Authoring } from '../controller';
import type { Command } from '../commands';
import type { XY } from '../solver/types';
import { sourceContourAdapter as adapter, seed, type SourceMeta } from './sourceContour';
import { editorFixtures } from './sourceContourTestSupport';
import { sketchViewKey } from '../viewPreferences';

let host: HTMLDivElement, root: Root;
const command = vi.fn();
const selectionChanged = vi.fn();
const arcState = () => seed(editorFixtures()[5]);
function Harness({ state, selected = [], hover, busy = false }: {
  state: Authoring<SourceMeta>; selected?: string[]; hover?: string; busy?: boolean;
}) {
  const [selection, setSelection] = useState(selected);
  const session = { current: state, status: adapter.status(state), busy, command } as unknown as WorkspaceSession<Authoring<SourceMeta>, Command>;
  return <Workspace adapter={adapter} state={state} session={session} selection={selection} hoverId={hover}
    onSelectionChange={ids => { selectionChanged(ids); setSelection(ids); }}/>;
}
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.removeItem(sketchViewKey); command.mockReset(); selectionChanged.mockClear();
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); });
const svg = () => host.querySelector<SVGSVGElement>('svg[role="application"]')!;
const pointer = async (type: string, at: XY, options = {}) => act(async () => {
  // Dispatch on the SVG so DOM-target fallback cannot conceal an adapter hit defect.
  svg().dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, clientX: at.x, clientY: at.y,
    buttons: type === 'pointerup' ? 0 : 1, ...options }));
});
const button = (label: string) => [...host.querySelectorAll('button')].find(b => b.textContent === label)!;
async function mount(props: Parameters<typeof Harness>[0]) {
  command.mockResolvedValue(props.state);
  await act(async () => root.render(<Harness {...props}/>));
  vi.spyOn(svg(), 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 640, height: 360 } as DOMRect);
}

it.each(['selected', 'hovered'] as const)('picks a %s arc centre through generic Workspace hit testing', async visibility => {
  const state = arcState(), arc = state.sketch.entities.find(e => e.kind === 'arc')!;
  if (arc.kind !== 'arc') throw Error('Expected arc');
  await mount({ state, selected: visibility === 'selected' ? [arc.id] : [], hover: visibility === 'hovered' ? arc.id : undefined });
  const centre = host.querySelector(`circle.sketch-point-hit[data-id="${arc.center}"]`)!;
  const at = { x: Number(centre.getAttribute('cx')), y: Number(centre.getAttribute('cy')) };
  await pointer('pointerdown', at); await pointer('pointerup', at);
  expect(selectionChanged).toHaveBeenLastCalledWith([arc.center]);
  expect(host.querySelector(`.sketch-centre[data-id="${arc.center}"]`)?.classList.contains('selected')).toBe(true);
});

it.each(['selected', 'hovered'] as const)('marquee-selects a %s arc centre through generic Workspace', async visibility => {
  const state = arcState(), arc = state.sketch.entities.find(e => e.kind === 'arc')!;
  if (arc.kind !== 'arc') throw Error('Expected arc');
  await mount({ state, selected: visibility === 'selected' ? [arc.id] : [], hover: visibility === 'hovered' ? arc.id : undefined });
  const centre = host.querySelector(`circle.sketch-point-hit[data-id="${arc.center}"]`)!;
  const at = { x: Number(centre.getAttribute('cx')), y: Number(centre.getAttribute('cy')) };
  const modifiers = { shiftKey: visibility === 'selected' };
  await pointer('pointerdown', { x: at.x - 12, y: at.y - 12 }, modifiers);
  await pointer('pointermove', { x: at.x + 12, y: at.y + 12 }, modifiers);
  expect(host.querySelector('.sketch-marquee')).not.toBeNull();
  await pointer('pointerup', { x: at.x + 12, y: at.y + 12 }, modifiers);
  expect(selectionChanged.mock.calls.at(-1)?.[0]).toContain(arc.center);
});

it('keeps property commands available while busy and clears selection after property deletion', async () => {
  const state = arcState();
  state.sketch.constraints.push({ id: 'radius', kind: 'distanceX', a: 'pole', b: 'rim', value: 8, driving: false });
  await mount({ state, selected: ['radius'], busy: true });
  expect(button('Make driving').disabled).toBe(true);
  expect(button('Reset position').disabled).toBe(false);
  expect(button('Delete selection').disabled).toBe(false);
  await act(async () => button('Delete selection').click());
  expect(command).toHaveBeenCalledWith([{ kind: 'removeConstraint', id: 'radius' }]);
  expect(selectionChanged).toHaveBeenLastCalledWith([]);
  expect(host.querySelector('[aria-label="Selection"]')).toBeNull();
});

it('keeps construction and conversion property commands enabled while busy', async () => {
  await mount({ state: arcState(), selected: ['piston'], busy: true });
  expect(button('Construction (X)').disabled).toBe(false);
  expect(button('Make line').disabled).toBe(false);
  expect(button('Delete selection').disabled).toBe(false);
});
