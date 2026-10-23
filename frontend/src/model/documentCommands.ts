import { currentModel, type CurrentModel } from './currentModel';
import { useDesignStore } from '../stores/design';

/** Imported settings have no history yet; never dispatch into a parked design. */
export function documentHistory(model: CurrentModel = currentModel()) {
  const temporal = useDesignStore.temporal.getState();
  const importedReason = model.kind !== 'parametric' ? 'Undo and Redo are not available for imported models.' : undefined;
  return {
    undoReason: importedReason ?? (temporal.pastStates.length || useDesignStore.getState().dragSnapshot ? undefined : 'Nothing to undo.'),
    redoReason: importedReason ?? (temporal.futureStates.length ? undefined : 'Nothing to redo.'),
    undo: () => { if (!documentHistory().undoReason) useDesignStore.getState().undo(); },
    redo: () => { if (!documentHistory().redoReason) useDesignStore.getState().redo(); },
  };
}
