import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import fixtures from '../../../shared/profile-inspection/mesher-profiles.json';
import { inspectProfileSamples } from '../api/designIo';
import { profileSamples } from '../api/profileSamples';
import { commitProfileEdit, currentEditOrigin, setAnchorValue, type EditBuilder } from '../design/customProfileEdit';
import { designForFamily, resetDesignStore, useDesignStore } from '../stores/design';
import { registerParameterPanel } from '../design/parameterPanelValidity';
import { CustomProfileTable } from '../design/FreeformEditors';
import { createProfileAdapter, anchorId, profileBinding } from './adapters/profile';
import { ProjectHistoryAuthority } from './workspace/projectHistoryAuthority';
import { ProfileController } from './profileController';
import { ProfileCanvasDialog, ProfileCanvasLauncher } from './ProfileCanvas';
import { closeProfileCanvas } from './profileCanvasVisibility';
import { focusableSelector } from '../shell/dialogFocus';
import { getProfileDraft, profileDraftBlocker, setProfileDraft } from './profileDraft';
import { profileServer } from './profileCanvasTestSupport';

let host: HTMLDivElement, root: Root;
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
const tick = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(350); await flush(); }); };
const mount = async () => { await act(async () => { root.render(<><ProfileCanvasLauncher/><ProfileCanvasDialog/></>); await flush(); }); await act(async () => button('Edit profile on canvas…').click()); };
const button = (text: string) => [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent === text)!;
const svg = () => document.querySelector<SVGSVGElement>('svg[role="application"]')!;
const point = (id: string) => document.querySelector<SVGCircleElement>(`.sketch-point[data-id="${id}"]`)!;
const xy = (id: string) => ({ x: Number(point(id).getAttribute('cx')), y: Number(point(id).getAttribute('cy')) });
async function pointer(target: Element, phase: string, at: { x: number; y: number }) {
  await act(async () => {
    const event = new MouseEvent(phase, { bubbles: true, button: 0, buttons: phase === 'pointerup' ? 0 : 1, clientX: at.x, clientY: at.y });
    Object.defineProperty(event, 'pointerId', { value: 1 }); target.dispatchEvent(event); await flush();
  });
}
async function choose(id: string) { const at = xy(id); await pointer(point(id), 'pointerdown', at); await pointer(svg(), 'pointerup', at); }

beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers(); closeProfileCanvas(); resetDesignStore();
  const design = designForFamily('FREEFORM'); design.shared_profile = true;
  design.profile_h!.points.splice(1, 0, { t: 1 / 3, r: 30 });
  useDesignStore.getState().replaceDocument(design); useDesignStore.temporal.getState().clear();
  profileServer(); host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); closeProfileCanvas(); setProfileDraft(null); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('profile canvas UI', () => {
  it('launches a portalled dialog, traps focus, and restores the launcher after Escape or Close', async () => {
    await act(async () => root.render(<><ProfileCanvasLauncher/><ProfileCanvasDialog/></>));
    const launch = button('Edit profile on canvas…');
    expect(launch.className).toBe('btn'); expect(launch.getAttribute('aria-haspopup')).toBe('dialog');
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    launch.focus(); await act(async () => launch.click()); await tick();
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!;
    expect(host.contains(dialog)).toBe(false); expect(dialog.getAttribute('aria-modal')).toBe('true');
    expect(dialog.classList.contains('source-sketch-editor')).toBe(true);
    expect(document.activeElement).toBe(button('Close'));
    const controls = [...dialog.querySelectorAll<HTMLElement>(focusableSelector)];
    const first = controls[0], last = controls.at(-1)!;
    first.focus();
    await act(async () => first.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true })));
    expect(document.activeElement).toBe(last);
    await act(async () => last.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true })));
    expect(document.activeElement).toBe(first);
    await act(async () => button('Insert exact').click());
    await act(async () => svg().dispatchEvent(new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true })));
    expect(document.querySelector('[role="dialog"]')).toBe(dialog);
    // Escape clears the selection before closing the dialog.
    await choose(anchorId('H', 1));
    expect(dialog.querySelector('aside [aria-label="Selected point join"]')).not.toBeNull();
    await act(async () => svg().dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    expect(document.querySelector('[role="dialog"]')).toBe(dialog);
    expect(dialog.querySelector('aside [aria-label="Selected point join"]')).toBeNull();
    await act(async () => svg().dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    expect(document.querySelector('[role="dialog"]')).toBeNull(); expect(document.activeElement).toBe(launch);
    await act(async () => launch.click()); await tick();
    await act(async () => button('Close').click());
    expect(document.querySelector('[role="dialog"]')).toBeNull(); expect(document.activeElement).toBe(launch);
  }, 15_000);

  it('cancels a live point drag without a preview or Undo step, then closes on a second Escape', async () => {
    const endDrag = vi.spyOn(ProfileController.prototype, 'endDrag');
    await mount(); await tick();
    const before = structuredClone(useDesignStore.getState().design), at = xy(anchorId('H', 1));
    await pointer(point(anchorId('H', 1)), 'pointerdown', at);
    await pointer(svg(), 'pointermove', { x: at.x + 12, y: at.y - 10 });
    expect(document.querySelector('.sketch-conflict-banner')).toBeNull();
    expect(button('Open sketch')).toBeUndefined();
    await pointer(svg(), 'pointermove', { x: at.x + 18, y: at.y - 20 });
    await act(async () => { await vi.advanceTimersByTimeAsync(20); await flush(); });
    expect(xy(anchorId('H', 1))).not.toEqual(at);
    expect(profileDraftBlocker()).toBeTruthy();
    await act(async () => { svg().dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await flush(); });
    expect(endDrag).toHaveBeenCalledWith(true);
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    expect(xy(anchorId('H', 1))).toEqual(at);
    expect(useDesignStore.getState().design).toEqual(before);
    expect(useDesignStore.temporal.getState().pastStates).toHaveLength(0);
    expect(profileDraftBlocker()).toBeNull();
    // Releasing after cancellation cannot finish the discarded drag.
    await pointer(svg(), 'pointerup', { x: at.x + 18, y: at.y - 20 });
    await tick();
    expect(profileDraftBlocker()).toBeNull();
    expect(useDesignStore.temporal.getState().pastStates).toHaveLength(0);
    await act(async () => svg().dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });

  it('cancels a box selection before closing on a second Escape', async () => {
    await mount(); await tick();
    await pointer(svg(), 'pointerdown', { x: 1, y: 1 });
    await pointer(svg(), 'pointermove', { x: 500, y: 400 });
    expect(document.querySelector('.sketch-marquee')).not.toBeNull();
    await act(async () => document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    expect(document.querySelector('.sketch-marquee')).toBeNull();
    await pointer(svg(), 'pointerup', { x: 500, y: 400 });
    expect(document.querySelector('aside [aria-label="Selected point join"]')).toBeNull();
    await act(async () => svg().dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });

  it.each(['Insert exact', 'Insert refit'])('returns %s to Select before closing on a second Escape', async tool => {
    await mount(); await tick();
    const before = structuredClone(useDesignStore.getState().design);
    await act(async () => button(tool).click());
    expect(button(tool).getAttribute('aria-pressed')).toBe('true');
    await act(async () => document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    expect(button(tool).getAttribute('aria-pressed')).toBe('false');
    expect(document.querySelector('[aria-label="Select (Esc)"]')?.getAttribute('aria-pressed')).toBe('true');
    expect(useDesignStore.getState().design).toEqual(before);
    expect(useDesignStore.temporal.getState().pastStates).toHaveLength(0);
    await act(async () => svg().dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });

  it('keeps accepted edits and project Undo/Redo after closing and reopening', async () => {
    await mount(); await tick(); await choose(anchorId('H', 1));
    const before = structuredClone(useDesignStore.getState().design);
    const join = document.querySelector<HTMLSelectElement>('[aria-label="Selected point join"]')!;
    await act(async () => { join.value = 'corner'; join.dispatchEvent(new Event('change', { bubbles: true })); await flush(); });
    await act(async () => { button('Accept').click(); await flush(); });
    const after = structuredClone(useDesignStore.getState().design);
    await act(async () => button('Close').click());
    expect(useDesignStore.getState().design).toEqual(after);
    await act(async () => button('Edit profile on canvas…').click()); await tick();
    await act(async () => { button('Undo').click(); await flush(); });
    expect(useDesignStore.getState().design).toEqual(before);
    await act(async () => { button('Redo').click(); await flush(); });
    expect(useDesignStore.getState().design).toEqual(after);
  }, 15_000);

  it('draws both layers in the shared workspace, uses round/square anchors, and toggles generated and plane inspection', async () => {
    await mount(); await tick();
    expect(document.querySelector('[role="toolbar"][aria-label="Profile tools"]')).not.toBeNull();
    expect(document.querySelector('.profile-authored')).not.toBeNull(); expect(document.querySelector('.profile-generated')).not.toBeNull();
    expect(document.querySelectorAll('.sketch-point')).toHaveLength(3); expect(point(anchorId('H', 0)).tagName).toBe('circle');
    expect(document.querySelector('.profile-V')).toBeNull();
    const toggle = [...document.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')][0];
    await act(async () => toggle.click()); expect(document.querySelector('.profile-generated')).toBeNull();
    await act(async () => toggle.click());
    const planes = document.querySelector<HTMLSelectElement>('select[aria-label="Profile planes"]')!;
    await act(async () => { planes.value = 'V'; planes.dispatchEvent(new Event('change', { bubbles: true })); });
    expect(document.querySelectorAll('.profile-generated')).toHaveLength(1);
    expect(document.querySelector('.profile-generated')?.querySelector('title')?.textContent).toBe('Generated V');
    expect(document.body.textContent).not.toContain('DOF');
  });

  it('previews a join change with the old curve dashed, both deviations, Accept and Cancel', async () => {
    await mount(); await tick(); await choose(anchorId('H', 1));
    const join = document.querySelector<HTMLSelectElement>('[aria-label="Selected point join"]')!;
    await act(async () => { join.value = 'corner'; join.dispatchEvent(new Event('change', { bubbles: true })); await flush(); });
    expect(document.querySelector('.ghost')).not.toBeNull();
    expect(document.body.textContent).toContain('curve deviation 0.125 mm'); expect(document.body.textContent).toContain('Generated profile change');
    expect(profileDraftBlocker()).toContain('unfinished'); expect(useDesignStore.temporal.getState().pastStates).toHaveLength(0);
    await act(async () => { button('Cancel').click(); await flush(); });
    expect(profileDraftBlocker()).toBeNull(); expect(useDesignStore.getState().design.profile_h!.points[1].join).toBeUndefined();
    await act(async () => { join.value = 'corner'; join.dispatchEvent(new Event('change', { bubbles: true })); await flush(); });
    await act(async () => { button('Accept').click(); await flush(); });
    expect(useDesignStore.getState().design.profile_h!.points[1].join).toBe('corner');
    expect(document.querySelector('rect.sketch-point')).not.toBeNull(); expect(useDesignStore.temporal.getState().pastStates).toHaveLength(1);
  });

  it('inserts by clicking the authored curve, defaults to exact, and deletes through the same preview', async () => {
    const fetcher = profileServer(); await mount(); await tick();
    const start = xy(anchorId('H', 0)), end = xy(anchorId('H', 2));
    const at = { x: start.x + (end.x - start.x) * 21.407 / 120, y: start.y + (end.y - start.y) * (20.125 - 12.7) / (140 - 12.7) };
    await act(async () => button('Insert exact').click()); await pointer(svg(), 'pointerdown', at); await pointer(svg(), 'pointerup', at);
    expect(button('Open sketch')).toBeUndefined();
    expect(document.querySelector('.sketch-conflict-banner')).toBeNull();
    expect(button('Accept').closest('aside')).not.toBeNull();
    expect(profileDraftBlocker()).toContain('unfinished');
    expect(fetcher.mock.calls.some(([url, init]) => String(url).endsWith('/insert') && JSON.parse(String(init?.body)).mode === 'exact')).toBe(true);
    await act(async () => { button('Accept').click(); await flush(); }); await tick();
    expect(useDesignStore.getState().design.profile_h!.points).toHaveLength(4);
    await choose(anchorId('H', 1)); await act(async () => { button('Delete point').click(); await flush(); });
    expect(document.body.textContent).toContain('Preview: Delete point');
    await act(async () => { button('Accept').click(); await flush(); });
    expect(useDesignStore.getState().design.profile_h!.points).toHaveLength(3);
    expect(useDesignStore.temporal.getState().pastStates).toHaveLength(2);
  });

  it('makes one live drag into one accepted Undo step and keeps recovery outside the dialog', async () => {
    await mount(); await tick(); const before = structuredClone(useDesignStore.getState().design), at = xy(anchorId('H', 1));
    await pointer(point(anchorId('H', 1)), 'pointerdown', at);
    await pointer(svg(), 'pointermove', { x: at.x + 12, y: at.y - 10 });
    await pointer(svg(), 'pointermove', { x: at.x + 18, y: at.y - 20 });
    await pointer(svg(), 'pointerup', { x: at.x + 18, y: at.y - 20 });
    expect(document.body.textContent).toContain('Preview: Move point'); expect(useDesignStore.getState().design).toEqual(before);
    expect(useDesignStore.temporal.getState().pastStates).toHaveLength(0); expect(profileDraftBlocker()).toBeTruthy();
    expect(button('Open sketch')).toBeUndefined();
    expect(document.querySelector('.sketch-conflict-banner')).toBeNull();
    expect(button('Accept').closest('aside')).not.toBeNull();
    await act(async () => { getProfileDraft()!.open(); }); expect(document.activeElement).toBe(svg());
    await act(async () => { button('Accept').click(); await flush(); });
    expect(profileDraftBlocker()).toBeNull(); expect(useDesignStore.temporal.getState().pastStates).toHaveLength(1);
    const moved = structuredClone(useDesignStore.getState().design);
    expect(document.querySelector('aside')?.textContent).toContain('Move point: applied. Undo restores the previous profile.');
    await act(async () => { useDesignStore.getState().undo(); await flush(); }); expect(useDesignStore.getState().design).toEqual(before);
    expect(document.querySelector('aside')?.textContent).not.toContain('applied. Undo restores');
    await act(async () => { useDesignStore.getState().redo(); await flush(); }); expect(useDesignStore.getState().design).toEqual(moved);
    expect(document.querySelector('aside')?.textContent).not.toContain('applied. Undo restores');
    await tick(); await choose(anchorId('H', 1));
    await act(async () => { button('Delete point').click(); await flush(); });
    await act(async () => { getProfileDraft()!.revert(); await flush(); });
    expect(profileDraftBlocker()).toBeNull(); expect(useDesignStore.getState().design).toEqual(moved);
    expect(useDesignStore.temporal.getState().pastStates).toHaveLength(1);
  });

  it('blocks invalid local NumberField drafts, and Revert uses their existing Escape cancellation', async () => {
    host.className = 'param-panel'; const unregister = registerParameterPanel(host);
    await mount(); await tick(); await choose(anchorId('H', 1));
    const label = [...document.querySelectorAll<HTMLLabelElement>('label')].find(label => label.textContent === 'r (design mm)')!;
    const input = document.getElementById(label.htmlFor) as HTMLInputElement;
    await act(async () => {
      input.focus(); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '-1');
      input.dispatchEvent(new Event('input', { bubbles: true })); await flush();
    });
    expect(profileDraftBlocker()).toContain('invalid'); expect(button('Revert to last valid').closest('aside')).not.toBeNull();
    expect(document.querySelector('.profile-canvas-freshness')?.closest('aside')).not.toBeNull();
    expect(document.querySelector('.sketch-conflict-banner')).toBeNull();
    await act(async () => { button('Revert to last valid').click(); await flush(); });
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    expect(profileDraftBlocker()).toBeNull(); expect(useDesignStore.temporal.getState().pastStates).toHaveLength(0); unregister?.();
  });

  it('preserves a pending preview on Escape and cancels it on the first Ctrl+Z without undoing the preceding edit', async () => {
    await mount(); await tick(); await choose(anchorId('H', 1));
    const before = useDesignStore.getState().design;
    commitProfileEdit(currentEditOrigin(), before, setAnchorValue(before, 'H', 1, 'r', 33).after);
    await act(async () => { await flush(); }); await tick();
    await act(async () => { button('Delete point').click(); await flush(); });
    expect(profileDraftBlocker()).toBeTruthy();
    await act(async () => { svg().dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await flush(); });
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    await act(async () => { svg().dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await flush(); });
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(profileDraftBlocker()).toBeTruthy();
    await act(async () => { getProfileDraft()!.open(); await flush(); });
    expect(document.body.textContent).toContain('Preview: Delete point');
    await act(async () => { svg().dispatchEvent(new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true })); await flush(); });
    expect(profileDraftBlocker()).toBeNull(); expect(useDesignStore.getState().design.profile_h!.points[1].r).toBe(33);
    expect(useDesignStore.temporal.getState().pastStates).toHaveLength(1);
  });

  it('keeps a table preview in the same Solve gate and lets the canvas recovery action cancel it', async () => {
    await act(async () => root.render(<div className="param-panel"><CustomProfileTable plane="H" field={{ id: 'freeform.interiorH', legacyKey: 'profile_h.points', section: 'Profile', label: 'H', kind: 'table' }}/><ProfileCanvasLauncher/><ProfileCanvasDialog/></div>));
    await tick();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    const join = document.querySelector<HTMLSelectElement>('select[aria-label="H point 1 join"]')!;
    await act(async () => { join.value = 'corner'; join.dispatchEvent(new Event('change', { bubbles: true })); await flush(); });
    expect(profileDraftBlocker()).toContain('unfinished');
    await act(async () => getProfileDraft()!.open());
    expect(document.querySelector('[role="dialog"][aria-label="Custom profile canvas"]')).not.toBeNull();
    expect(button('Open sketch')).toBeUndefined();
    expect(document.querySelector('.sketch-conflict-banner')).toBeNull();
    await act(async () => { getProfileDraft()!.revert(); await flush(); });
    expect(profileDraftBlocker()).toBeNull(); expect(useDesignStore.temporal.getState().pastStates).toHaveLength(0);
    expect(useDesignStore.getState().design.profile_h!.points[1].join).toBeUndefined();
  });

  it('opens the dialog from an invalid table draft while keeping Revert in the Solve gate', async () => {
    host.className = 'param-panel'; const unregister = registerParameterPanel(host);
    await act(async () => root.render(<><CustomProfileTable plane="H" field={{ id: 'freeform.interiorH', legacyKey: 'profile_h.points', section: 'Profile', label: 'H', kind: 'table' }}/><ProfileCanvasDialog/></>));
    const label = [...document.querySelectorAll<HTMLLabelElement>('label')].find(label => label.textContent === 'H point 1 radius')!;
    const input = document.getElementById(label.htmlFor) as HTMLInputElement;
    await act(async () => {
      input.focus(); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '-1');
      input.dispatchEvent(new Event('input', { bubbles: true })); await flush();
    });
    expect(profileDraftBlocker()).toContain('invalid');
    await act(async () => getProfileDraft()!.open());
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    await act(async () => { getProfileDraft()!.revert(); await flush(); });
    expect(profileDraftBlocker()).toBeNull(); expect(input.value).toBe('30.00'); unregister?.();
  });

  it.each([1, 1.7, .5])('ports FV: labels design and scaled coordinates for Scale %s', async scale => {
    useDesignStore.getState().updateValue('scale', scale); await mount(); await tick();
    const coordinates = document.querySelector<HTMLSelectElement>('[aria-label="Profile coordinates"]');
    if (scale === 1) expect(coordinates).toBeNull();
    else {
      expect(document.body.textContent).toContain(`Design mm before Scale (Scale = ${scale}).`);
      await act(async () => { coordinates!.value = 'scaled'; coordinates!.dispatchEvent(new Event('change', { bubbles: true })); });
      expect(document.body.textContent).toContain(`Plot in scaled mm (Scale = ${scale}).`);
      expect(document.querySelector('.profile-axis-label')?.parentElement?.textContent).toContain('scaled mm');
    }
  });

  it('ports FV: posts the serialized scaled design and propagates the abort signal', async () => {
    const design = { ...designForFamily('FREEFORM'), scale: 1.7 }, abort = new AbortController();
    const fetcher = vi.fn<typeof fetch>(async () => new Response(fixtures[4].csv));
    expect(await inspectProfileSamples(design, 42, abort.signal, fetcher)).toEqual(profileSamples(fixtures[4].csv));
    expect(fetcher).toHaveBeenCalledWith('/api/export/profiles?kind=profiles', expect.objectContaining({ method: 'POST', signal: abort.signal }));
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toMatchObject({ designRevision: 42, design: { scale: 1.7 } });
  });
});

