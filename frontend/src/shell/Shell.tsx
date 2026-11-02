import { useEffect, useState } from 'react';
import { observeLivePreview } from '../api/previewSocket';
import { StatusBar } from './StatusBar';
import { TopBar } from './TopBar';
import { Workspace } from './Workspace';
import { CadWorkspaceOwners } from './CadWorkspaceOwners';
import { CadSetupPublisher } from './cadSetupPublisher';
import { CadDeliveryHealthDataSource } from './CadDeliveryHealth';
import { JobsCoordinator } from './JobsCoordinator';
import { useDocumentTitle } from './useDocumentTitle';

export function Shell() {
  const [resetKey, setResetKey] = useState(0);
  useDocumentTitle();
  useEffect(() => observeLivePreview(), []);
  return <><CadSetupPublisher/><CadDeliveryHealthDataSource/><CadWorkspaceOwners/><JobsCoordinator><div className="app-shell"><TopBar onResetLayout={() => setResetKey((value) => value + 1)}/><Workspace resetKey={resetKey}/><StatusBar/></div></JobsCoordinator></>;
}
