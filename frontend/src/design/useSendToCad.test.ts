import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readsPlain } from '../api/httpError';
import { useCadConnectionStore } from '../stores/cadConnection';
import { useCadDeliveryHealthStore } from '../stores/cadDeliveryHealth';
import type { CadOperationSummary } from '../api/cadOperations';
import { fusionDeliveryWords, PICKUP_WAIT_MS, sentToCadMessage, watchFusionDelivery, type FusionDeliveryFacts } from './useSendToCad';

const operation = (fusionDelivery: CadOperationSummary['fusionDelivery'], kind = 'insert_link', message: string | null = null) =>
  ({ kind, fusionDelivery, message });

describe('fusionDeliveryWords', () => {
  it.each<[string, FusionDeliveryFacts, string]>([
    ['waiting', {}, 'Waiting for Fusion to open this model.'],
    ['waiting update', { update: true }, 'Waiting for Fusion to update this model.'],
    ['claimed', { operation: operation('claimed') }, 'Fusion is opening this model.'],
    ['applied', { operation: operation('applied') }, 'Opened in Fusion.'],
    ['applied update', { operation: operation('applied', 'update_link') }, 'Updated in Fusion.'],
    ['closed', { fusion: { processRunning: false } }, 'Fusion is closed. Open Fusion and this model will arrive.'],
    ['busy', { waited: true, fusion: { processRunning: true, wgRequestPickup: 1 } },
      'Fusion has not started on this model. Finish any open command in Fusion and it will open.'],
    ['old add-in', { waited: true, fusion: { processRunning: true, wgRequestPickup: null } },
      'WGLink in Fusion cannot take this model. Restart Fusion to update WGLink, then send the model again.'],
    ['remedy', { waitingRemedy: 'Restart Fusion to install the WGLink update.' }, 'Restart Fusion to install the WGLink update.'],
  ])('%s', (_name, facts, text) => {
    expect(fusionDeliveryWords(facts)).toEqual({ tone: 'status', text });
  });

  it('never shows raw add-in text for a refusal', () => {
    const raw = fusionDeliveryWords({ operation: operation('refused', 'insert_link', 'KeyError: entity_token in options[0]') });
    expect(raw).toEqual({ tone: 'error', text: 'Fusion could not open this model. Send it again from WG.' });
    const plain = 'This model is already in the open document. Update it instead.';
    expect(fusionDeliveryWords({ operation: operation('refused', 'insert_link', plain) })?.text).toBe(plain);
  });

  it('treats expiry and an unknown result as something to act on, and stays quiet when replaced', () => {
    expect(fusionDeliveryWords({ operation: operation('expired') })?.tone).toBe('error');
    expect(fusionDeliveryWords({ operation: operation('uncertain') })?.tone).toBe('error');
    expect(fusionDeliveryWords({ operation: operation('cancelled') })).toBeNull();
  });

  it('keeps every line plain', () => {
    for (const delivery of ['waiting', 'claimed', 'applied', 'refused', 'expired', 'uncertain'] as const) {
      expect(readsPlain(fusionDeliveryWords({ operation: operation(delivery) })!.text)).toBe(true);
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
      'Fusion has not started on this model. Finish any open command in Fusion and it will open.',
      'Opened in Fusion.',
    ]);
  });
});
