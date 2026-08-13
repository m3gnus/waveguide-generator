import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const preview = vi.hoisted(() => {
  const listeners = new Set<() => void>();
  const snapshot: {
    errorFields: Readonly<Record<string, string>> | null;
    errorRevision: number | null;
  } = { errorFields: null, errorRevision: null };
  return { listeners, snapshot };
});

vi.mock('../api/previewSocket', () => ({
  previewSocket: {
    getSnapshot: () => preview.snapshot,
    subscribe: (listener: () => void) => {
      preview.listeners.add(listener);
      return () => preview.listeners.delete(listener);
    },
  },
}));

import { resetDesignStore, useDesignStore } from '../stores/design';
import { workspaceModeStore } from '../stores/workspaceMode';
import { ParamPanel } from './ParamPanel';

describe('ParamPanel preview field errors', () => {
  let host: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    localStorage.clear();
    resetDesignStore();
    workspaceModeStore.setMode('parametric');
    preview.snapshot.errorFields = null;
    preview.snapshot.errorRevision = null;
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    queryClient.clear();
    preview.listeners.clear();
  });

  function render(): void {
    act(() => root.render(<QueryClientProvider client={queryClient}><ParamPanel tab="geometry" /></QueryClientProvider>));
  }

  function publish(fields: Record<string, string>, revision = useDesignStore.getState().designRevision): void {
    preview.snapshot.errorFields = fields;
    preview.snapshot.errorRevision = revision;
    act(() => preview.listeners.forEach((listener) => listener()));
  }

  it('routes a matching server key into its NumberField and clears it after an edit', () => {
    render();
    publish({ 'morph.corner_radius': 'Increase morphCorner to at least 15 mm.' });

    const row = host.querySelector<HTMLElement>('[data-parameter-id="morph.corner_radius"]')!;
    expect(row.textContent).toContain('Increase morphCorner to at least 15 mm.');
    expect(row.querySelector('input')?.getAttribute('aria-invalid')).toBe('true');

    const input = row.querySelector<HTMLInputElement>('input')!;
    act(() => {
      input.focus();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, '20');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    act(() => input.blur());
    expect(useDesignStore.getState().design.morph.corner_radius).toBe(20);
    expect(row.textContent).not.toContain('Increase morphCorner to at least 15 mm.');
  });

  it('matches schema-qualified paths without attaching unknown keys to a row', () => {
    render();
    publish({ 'design.root.morph.corner_radius': 'Qualified detail', future_field: 'Global detail' });
    expect(host.querySelector('[data-parameter-id="morph.corner_radius"]')?.textContent).toContain('Qualified detail');
    expect(host.textContent).not.toContain('Global detail');
  });
});