describe('profile canvas freshness (FV regression net)', () => {
  type Request = { url: string; init?: RequestInit; response: Response; resolve(value: Response): void };
  let requests: Request[];
  beforeEach(() => {
    requests = []; const service = profileServer();
    vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (url, init) => {
      const response = await service(url, init);
      return new Promise<Response>(resolve => requests.push({ url: String(url), init, response, resolve }));
    }));
  });
  const respond = async (group: Request[] = requests) => act(async () => { group.forEach(request => request.resolve(request.response)); await flush(); });
  const curve = () => document.querySelector('.profile-authored')?.getAttribute('d');

  it('debounces requests, drops late curve/export replies, and hides another document immediately', async () => {
    await mount(); expect(requests).toHaveLength(0); await tick(); expect(requests).toHaveLength(2);
    await respond(); const firstCurve = curve(); expect(firstCurve).toBeTruthy();
    await act(async () => useDesignStore.getState().updateValue('length', 130)); await tick();
    const obsolete = requests.slice(2);
    await act(async () => useDesignStore.getState().replaceDocument(designForFamily('FREEFORM')));
    expect(document.querySelector('.profile-authored')).toBeNull(); expect(document.querySelector('.profile-generated')).toBeNull();
    await respond(obsolete); expect(document.querySelector('.profile-generated')).toBeNull();
    await tick(); await respond(requests.slice(4)); expect(curve()).toBeTruthy();
    expect(document.querySelector('.profile-canvas-freshness')?.textContent).toBe('');
    expect(obsolete.find(request => request.url.startsWith('/api/export'))?.init?.signal?.aborted).toBe(true);
  });

  it('dims last-valid layers during updates, reports failures, and recovers', async () => {
    await mount(); await tick(); await respond();
    await act(async () => useDesignStore.getState().updateValue('length', 130));
    expect(document.querySelector('.profile-authored.stale')).not.toBeNull(); expect(document.body.textContent).toContain('Updating… — last valid profile');
    await tick();
    await act(async () => {
      const generated = requests.slice(2).find(request => request.url.startsWith('/api/export'))!;
      generated.resolve(Response.json({ detail: 'Invalid corner radius' }, { status: 422 })); await flush();
    });
    expect(document.body.textContent).toContain('Invalid corner radius');
    await act(async () => useDesignStore.getState().updateValue('length', 140)); await tick(); await respond(requests.slice(4));
    expect(document.querySelector('.profile-generated.stale')).toBeNull();
  });

  it('guards replacement before React observes it and ignores responses after unmount', async () => {
    await mount(); await tick();
    await act(async () => {
      useDesignStore.getState().replaceDocument(designForFamily('FREEFORM'), { revision: useDesignStore.getState().designRevision });
      requests.forEach(request => request.resolve(request.response)); await flush();
    });
    expect(document.querySelector('.profile-generated')).toBeNull();
    await tick(); const pending = requests.slice(2);
    await act(async () => root.render(null));
    expect(pending.find(request => request.url.startsWith('/api/export'))?.init?.signal?.aborted).toBe(true);
    await respond(pending); expect(document.querySelector('.profile-generated')).toBeNull();
  });

  it('drops older same-document replies after accepting the newer keyed curve and export', async () => {
    await mount(); await tick(); await respond();
    await act(async () => useDesignStore.getState().updateValue('length', 130)); await tick(); const old = requests.slice(2);
    await act(async () => useDesignStore.getState().updateValue('length', 140)); await tick(); const fresh = requests.slice(4);
    await act(async () => {
      for (const request of fresh) {
        if (request.url.endsWith('/curves')) {
          const body = await request.response.json(); body.H.samples = [[0, 12.7], [140, 140]];
          request.resolve(Response.json(body));
        } else request.resolve(new Response(fixtures[1].csv));
      }
      await flush();
    });
    const accepted = [curve(), document.querySelector('.profile-generated')?.getAttribute('d')];
    await respond(old);
    expect([curve(), document.querySelector('.profile-generated')?.getAttribute('d')]).toEqual(accepted);
    expect(document.querySelector('.profile-canvas-freshness')?.textContent).toBe('');
  });

  it('reports an initial refusal without an authored curve and blocks Solve with an honest empty last-valid state', async () => {
    await mount(); await tick();
    await act(async () => {
      for (const request of requests) {
        const key = JSON.parse(String(request.init?.body)).content_key;
        request.resolve(Response.json({ content_key: key, detail: 'Bad profile' }, { status: 422 }));
      }
      await flush();
    });
    expect(document.body.textContent).toContain('Bad profile'); expect(document.querySelector('.profile-authored')).toBeNull();
    expect(document.querySelector('.profile-generated')).toBeNull(); expect(profileDraftBlocker()).toContain('invalid');
    expect(button('Revert to last valid').disabled).toBe(true);
  });
});

