import { SETTINGS_NAMESPACES, namespaceStorage } from '../stores/durableSettings';

const storage = namespaceStorage('sketchView');
export const sketchViewKey = SETTINGS_NAMESPACES.sketchView;
export const defaultSketchView = { dimensions: true, names: false, constraints: true };
export type SketchViewPreferences = typeof defaultSketchView;
export function readSketchView(): SketchViewPreferences {
  try {
    const value = JSON.parse(storage.getItem('') ?? 'null');
    return Object.fromEntries(Object.entries(defaultSketchView).map(([key, fallback]) => [key,
      typeof value?.[key] === 'boolean' ? value[key] : fallback])) as SketchViewPreferences;
  } catch { return { ...defaultSketchView }; }
}
export function saveSketchView(value: SketchViewPreferences) {
  try { storage.setItem('', JSON.stringify(value)); } catch { /* Viewing still works without storage. */ }
}
