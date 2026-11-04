// Shared by the rail launcher and every Custom profile draft recovery action.
export const profileCanvasVisibility = {
  open: false,
  listeners: new Set<() => void>(),
  getSnapshot: () => profileCanvasVisibility.open,
  subscribe: (listener: () => void) => { profileCanvasVisibility.listeners.add(listener); return () => { profileCanvasVisibility.listeners.delete(listener); }; },
  set(open: boolean) { profileCanvasVisibility.open = open; profileCanvasVisibility.listeners.forEach(listener => listener()); },
};

export function openProfileCanvas() { profileCanvasVisibility.set(true); }
export function closeProfileCanvas() { profileCanvasVisibility.set(false); }
