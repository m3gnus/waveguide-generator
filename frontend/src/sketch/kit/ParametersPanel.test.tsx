import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { ParametersPanel } from './ParametersPanel';
import { DimensionExpressionField } from './DimensionExpressionField';
import { applyCommand, importedSketch } from '../ops';
import type { Sketch } from '../model';
import type { Command } from '../commands';

let host: HTMLDivElement, root: Root, current: Sketch, commands: Command[];
function base(): Sketch {
  const s = importedSketch([{ id: 'a', x: 0, y: 0 }, { id: 'b', x: 10, y: 0 }], [{ id: 'line', kind: 'line', p1: 'a', p2: 'b' }]);
  s.constraints = [{ id: 'width', kind: 'distance', a: 'a', b: 'b', value: 10, label: 'Width', driving: true }];
  return s;
}
function Harness({ initial = base(), enabled = true, pending = false }: { initial?: Sketch; enabled?: boolean; pending?: boolean }) {
  const [sketch, setSketch] = useState(initial); current = sketch;
  return <ParametersPanel sketch={sketch} enabled={enabled} pending={pending} command={async command => {
    const result = applyCommand(sketch, command); if ('error' in result) throw new Error(result.error);
    commands.push(command); setSketch(result.sketch);
  }}/>;
}
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div'); document.body.append(host); root = createRoot(host); commands = [];
});
afterEach(() => { act(() => root.unmount()); host.remove(); });
const input = (label: string) => host.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!;
const button = (label: string) => [...host.querySelectorAll('button')].find(b => (b.getAttribute('aria-label') || b.textContent) === label)!;
async function change(target: HTMLInputElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(target, value);
    target.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
const key = (target: Element, key: string) => act(async () => { target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })); });
const click = (target: HTMLElement) => act(async () => target.click());

it('adds numbered parameters and focuses the name, edits with live values, changes units and deletes unused rows', async () => {
  await act(async () => root.render(<Harness/>));
  expect(host.textContent).toContain('No parameters. Add one to reuse a value in several dimensions.');
  await click(button('Add parameter'));
  expect(current.parameters).toEqual([{ name: 'p1', expression: '0', unit: 'mm', value: 0 }]);
  expect(document.activeElement).toBe(input('Parameter name: p1'));
  await change(input('Parameter name: p1'), 'spacing');
  await key(input('Parameter name: p1'), 'Enter');
  expect(current.parameters?.[0].name).toBe('spacing');
  await change(input('Expression: spacing'), '2 cm');
  expect(host.querySelector('output')?.textContent).toBe('= 20.00 mm');
  await key(input('Expression: spacing'), 'Enter');
  expect(current.parameters?.[0]).toMatchObject({ expression: '2 cm', value: 20 });
  const select = host.querySelector<HTMLSelectElement>('select')!;
  await act(async () => { select.value = 'none'; select.dispatchEvent(new Event('change', { bubbles: true })); });
  expect(current.parameters?.[0].unit).toBe('none');
  await click(button('Add parameter')); expect(current.parameters?.[1].name).toBe('p1');
  await click(button('Add parameter')); expect(current.parameters?.[2].name).toBe('p2');
  await click(button('Delete parameter: spacing'));
  expect(current.parameters?.map(p => p.name)).toEqual(['p1', 'p2']);
  expect(commands.filter(c => c.kind === 'renameParameter')).toHaveLength(1);
});

it('commits names on blur, cancels on Escape and shows invalid-name and expression errors inline', async () => {
  await act(async () => root.render(<Harness/>)); await click(button('Add parameter'));
  await change(input('Parameter name: p1'), 'Width'); await key(input('Parameter name: p1'), 'Enter');
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('already used');
  expect(current.parameters?.[0].name).toBe('p1');
  await key(input('Parameter name: p1'), 'Escape'); expect(input('Parameter name: p1').value).toBe('p1');
  await change(input('Parameter name: p1'), 'spacing');
  await act(async () => input('Parameter name: p1').blur());
  expect(current.parameters?.[0].name).toBe('spacing');
  await change(input('Expression: spacing'), 'missing + 1');
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('missing');
  await key(input('Expression: spacing'), 'Enter');
  expect(current.parameters?.[0].expression).toBe('0');
});

it('lists parameter and dimension completion names and inserts the shared reference syntax', async () => {
  const initial = base(); initial.parameters = [{ name: 'pitch', expression: '2', value: 2, unit: 'mm' }];
  function Entry() {
    const [value, setValue] = useState('');
    const dimension = initial.constraints[0]; if (!('value' in dimension)) throw new Error('dimension');
    return <DimensionExpressionField sketch={initial} dimension={dimension} value={value} onChange={setValue}/>;
  }
  await act(async () => root.render(<Entry/>));
  await change(input('Dimension value'), 'pit');
  expect(host.querySelector('[role="option"]')?.textContent).toBe('pitch');
  await key(input('Dimension value'), 'Enter'); expect(input('Dimension value').value).toBe('pitch');
  await act(async () => root.render(<Harness initial={initial}/>));
  await change(input('Expression: pitch'), 'Wi');
  expect(host.querySelector('[role="option"]')?.textContent).toBe('Width');
  await key(input('Expression: pitch'), 'Tab'); expect(input('Expression: pitch').value).toBe('Width');
});

it('shows dependency counts and disables deletion with a tooltip listing the users', async () => {
  const initial = base(); initial.parameters = [{ name: 'pitch', expression: '2', value: 2, unit: 'mm' }, { name: 'twice', expression: 'pitch*2', value: 4, unit: 'mm' }];
  initial.constraints[0].expression = 'pitch * 5';
  await act(async () => root.render(<Harness initial={initial}/>));
  expect(button('Delete parameter: pitch').disabled).toBe(true);
  expect(button('Delete parameter: pitch').title).toContain('Width');
  expect(button('Delete parameter: pitch').title).toContain('twice');
  expect(host.textContent).toContain('used by 2');
  await change(input('Parameter name: pitch'), 'spacing'); await key(input('Parameter name: pitch'), 'Enter');
  expect(current.constraints[0].expression).toBe('spacing * 5');
  expect(current.parameters?.[1].expression).toBe('spacing*2');
});

it.each([{ enabled: false }, { pending: true }])('disables edits when unavailable: %j', async props => {
  await act(async () => root.render(<Harness {...props}/>));
  expect(button('Add parameter').disabled).toBe(true);
});
