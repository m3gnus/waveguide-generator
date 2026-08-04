import { describe, expect, it } from 'vitest';
import { designForFamily } from '../stores/design';
import { previewBadge, previewErrorMessage, staleReason, viewportSubtitle } from './presentation';

describe('preview presentation', () => {
  it('applies badge precedence from paused through live', () => {
    expect(previewBadge(false, 'disconnected', 'bad design', true)).toEqual({ label: 'PAUSED', className: 'paused-badge' });
    expect(previewBadge(true, 'reconnecting', 'bad design', true)).toEqual({ label: 'RECONNECTING', className: 'reconnect-badge' });
    expect(previewBadge(true, 'connected', 'bad design', true)).toEqual({ label: 'ERROR', className: 'error-badge' });
    expect(previewBadge(true, 'connected', null, true)).toEqual({ label: 'STALE', className: 'stale-badge' });
    expect(previewBadge(true, 'connected', null, false)).toEqual({ label: 'LIVE', className: 'live-badge' });
  });

  it('makes retained geometry explicit in preview errors', () => {
    expect(previewErrorMessage('unsupported expression')).toBe(
      'Preview failed: unsupported expression. Displayed geometry is not the current design.',
    );
  });

  it('names the revision when the failure belongs to an edit already moved past', () => {
    expect(previewErrorMessage('bad morph', 57, 58)).toBe(
      'Preview failed at revision r57: bad morph. Displayed geometry is not the current design.',
    );
    expect(previewErrorMessage('bad morph', 58, 58)).toBe(
      'Preview failed: bad morph. Displayed geometry is not the current design.',
    );
  });

  it('explains a lagging viewport differently for each cause', () => {
    expect(staleReason(false, 'connected', null)).toContain('paused');
    expect(staleReason(true, 'reconnecting', null)).toContain('reconnecting');
    expect(staleReason(true, 'connected', 'boom')).toContain('failed');
    expect(staleReason(true, 'connected', null)).toContain('Waiting');
  });
});

describe('viewport subtitle', () => {
  it('uses the family mouth field and actual quadrant count', () => {
    const design = designForFamily('R-OSSE');
    design.R = 140;
    design.quadrants = [1, 2];
    expect(viewportSubtitle(design)).toBe('R-OSSE · Ø 280 mm · half');
  });

  it('omits a mouth dimension that OSSE does not provide', () => {
    const design = { ...designForFamily('OSSE'), R: 999, quadrants: [1, 2, 3] };
    expect(viewportSubtitle(design)).toBe('OSSE · 3/4');
    expect(viewportSubtitle(design)).not.toContain('999');
  });

  it('derives FREEFORM mouth dimensions only when both profiles provide them', () => {
    const design = designForFamily('FREEFORM');
    design.profile_h!.points.at(-1)!.r = 140;
    design.profile_v!.points.at(-1)!.r = 100;
    design.quadrants = [1];
    expect(viewportSubtitle(design)).toBe('FREEFORM · 280 × 200 mm · quarter');

    design.profile_v = undefined;
    expect(viewportSubtitle(design)).toBe('FREEFORM · quarter');
  });
});