describe('profile curve request recovery', () => {
  it.each(['transport', '503', 'unkeyed 422'] as const)('keeps Solve unblocked after a %s failure and retries explicitly', async failure => {
    const service = profileServer(), respond = service.getMockImplementation()!;
    let failing = true;
    service.mockImplementation(async (url, init) => {
      if (String(url).endsWith('/curves') && failing) {
        if (failure === 'transport') throw new TypeError('Network unavailable');
        return new Response('Service unavailable', { status: failure === '503' ? 503 : 422 });
      }
      return respond(url, init);
    });
    await mount(); await tick();
    expect(profileDraftBlocker()).toBeNull();
    expect(button('Revert to last valid')).toBeUndefined();
    expect(button('Retry')).toBeTruthy();
    failing = false;
    await act(async () => button('Retry').click()); await tick();
    expect(document.querySelector('.profile-authored')).not.toBeNull();
    expect(document.querySelector('.profile-authored.stale')).toBeNull();
    expect(button('Retry')).toBeUndefined();
    expect(profileDraftBlocker()).toBeNull();
  });

  it.each(['reopen', 'revision'] as const)('retries a failed revision on %s without poisoning the shared query cache', async recovery => {
    const service = profileServer(), respond = service.getMockImplementation()!;
    let failing = true;
    service.mockImplementation(async (url, init) => {
      if (String(url).endsWith('/curves') && failing) throw new TypeError('Network unavailable');
      return respond(url, init);
    });
    await mount(); await tick();
    expect(button('Retry')).toBeTruthy(); expect(profileDraftBlocker()).toBeNull();
    failing = false;
    if (recovery === 'reopen') {
      await act(async () => button('Close').click());
      await act(async () => button('Edit profile on canvas…').click());
    } else await act(async () => useDesignStore.getState().updateValue('throat_ext_length', 12));
    await tick();
    expect(document.querySelector('.profile-authored')).not.toBeNull();
    expect(document.querySelector('.profile-authored.stale')).toBeNull();
    expect(button('Retry')).toBeUndefined(); expect(profileDraftBlocker()).toBeNull();
    expect(service.mock.calls.filter(([url]) => String(url).endsWith('/curves'))).toHaveLength(2);
  });
});

