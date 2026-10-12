import { useEffect, useState } from 'react';
import { fetchFemPlane, fetchFemField, type DecodedFemField } from '../api/femField';

const SELECTION_IDLE_MS = 250;

/** Abort and clear obsolete selections before a new run/frequency can display. */
export function useFemField(jobId: string | null) {
  const [selection, setSelection] = useState<{ jobId: string; frequency?: number; channel?: string } | null>(null);
  const [loaded, setLoaded] = useState<{ jobId: string; field: DecodedFemField } | null>(null);
  // Axis metadata survives a frequency request so the slider stays mounted.
  const [axes, setAxes] = useState<{ jobId: string; frequencies: number[]; channels: string[]; channel: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [retry, setRetry] = useState(0);
  const frequency = selection?.jobId === jobId ? selection.frequency : undefined;
  const channel = selection?.jobId === jobId ? selection.channel : undefined;
  useEffect(() => {
    if (!jobId) {
      setLoaded(null);
      setAxes(null);
      setSelection(null);
      setError(null);
      setLoading(false);
      return;
    }
    setLoaded(null);
    setError(null);
    setLoading(true);
    const controller = new AbortController();
    const load = () => void fetchFemField(jobId, frequency, channel, controller.signal).then((field) => {
      if (!controller.signal.aborted) { setLoaded({ jobId, field }); setLoading(false); }
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) {
        setError(reason instanceof Error ? reason.message : 'FEM field could not be loaded');
        setLoading(false);
      }
    });
    // Aborting a browser request cannot cancel server decompression. Wait for
    // the selection to settle before starting that work; load new jobs at once.
    const timer = frequency !== undefined || channel !== undefined
      ? window.setTimeout(load, SELECTION_IDLE_MS) : null;
    if (timer === null) load();
    return () => {
      if (timer !== null) window.clearTimeout(timer);
      controller.abort();
    };
  }, [jobId, frequency, channel, retry]);
  useEffect(() => {
    if (loaded) setAxes({ jobId: loaded.jobId, frequencies: loaded.field.payload.frequencies_hz,
      channels: loaded.field.payload.channel_ids, channel: loaded.field.payload.channel_id });
  }, [loaded]);
  const currentAxes = axes?.jobId === jobId ? axes : null;
  const field = loaded?.jobId === jobId
    && (frequency === undefined || loaded.field.frequencyHz === frequency)
    && (channel === undefined || loaded.field.payload.channel_id === channel) ? loaded.field : null;
  return {
    field, error: jobId ? error : null, loading: jobId !== null && loading,
    frequencies: currentAxes?.frequencies ?? [], channels: currentAxes?.channels ?? [],
    frequencyIndex: Math.max(0, currentAxes?.frequencies.indexOf(frequency ?? field?.frequencyHz ?? 0) ?? 0),
    channel: channel ?? field?.payload.channel_id ?? currentAxes?.channel ?? '',
    setFrequencyIndex: (index: number) => {
      if (jobId && currentAxes?.frequencies[index] !== undefined)
        setSelection({ jobId, frequency: currentAxes.frequencies[index], channel });
    },
    setChannel: (next: string) => { if (jobId) setSelection({ jobId, frequency, channel: next }); },
    retry: () => setRetry((value) => value + 1),
  };
}

/** Clear obsolete planes immediately, and debounce post-solve evaluations. */
export function useFemPlane(jobId: string | null, field: DecodedFemField | null,
  plane: import('../api/fieldPlane').FieldPlaneSpec | null,
  scope: import('../api/femField').FemPlaneScope, quantity: import('../api/femField').FemPlaneQuantity) {
  const key = jobId && field && plane && scope !== 'interior'
    ? JSON.stringify([jobId, field.frequencyHz, field.payload.channel_id, plane, scope, quantity]) : null;
  const [loaded, setLoaded] = useState<{ key: string; value: import('../api/femField').FemPlaneField } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    setError(null); setLoaded(null); setLoading(!!key);
    if (!key || !jobId || !field || !plane) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void fetchFemPlane(jobId, plane, field.frequencyHz, field.payload.channel_id, scope === 'combined' ? 'exterior' : scope, quantity, controller.signal)
        .then((value) => { if (!controller.signal.aborted) { setLoaded({ key, value }); setLoading(false); } })
        .catch((reason: unknown) => { if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : 'Coupled plane could not be loaded'); setLoading(false);
        } });
    }, SELECTION_IDLE_MS);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [key, retry]);
  return { field: loaded?.key === key ? loaded?.value : null, error, loading,
    retry: () => setRetry((v) => v + 1) };
}
