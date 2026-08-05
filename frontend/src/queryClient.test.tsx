import { useQueryClient } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { appQueryClient, AppQueryProvider } from './queryClient';

describe('AppQueryProvider', () => {
  const hosts: HTMLDivElement[] = [];
  const roots: Root[] = [];

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    appQueryClient.clear();
  });

  afterEach(() => {
    act(() => roots.splice(0).forEach((root) => root.unmount()));
    hosts.splice(0).forEach((host) => host.remove());
  });

  it('reuses one query cache across independent React roots', () => {
    const observed: unknown[] = [];
    function Probe() {
      observed.push(useQueryClient());
      return null;
    }

    act(() => {
      for (let index = 0; index < 2; index += 1) {
        const host = document.createElement('div');
        document.body.append(host);
        hosts.push(host);
        const root = createRoot(host);
        roots.push(root);
        root.render(<AppQueryProvider><Probe/></AppQueryProvider>);
      }
    });

    expect(observed).toEqual([appQueryClient, appQueryClient]);
  });
});
