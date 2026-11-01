import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readsPlain } from '../api/httpError';
import { useCadConnectionStore } from '../stores/cadConnection';
import { useCadDeliveryHealthStore } from '../stores/cadDeliveryHealth';
import type { CadOperationSummary } from '../api/cadOperations';
import { fusionDeliveryWords, PICKUP_WAIT_MS, sentToCadMessage, watchFusionDelivery, type FusionDeliveryFacts } from './useSendToCad';

const operation = (fusionDelivery: CadOperationSummary['fusionDelivery'], kind = 'insert_link') => ({ kind, fusionDelivery });

describe('fusionDeliveryWords', () => {
  it.each<[string, FusionDeliveryFacts, string]>([
    ['waiting', {}, 'Waiting for Fusion to open this model.'],
    ['waiting update', { update: true }, 'Waiting for Fusion to update this model.'],
    ['claimed', { operation: operation('claimed') }, 'Fusion is opening this model.'],
    ['applied', { operation: operation('applied') }, 'Opened in Fusion.'],
    ['applied update', { operation: operation('applied', 'update_link') }, 'Updated in Fusion.'],
    ['closed', { fusion: { processRunning: false } }, 'Fusion is closed. Open Fusion and this model will arrive.'],
    ['busy', { waited: true, fusion: { processRunning: true, wgRequestPickup: 1 } },
      'Fusion has not confirmed this model yet. Finish any open command in Fusion.'],
    ['old add-in', { waited: true, fusion: { processRunning: true, addinDeliveryVersion: 3, wgRequestPickup: null } },
      'WGLink in Fusion cannot take this model. Restart Fusion to update WGLink, then send the model again.'],
    ['no report yet', { waited: true, fusion: { processRunning: true } },
      'Fusion has not confirmed this model yet. Check that Fusion has finished starting and WGLink is running.'],
    ['no status', { waited: true }, 'Fusion has not confirmed this model yet. Check that Fusion has finished starting and WGLink is running.'],
    ['cancelled', { operation: operation('cancelled') }, 'This send was replaced or is no longer needed. Nothing changed in Fusion.'],
    ['remedy', { waitingRemedy: 'Restart Fusion to install the WGLink update.' }, 'Restart Fusion to install the WGLink update.'],
  ])('%s', (_name, facts, text) => {
    expect(fusionDeliveryWords(facts)).toEqual({ tone: 'status', text });
  });

  it('points at Fusion for a refusal, never repeating its text', () => {
    expect(fusionDeliveryWords({ operation: operation('refused') })).toEqual({
      tone: 'error', text: 'Fusion could not open this model. Read the reason in Fusion, then send it again from WG.' });
  });

  it('treats expiry and an unknown result as something to act on', () => {
    expect(fusionDeliveryWords({ operation: operation('expired') }).tone).toBe('error');
    expect(fusionDeliveryWords({ operation: operation('uncertain') }).tone).toBe('error');
  });

  it('keeps every line plain', () => {
    for (const delivery of ['waiting', 'claimed', 'applied', 'refused', 'expired', 'uncertain'] as const) {
      expect(readsPlain(fusionDeliveryWords({ operation: operation(delivery) }).text)).toBe(true);
    }
  });

  it('tells the user how to finish a send no request was published for', () => {
    expect(sentToCadMessage({ bundlePath: '/x' } as Parameters<typeof sentToCadMessage>[0]))
      .toBe('The model is ready. In Fusion, choose WGLink → Advanced → Insert.');
  });
});

describe('watchFusionDelivery', () => {
  beforeEach(() => { vi.useFakeTimers(); useCadDeliveryHealthStore.setState({ outboundOperations: {} }); });
  afterEach(() => { vi.useRealTimers(); });

  it('follows the send and explains a long wait once', () => {
    useCadConnectionStore.setState({ fusionStatus: { processRunning: true, wgRequestPickup: 1 } as never });
    const seen: string[] = [];
    const stop = watchFusionDelivery({ requestId: 'r1' }, (words) => seen.push(words.text));
    vi.advanceTimersByTime(PICKUP_WAIT_MS);
    useCadDeliveryHealthStore.setState({ outboundOperations: { r1: operation('applied') as CadOperationSummary } });
    useCadConnectionStore.setState({ fusionStatus: null });
    stop();
    useCadDeliveryHealthStore.setState({ outboundOperations: {} });
    expect(seen).toEqual([
      'Waiting for Fusion to open this model.',
      'Fusion has not confirmed this model yet. Finish any open command in Fusion.',
      'Opened in Fusion.',
    ]);
  });

  it('keeps a finished answer after the health store forgets the operation', () => {
    useCadDeliveryHealthStore.setState({ outboundOperations: { r2: operation('applied') as CadOperationSummary } });
    const seen: string[] = [];
    const stop = watchFusionDelivery({ requestId: 'r2' }, (words) => seen.push(words.text));
    useCadDeliveryHealthStore.setState({ outboundOperations: {} });
    vi.advanceTimersByTime(PICKUP_WAIT_MS);
    stop();
    expect(seen).toEqual(['Opened in Fusion.']);
  });
});
