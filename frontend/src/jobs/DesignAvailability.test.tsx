import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DesignAvailabilityNotice, RerunButton } from './DesignAvailability';
import type { JobDesignFields } from './jobDesign';

const STORED: JobDesignFields = {
  script_snapshot: { version: 1, design: { formula: 'OSSE', L: 120, a: 45, a0: 10, r0: 12.7, k: 1 } },
  design_availability: {
    reopenable: true,
    source: 'v2-snapshot',
    reason_code: 'ok',
    reason: null,
    note: null,
  },
};

const UNREADABLE: JobDesignFields = {
  script_snapshot: { params: { type: 'OSSE' } },
  design_availability: {
    reopenable: false,
    source: 'none',
    reason_code: 'unreadable_design',
    reason: "This job's stored design is not in a format this version can read back. Re-enter its profiles to run it again.",
    note: null,
  },
};

const NO_DESIGN: JobDesignFields = {
  script_snapshot: null,
  design_availability: {
    reopenable: false,
    source: 'none',
    reason_code: 'no_stored_design',
    reason: 'No design was stored with this job.',
    note: null,
  },
};

const CAD_IMPORT: JobDesignFields = {
  script_snapshot: null,
  design_availability: {
    reopenable: false,
    source: 'cad-import',
    reason_code: 'imported_geometry',
    reason: 'This immutable CAD return cannot be reopened. The ingestion anchor is registered design design-123.',
    note: null,
  },
};

describe('the design verdict a job card shows', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
  });

  const render = (node: React.ReactNode) => act(() => root.render(node));
  const button = () => host.querySelector('button') as HTMLButtonElement;
  const notice = () => host.querySelector('[role="note"]');

  it('runs a job with a stored design', () => {
    const onRerun = vi.fn();
    render(<RerunButton job={STORED} onRerun={onRerun}/>);
    expect(button().disabled).toBe(false);
    act(() => { button().click(); });
    expect(onRerun).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['a design this version cannot read back', UNREADABLE, 'not in a format'],
    ['a job that never stored a design', NO_DESIGN, 'No design was stored'],
  ])('refuses %s with the reason on the control itself', (_name, job, fragment) => {
    const onRerun = vi.fn();
    render(<RerunButton job={job} onRerun={onRerun}/>);
    expect(button().disabled).toBe(true);
    expect(button().title).toContain(fragment);
    act(() => { button().click(); });
    expect(onRerun).not.toHaveBeenCalled();
  });

  it('states the cause in the card, not only in a tooltip', () => {
    render(<DesignAvailabilityNotice job={UNREADABLE}/>);
    expect(notice()?.textContent).toContain('Re-enter its profiles');
    expect(getComputedStyle(notice()!).fontSize).toBe('11px');
  });

  it('stays quiet about a job with nothing to explain', () => {
    render(<DesignAvailabilityNotice job={STORED}/>);
    expect(notice()).toBeNull();
  });

  it('allows CAD replay while pointing at the linked design instead of reopening it', () => {
    const onRerun = vi.fn();
    render(<><RerunButton job={CAD_IMPORT} onRerun={onRerun}/><DesignAvailabilityNotice job={CAD_IMPORT}/></>);
    expect(button().disabled).toBe(false);
    expect(notice()?.textContent).toContain('design-123');
    act(() => button().click());
    expect(onRerun).toHaveBeenCalledOnce();
  });

  it('still refuses a job whose verdict the server did not send', () => {
    // An older server, or a replayed fixture: fall back to the snapshot rather
    // than trusting a field that is not there.
    render(<RerunButton job={{ script_snapshot: { params: { type: 'OSSE' } } }} onRerun={() => {}}/>);
    expect(button().disabled).toBe(true);
    expect(button().title).toContain('cannot be reopened');
  });
});
