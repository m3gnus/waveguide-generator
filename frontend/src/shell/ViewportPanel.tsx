import { useEffect } from 'react';
import { previewSocket } from '../api/previewSocket';
import { viewerPreferences } from '../viewerprefs/viewerPreferences';
import { Viewport } from '../viewport/Viewport';

export function ViewportPanel() {
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
    };
  }, []);
  return <Viewport />;
}
