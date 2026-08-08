import { useEffect, useState } from 'react';
import { previewSocket } from '../api/previewSocket';
import { viewerPreferences } from '../viewerprefs/viewerPreferences';
import { StatusBar } from './StatusBar';
import { TopBar } from './TopBar';
import { Workspace } from './Workspace';
import { JobsCoordinator } from './JobsCoordinator';

export function Shell() {
  const [resetKey, setResetKey] = useState(0);
  useEffect(() => {
    let active = true;
    const applyLiveUpdateGate = () => queueMicrotask(() => {
      if (!active) return;
      if (viewerPreferences.getSnapshot().liveUpdate) previewSocket.start();
      else previewSocket.stop();
    });
    applyLiveUpdateGate();
    const unsubscribe = viewerPreferences.subscribe(applyLiveUpdateGate);
    return () => {
      active = false;
      unsubscribe();
      previewSocket.stop();
    };
  }, []);
  return <JobsCoordinator><div className="app-shell"><TopBar onResetLayout={() => setResetKey((value) => value + 1)}/><Workspace resetKey={resetKey}/><StatusBar/></div></JobsCoordinator>;
}