describe('profile gesture acceptance authority', () => {
  it('reuses the owner commit after one drag, interleaves a table edit, and rejects an older candidate', async () => {
    const authority = new ProjectHistoryAuthority(profileBinding(vi.fn()));
    let proposal: EditBuilder | undefined;
    const controller = new ProfileController({ initial: authority.current, authority, adapter: createProfileAdapter(), presentation: () => ({}),
      propose: (_label, build) => { proposal = build; }, drafting: vi.fn(), blocked: () => false });
    const before = structuredClone(controller.current);
    await controller.beginDrag([anchorId('H', 1)]);
    for (let r = 31; r <= 50; r++) await controller.dragTo([{ id: anchorId('H', 1), at: { x: 45, y: r } }]);
    await controller.endDrag();
    expect(controller.current).toEqual(before); expect(useDesignStore.temporal.getState().pastStates).toHaveLength(0);
    const origin = currentEditOrigin(), candidate = await proposal!(before, { origin, signal: new AbortController().signal });
    commitProfileEdit(origin, before, candidate.after); await controller.enqueue(async () => {});
    expect(useDesignStore.temporal.getState().pastStates).toHaveLength(1);
    const moved = useDesignStore.getState().design;
    commitProfileEdit(currentEditOrigin(), moved, setAnchorValue(moved, 'H', 1, 'r', 55).after);
    await controller.enqueue(async () => {}); expect(controller.current.profile_h!.points[1].r).toBe(55);
    expect(() => commitProfileEdit(origin, before, candidate.after)).toThrow('design changed');
    await controller.undo(); await controller.enqueue(async () => {}); expect(controller.current.profile_h!.points[1].r).toBe(50);
    await controller.undo(); await controller.enqueue(async () => {}); expect(controller.current).toEqual(before);
    controller.destroy();
  });
});
