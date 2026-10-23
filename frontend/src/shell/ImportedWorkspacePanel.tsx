import { useSyncExternalStore } from 'react';
import { workspaceModeStore } from '../stores/workspaceMode';
import { useMeshImportStore } from '../stores/meshImport';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { noteExplicitNavigation, workspaceNavigation } from './workspaceNavigation';
import { CadOperationsSection } from './CadOperationsSection';
import { CadLinkPanel } from './CadLinkPanel';

/** One selected input; CAD connection controls remain specific to CAD Link. */
export function ImportedWorkspacePanel() {
  const { mode, importedInput } = useSyncExternalStore(workspaceModeStore.subscribe, workspaceModeStore.getSnapshot);
  const filename = useMeshImportStore(state => state.filename);
  if (mode === 'parametric') return <div className="imported-workspace"><CadOperationsSection record={null}/></div>;
  return <div className="imported-workspace">
    <div className="cad-section">
      <h3>Imported mesh</h3>
      <div role="group" aria-label="Imported mesh input" className="theme-toggle imported-input-toggle">
        {(['file', 'cad'] as const).map(input => <button key={input} type="button"
          aria-pressed={importedInput === input} className={importedInput === input ? 'on' : ''}
          onClick={() => {
            noteExplicitNavigation(); importedMeshStore.beginIntent(); workspaceModeStore.setImportedInput(input);
            if (input === 'file') importedMeshStore.showFile(); else importedMeshStore.showCad();
          }}>{input === 'file' ? 'File mesh' : 'CAD Link'}</button>)}
      </div>
    </div>
    {importedInput === 'file' ? <div className="cad-section">
      <h4>{filename || 'Import a mesh file'}</h4>
      <p>Use Import mesh above to open an ASCII Gmsh 2.2 surface mesh. CAD Link is optional.</p>
      <p>In Geometry, choose units and Radiating surfaces, then prepare the mesh.</p>
      <button onClick={() => workspaceNavigation.navigate('geometry')}>Open mesh setup</button>
      <CadOperationsSection record={null}/>
    </div> : <CadLinkPanel/>}
  </div>;
}
