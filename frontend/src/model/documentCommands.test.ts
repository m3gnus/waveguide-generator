import { afterEach, beforeEach, expect, it } from 'vitest';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { workspaceModeStore } from '../stores/workspaceMode';
import { documentHistory } from './documentCommands';

beforeEach(() => { resetDesignStore(); workspaceModeStore.setMode('parametric'); });
afterEach(() => { resetDesignStore(); workspaceModeStore.setMode('parametric'); });

it.each(['cad', 'file'] as const)('never changes parked A through Undo or Redo on %s', input => {
  useDesignStore.getState().updateField('L', 321);
  const undo = documentHistory().undo;
  expect(documentHistory().undoReason).toBeUndefined();
  workspaceModeStore.setImportedInput(input);
  workspaceModeStore.setMode('cad');
  const before = useDesignStore.getState().design;
  const history = useDesignStore.temporal.getState();
  expect(documentHistory().undoReason).toContain('imported models');
  expect(documentHistory().redoReason).toContain('imported models');
  undo(); // Even a previously captured palette action must dispatch again.
  documentHistory().redo();
  expect(useDesignStore.getState().design).toBe(before);
  expect(useDesignStore.temporal.getState()).toBe(history);
  workspaceModeStore.setMode('parametric');
  documentHistory().undo();
  expect(useDesignStore.getState().design.L).not.toBe(321);
  expect(documentHistory().redoReason).toBeUndefined();
  workspaceModeStore.setMode('cad');
  documentHistory().redo();
  expect(useDesignStore.getState().design.L).not.toBe(321);
  workspaceModeStore.setMode('parametric');
  documentHistory().redo();
  expect(useDesignStore.getState().design.L).toBe(321);
});
