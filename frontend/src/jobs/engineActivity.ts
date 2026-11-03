import { useEffect, useRef } from 'react';
import type { QueryClient } from '@tanstack/react-query';
import { activityStore, type ActivityHandle } from '../shell/activity';

interface Reader { busy: boolean; failed: boolean }
interface EngineActivity { readers: Map<symbol, Reader>; handle: ActivityHandle | null }
const clients = new WeakMap<QueryClient, EngineActivity>();

/** Capability consumers share one engine detection lifecycle, including the
 * pending server answers between polls. A panel remount never duplicates it. */
export function useEngineActivity(client: QueryClient, busy: boolean, failed: boolean): void {
  const id = useRef(Symbol('engine activity')).current;
  let shared = clients.get(client);
  if (!shared) {
    shared = { readers: new Map(), handle: null };
    clients.set(client, shared);
  }
  const state = shared;
  useEffect(() => () => {
    state.readers.delete(id);
    if (![...state.readers.values()].some(reader => reader.busy)) {
      state.handle?.end(false);
      state.handle = null;
    }
  }, [id, state]);
  useEffect(() => {
    state.readers.set(id, { busy, failed });
    const readers = [...state.readers.values()];
    if (readers.some(reader => reader.busy)) {
      state.handle ??= activityStore.start({ kind: 'engine-check', label: 'Checking solver engines', background: true });
    } else {
      state.handle?.end(!readers.some(reader => reader.failed));
      state.handle = null;
    }
  }, [busy, failed, id, state]);
}
