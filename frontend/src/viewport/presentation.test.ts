import { describe, expect, it } from 'vitest';
import { previewBadge, previewErrorKey, previewErrorMessage, staleReason } from './presentation';

describe('preview presentation', () => {
  it('applies badge precedence from paused through live', () => {
    expect(previewBadge(false, 'disconnected', 'bad design', true)).toEqual({ label: 'PAUSED', className: 'paused-badge' });
    expect(previewBadge(true, 'reconnecting', 'bad design', true)).toEqual({ label: 'RECONNECTING', className: 'reconnect-badge' });
    expect(previewBadge(true, 'connected', 'bad design', true)).toEqual({ label: 'ERROR', className: 'error-badge' });
    expect(previewBadge(true, 'connected', null, true)).toEqual({ label: 'STALE', className: 'stale-badge' });
    expect(previewBadge(true, 'connected', null, false)).toBeNull();
  });

  it('makes retained geometry explicit in preview errors', () => {
    expect(previewErrorMessage('unsupported expression')).toBe(
      'WG could not show this edit: unsupported expression. The view still shows the last design that worked.',
    );
  });

  it('names the revision when the failure belongs to an edit already moved past', () => {
    expect(previewErrorMessage('bad morph', 57, 58)).toBe(
      'An earlier edit could not be shown: bad morph. The view still shows the last design that worked.',
    );
    expect(previewErrorMessage('bad morph', 58, 58)).toBe(
      'WG could not show this edit: bad morph. The view still shows the last design that worked.',
    );
  });

  it('explains a lagging viewport differently for each cause', () => {
    expect(staleReason(false, 'connected', null)).toContain('paused');
    expect(staleReason(true, 'reconnecting', null)).toContain('reconnecting');
    expect(staleReason(true, 'connected', 'boom')).toContain('could not show');
    expect(staleReason(true, 'connected', null)).toContain('still drawing');
  });
});

it('keys a dismissal on the failure, not its shared plain words', () => {
  const plain = 'WG could not update the preview and has recorded the problem.';
  expect(previewErrorKey({ error: plain, errorDetail: 'first failure' })).not.toBe(previewErrorKey({ error: plain, errorDetail: 'second failure' }));
  expect(previewErrorKey({ error: 'Self-intersecting mouth.', errorDetail: null })).toBe('Self-intersecting mouth.');
  expect(previewErrorKey({ error: null })).toBeNull();
});
