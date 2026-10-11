import { preferencesStore } from './preferences';

export interface ChartTheme {
  id: string;
  label: string;
  default?: boolean;
}

export async function loadChartThemes(signal?: AbortSignal): Promise<{ themes: ChartTheme[]; defaultId?: string }> {
  try {
    const response = await fetch('/api/themes', { signal });
    if (!response.ok) return { themes: [] };
    const body = await response.json() as { themes?: Array<string | { id?: string; name?: string; label?: string; default?: boolean }>; default?: string };
    const themes = (body.themes ?? []).map((item): ChartTheme => {
      if (typeof item === 'string') return { id: item, label: item };
      const id = item.id ?? item.name ?? '';
      return { id, label: item.label ?? id, default: item.default };
    }).filter((item) => item.id);
    const requestedDefault = body.default ?? themes.find((item) => item.default)?.id;
    const defaultId = themes.some((item) => item.id === requestedDefault) ? requestedDefault : undefined;
    return { themes, defaultId };
  } catch { return { themes: [] }; }
}

/** Resolve the default before charts and exports mount; a slow server keeps Match interface. */
export async function initializeChartThemeDefault(timeoutMs = 1_500): Promise<void> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const result = await Promise.race([
      loadChartThemes(controller.signal),
      new Promise<null>((resolve) => { timer = setTimeout(() => resolve(null), timeoutMs); }),
    ]);
    if (result?.defaultId) preferencesStore.setChartThemeDefault(result.defaultId);
  } finally {
    clearTimeout(timer);
    controller.abort();
  }
}
