import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { reportCaughtError, reportInterfaceError, resetClientErrorReporting, startClientErrorReporting } from './clientErrors';

describe('reportInterfaceError', () => {
  beforeEach(() => resetClientErrorReporting());
  afterEach(() => resetClientErrorReporting());

  it('reports an error once and ignores the repeat', () => {
    const report = vi.fn(async () => true);
    expect(reportInterfaceError({ message: 'boom', source: 'window' }, report)).toBe(true);
    expect(reportInterfaceError({ message: 'boom', source: 'window' }, report)).toBe(false);
    expect(report).toHaveBeenCalledTimes(1);
  });

  it('treats the same message from a different origin as its own report', () => {
    const report = vi.fn(async () => true);
    reportInterfaceError({ message: 'boom', source: 'window' }, report);
    expect(reportInterfaceError({ message: 'boom', source: 'render' }, report)).toBe(true);
  });

  it('stops after a session limit, so a render loop cannot spend the session posting', () => {
    const report = vi.fn(async () => true);
    for (let index = 0; index < 200; index += 1) {
      reportInterfaceError({ message: `boom ${index}`, source: 'window' }, report);
    }
    expect(report.mock.calls.length).toBeLessThanOrEqual(20);
  });

  it('ignores an empty message rather than posting a blank report', () => {
    const report = vi.fn(async () => true);
    expect(reportInterfaceError({ message: '   ', source: 'window' }, report)).toBe(false);
    expect(report).not.toHaveBeenCalled();
  });

  it('carries the stack when there is one', () => {
    const sent: { message: string; stack?: string; source?: string; at?: string }[] = [];
    const report = vi.fn(async (entry: { message: string; stack?: string; source?: string; at?: string }) => {
      sent.push(entry);
      return true;
    });
    reportInterfaceError({ message: 'boom', stack: 'at solve()', source: 'render' }, report);
    expect(sent[0]).toMatchObject({ message: 'boom', stack: 'at solve()', source: 'render' });
    expect(sent[0].at).toBeTruthy();
  });
});

describe('reportCaughtError', () => {
  beforeEach(() => resetClientErrorReporting());
  afterEach(() => resetClientErrorReporting());

  it('carries the operation, its context and the error stack', () => {
    const report = vi.fn(async () => true);
    const error = new Error('disk full');
    expect(reportCaughtError('run-archive', error, { job_id: 'job-1' }, report)).toBe(true);
    expect(report).toHaveBeenCalledWith(expect.objectContaining({
      message: 'run-archive: disk full', stack: error.stack, source: 'caught',
      operation: 'run-archive', context: { job_id: 'job-1' },
    }));
  });

  it('gives a thrown non-Error the stack of the call that caught it', () => {
    const report = vi.fn(async () => true);
    reportCaughtError('auto-export', 'plain string', undefined, report);
    const [[entry]] = report.mock.calls as unknown as [[{ message: string; stack?: string }]];
    expect(entry.message).toBe('auto-export: plain string');
    expect(entry.stack).toContain('reportCaughtError');
  });

  it('reports the same failure of another operation separately, and a repeat once', () => {
    const report = vi.fn(async () => true);
    reportCaughtError('export-build', new Error('boom'), undefined, report);
    reportCaughtError('export-build', new Error('boom'), undefined, report);
    reportCaughtError('export-write', new Error('boom'), undefined, report);
    expect(report).toHaveBeenCalledTimes(2);
  });
});

describe('startClientErrorReporting', () => {
  beforeEach(() => resetClientErrorReporting());

  it('detaches both handlers when stopped', () => {
    const added: string[] = [];
    const removed: string[] = [];
    const target = {
      addEventListener: (name: string) => { added.push(name); },
      removeEventListener: (name: string) => { removed.push(name); },
    } as unknown as Window;

    const stop = startClientErrorReporting(target);
    expect(added).toEqual(['error', 'unhandledrejection']);
    stop();
    expect(removed).toEqual(['error', 'unhandledrejection']);
  });
});
