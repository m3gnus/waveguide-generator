import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DesignAvailabilityNotice, RerunButton } from './DesignAvailability';
import type { JobDesignFields } from './jobDesign';

const RECOVERED: JobDesignFields = {
  script_snapshot: { version: 1, design: { formula: 'OSSE', L: 120, a: 45, a0: 10, r0: 12.7, k: 1 } },
  design_availability: {
    reopenable: true,
    source: 'v1-design-state',
    reason_code: 'recovered',
    reason: null,
    note: null,
  },
};

const FROM_PAYLOAD: JobDesignFields = {
  ...RECOVERED,
  design_availability: {
    reopenable: true,
    source: 'v1-mesher-payload',
    reason_code: 'recovered',
    reason: null,
    note: 'Recovered from the mesher payload this job was solved with.',
  },
};

const FREEFORM: JobDesignFields = {
  script_snapshot: { params: { type: 'FREEFORM' } },
  design_availability: {
    reopenable: false,
    source: 'none',
    reason_code: 'freeform_legacy_design',
    reason: "This job's FREEFORM design was stored in the v1 format. Re-enter its profiles to run it again.",
    note: null,
  },
};

const NO_DESIGN: JobDesignFields = {
  script_snapshot: null,
  design_availability: {
    reopenable: false,
    source: 'none',
    reason_code: 'no_stored_design',
    reason: 'This job predates design snapshots in v1, and no design was stored with it.',
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

  it('runs a recovered v1 job exactly like a native one', () => {
    const onRerun = vi.fn();
    render(<RerunButton job={RECOVERED} onRerun={onRerun}/>);
    expect(button().disabled).toBe(false);
    act(() => { button().click(); });
    expect(onRerun).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['a FREEFORM design v2 cannot translate', FREEFORM, 'FREEFORM'],
    ['a job that never stored a design', NO_DESIGN, 'predates design snapshots'],
  ])('refuses %s with the reason on the control itself', (_name, job, fragment) => {
    const onRerun = vi.fn();
    render(<RerunButton job={job} onRerun={onRerun}/>);
    expect(button().disabled).toBe(true);
    expect(button().title).toContain(fragment);
    act(() => { button().click(); });
    expect(onRerun).not.toHaveBeenCalled();
  });

  it('states the cause in the card, not only in a tooltip', () => {
    render(<DesignAvailabilityNotice job={FREEFORM}/>);
    expect(notice()?.textContent).toContain('Re-enter its profiles');
  });

  it('says what a payload-recovered design is missing, without blocking it', () => {
    render(<><RerunButton job={FROM_PAYLOAD} onRerun={() => {}}/><DesignAvailabilityNotice job={FROM_PAYLOAD}/></>);
    expect(button().disabled).toBe(false);
    expect(notice()?.textContent).toContain('mesher payload');
  });

  it('stays quiet about a job with nothing to explain', () => {
    render(<DesignAvailabilityNotice job={RECOVERED}/>);
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
