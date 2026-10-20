import type { SourceDocument } from '../../api/sourceEditor';
import type { Authoring } from '../controller';
import { parseSketch } from '../model';
import { contextData } from '../context';
import { lower, movingWeights, type SourceMeta } from './sourceContour';

export const SOURCE_AUTHORING_VERSIONS = ['wg-source-authoring-v1', 'wg-source-authoring-v2'];

/** Keep unknown authoring formats opaque, including newer sketches in known envelopes. */
export function newerEnvelope(raw: unknown): Record<string, unknown> | undefined {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return;
  const envelope = raw as Record<string, unknown>;
  if (typeof envelope.version === 'string' && envelope.version.startsWith('wg-source-authoring-') && !SOURCE_AUTHORING_VERSIONS.includes(envelope.version)) return envelope;
  const sketch = envelope.sketch as Record<string, unknown> | undefined;
  if (sketch && !parseSketch(sketch).ok && sketch.version !== 'wg-sketch-v1') return envelope;
}

export function sameSourceDocument(a: SourceDocument, b: SourceDocument) {
  const canonical = (value: unknown): string => JSON.stringify(value, (_, v: unknown) => v && typeof v === 'object' && !Array.isArray(v)
    ? Object.fromEntries(Object.entries(v).sort(([a], [b]) => a.localeCompare(b))) : v);
  return canonical(a) === canonical(b);
}
export function parseSourceAuthoring(raw: unknown, document: SourceDocument): Authoring<SourceMeta> | null {
  try {
    const envelope = raw as Record<string, unknown> | null;
    if (!envelope || typeof envelope.version !== 'string' || !SOURCE_AUTHORING_VERSIONS.includes(envelope.version)) return null;
    const parsed = parseSketch(envelope.sketch), meta = envelope.meta as SourceMeta;
    if (!parsed.ok || !meta || !meta.weights || !Object.values(meta.weights).every(Number.isFinite)
      || !Array.isArray(meta.references) || ['axis', 'baffle'].some(kind => meta.references.filter(r => r.kind === kind).length !== 1)
      || new Set(meta.references.map(r => r.kind)).size !== meta.references.length
      || !meta.references.every(r => ['axis', 'baffle', 'limit'].includes(r.kind) && parsed.sketch.entities.some(e => e.id === r.id && e.reference && e.construction && e.kind === 'line'))
      || ![meta.physical_source_id, meta.rim_id, meta.channel_id].every(v => typeof v === 'string') || !['normal', 'axial'].includes(meta.motion)) return null;
    if (parsed.sketch.entities.some(e => e.reference && (!e.construction || e.kind !== 'line' || !meta.references.some(r => r.id === e.id) && !contextData(e)))) return null;
    if (meta.preset && (!['flat', 'dome', 'cone'].includes(meta.preset.shape) || !meta.preset.dimensions
      || meta.preset.values !== undefined && (!meta.preset.values || typeof meta.preset.values !== 'object'
        || Array.isArray(meta.preset.values) || !Object.values(meta.preset.values).every(Number.isFinite))
      || typeof meta.preset.dimensions !== 'object' || Array.isArray(meta.preset.dimensions)
      || !Object.values(meta.preset.dimensions).every(id => typeof id === 'string'))) return null;
    const lowered = lower(parsed.sketch, meta);
    const matches = envelope.pending === true ? sameSourceDocument(envelope.document as SourceDocument, document)
      : lowered.ok && sameSourceDocument(lowered.document, document);
    return matches
      ? { sketch: parsed.sketch, meta: { ...meta, weights: movingWeights(parsed.sketch, meta.weights) } } : null;
  } catch { return null; }
}

/** Live host context is rebuilt on open; only authored geometry and provenance persist. */
export function serializeSourceAuthoring(state: Authoring<SourceMeta>, pending = false) {
  const { clearances: _clearances, missingContext: _missing, ...meta } = state.meta;
  return { version: 'wg-source-authoring-v2', sketch: state.sketch, meta, pending };
}
