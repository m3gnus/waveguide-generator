import { useEffect, useState } from 'react';
import { previewSocket } from '../api/previewSocket';
import { StatusBar } from './StatusBar';
import { TopBar } from './TopBar';
import { Workspace } from './Workspace';

export function Shell() {
  const [resetKey, setResetKey] = useState(0);
  useEffect(() => {
    previewSocket.start();
    return () => previewSocket.stop();
  }, []);
  return <div className="app-shell"><TopBar onResetLayout={() => setResetKey((value) => value + 1)}/><Workspace resetKey={resetKey}/><StatusBar/></div>;
}
