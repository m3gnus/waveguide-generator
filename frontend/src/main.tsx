import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { durableSettings, startDurableSettings } from './stores/durableSettings';
import './styles/app.css';

/**
 * Settings are read from the server before anything that depends on them loads.
 *
 * Stores seed themselves from durable settings during module evaluation, which
 * is what keeps the first paint free of a flash of defaults. That only works if
 * the server's copy has already replaced the browser cache, so `App` and every
 * store below it are imported dynamically after hydration rather than at the
 * top of this file. The timeout keeps a wedged backend from turning a slow
 * start into no start: the cached copy carries that session instead.
 */
async function start(): Promise<void> {
  await durableSettings.hydrate({ timeoutMs: 1_500 });
  startDurableSettings();

  const [{ default: App }, { restoreAutosaveWithLateRetry, startAutosave }] = await Promise.all([
    import('./App'),
    import('./stores/autosave'),
  ]);

  const stopLateAutosaveRestore = restoreAutosaveWithLateRetry();
  const autosave = startAutosave();
  if (import.meta.hot) import.meta.hot.dispose(() => {
    stopLateAutosaveRestore();
    autosave.dispose();
  });

  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

void start();
