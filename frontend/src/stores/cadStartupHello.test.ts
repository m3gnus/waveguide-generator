import { describe, expect, it, vi } from 'vitest';
import type { JobsSnapshot } from '../api/jobsSocket';
import { cadStartupHello } from './cadStartupHello';

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
function socket(connection: JobsSnapshot['connection']) {
  const listeners = new Set<() => void>();
  let state = connection;
  return {
    getSnapshot: () => ({ connection: state }),
    subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    move(next: JobsSnapshot['connection']) { state = next; listeners.forEach((listener) => listener()); },
    listeners,
  };
}

describe('cadStartupHello', () => {
  it('an answered mount read answers the first hello of a starting socket, and only that one', async () => {
    const manager = socket('connecting');
    const startup = cadStartupHello(manager);
    startup.mountRead(Promise.resolve(), vi.fn()); await settle();
    manager.move('connected');
    expect(startup.resync()).toBe(false);
    expect(startup.resync()).toBe(true);
    expect(manager.listeners.size).toBe(0);
  });

  it('re-runs a mount read that fails after it answered the first hello (P2-1)', async () => {
    const startup = cadStartupHello(socket('connecting'));
    let fail!: (error: Error) => void;
    const retry = vi.fn();
    startup.mountRead(new Promise((_, reject) => { fail = reject; }), retry);
    expect(startup.resync()).toBe(false);
    fail(new Error('offline')); await settle();
    expect(retry).toHaveBeenCalledOnce();
  });

  it('a failed mount read leaves the first hello to read, with no extra retry', async () => {
    const startup = cadStartupHello(socket('connecting'));
    const retry = vi.fn();
    startup.mountRead(Promise.reject(new Error('offline')), retry); await settle();
    expect(startup.resync()).toBe(true);
    expect(retry).not.toHaveBeenCalled();
  });

  it('a socket that drops before its first hello reads on the reconnect hello (P2-2)', async () => {
    const manager = socket('connecting');
    const startup = cadStartupHello(manager);
    startup.mountRead(Promise.resolve(), vi.fn()); await settle();
    manager.move('reconnecting'); manager.move('connecting'); manager.move('connected');
    expect(startup.resync()).toBe(true);
    expect(manager.listeners.size).toBe(0);
  });

  it.each(['connected', 'reconnecting', 'disconnected'] as const)('reads on every hello when the socket was %s at mount', async (connection) => {
    const startup = cadStartupHello(socket(connection));
    startup.mountRead(Promise.resolve(), vi.fn()); await settle();
    expect(startup.resync()).toBe(true);
  });

  it('reads on every hello when the manager cannot report its state (P3-5)', async () => {
    const startup = cadStartupHello({});
    startup.mountRead(Promise.resolve(), vi.fn()); await settle();
    expect(startup.resync()).toBe(true);
  });

  it('dispose stops watching and drops an owed retry', async () => {
    const manager = socket('connecting');
    const startup = cadStartupHello(manager);
    let fail!: (error: Error) => void;
    const retry = vi.fn();
    startup.mountRead(new Promise((_, reject) => { fail = reject; }), retry);
    expect(startup.resync()).toBe(false);
    startup.dispose(); fail(new Error('offline')); await settle();
    expect(retry).not.toHaveBeenCalled();
    expect(manager.listeners.size).toBe(0);
  });
});
