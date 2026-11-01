import { useSyncExternalStore } from 'react';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { noteExplicitNavigation } from './workspaceNavigation';
import { CadOperationsSection } from './CadOperationsSection';
import { ParamPanel } from '../design/ParamPanel';
import { CadLinkPanel } from './CadLinkPanel';
import { useCadModelSelection } from '../stores/cadModelState';
import { cadWorkspace } from '../stores/cadWorkspace';

/** One selected input; CAD connection controls remain specific to CAD Link. */
export function ImportedWorkspacePanel() {
  const { mode, importedInput } = useSyncExternalStore(workspaceModeStore.subscribe, workspaceModeStore.getSnapshot);
  // A model's parameters have nothing to describe until a model is open.
  const model = useCadModelSelection();
  const parameters = mode !== 'cad' || importedInput === 'file' || model.selected;
  return <div className="imported-workspace panel-scroll">
    {mode === 'cad' && <>
    <div className="cad-section">
      <h3>Imported mesh</h3>
      <div role="group" aria-label="Imported mesh input" className="theme-toggle imported-input-toggle">
        {(['file', 'cad'] as const).map(input => <button key={input} type="button"
          aria-pressed={importedInput === input} className={importedInput === input ? 'on' : ''}
          onClick={() => {
            noteExplicitNavigation(); importedMeshStore.beginIntent(); workspaceModeStore.setImportedInput(input); cadWorkspace.getSnapshot().clearFeedback();
            if (input === 'file') importedMeshStore.showFile(); else importedMeshStore.showCad();
          }}>{input === 'file' ? 'File mesh' : 'CAD Link'}</button>)}
      </div>
    </div>
    {importedInput === 'cad' && <CadLinkPanel/>}
    </>}
    {parameters && <ParamPanel tab="geometry" embedded/>}
    {(mode === 'parametric' || importedInput === 'file') && <CadOperationsSection record={null}/>}
  </div>;
}
