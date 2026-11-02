import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { workspaceModeStore } from '../stores/workspaceMode';
import { viewerPreferences } from '../viewerprefs/viewerPreferences';
import { observeLivePreview } from './previewSocket';

let dispose: (() => void) | undefined;
beforeEach(() => { viewerPreferences.reset(); workspaceModeStore.setMode('cad'); });
afterEach(() => { dispose?.(); dispose = undefined; });

it('defers parametric geometry during CAD restore and requests it when Parametric opens', async () => {
  const control = { start: vi.fn(), stop: vi.fn() };
  dispose = observeLivePreview(control);
  await Promise.resolve();
  expect(control.start).not.toHaveBeenCalled();
  workspaceModeStore.setMode('parametric');
  await Promise.resolve();
  expect(control.start).toHaveBeenCalledTimes(1);
  workspaceModeStore.setMode('cad');
  await Promise.resolve();
  expect(control.stop).toHaveBeenCalledTimes(2);
});

it('keeps live update off across workspace switches and honours a late preference', async () => {
  viewerPreferences.update({ liveUpdate: false });
  const control = { start: vi.fn(), stop: vi.fn() };
  dispose = observeLivePreview(control);
  workspaceModeStore.setMode('parametric');
  await Promise.resolve();
  expect(control.start).not.toHaveBeenCalled();
  viewerPreferences.update({ liveUpdate: true });
  await Promise.resolve();
  expect(control.start).toHaveBeenCalledTimes(1);
  viewerPreferences.update({ liveUpdate: false });
  await Promise.resolve();
  expect(control.stop).toHaveBeenCalledTimes(3);
});

it('cancels queued work and subscriptions when the shell unmounts', async () => {
  const control = { start: vi.fn(), stop: vi.fn() };
  dispose = observeLivePreview(control);
  dispose(); dispose = undefined;
  workspaceModeStore.setMode('parametric');
  viewerPreferences.update({ liveUpdate: true });
  await Promise.resolve();
  expect(control.start).not.toHaveBeenCalled();
  expect(control.stop).toHaveBeenCalledTimes(1);
});

it('does not build a restored design but validates edits in CAD until another document loads', async () => {
  const control = { start: vi.fn(), stop: vi.fn() };
  dispose = observeLivePreview(control);
  resetDesignStore();
  await Promise.resolve();
  expect(control.start).not.toHaveBeenCalled();
  useDesignStore.getState().updateValue('a', 95);
  await Promise.resolve();
  expect(control.start).toHaveBeenCalledTimes(1);
  resetDesignStore();
  await Promise.resolve();
  expect(control.stop).toHaveBeenCalledTimes(3);
});
