import { jsonRequest } from './cadlink';

/** A mirror plane of the exported CAD frame. */
export type DomainPlane = 'x0' | 'y0' | 'z0';

/** One reading the model card's Change offers. */
export type DomainReading =
  | { reading: 'as-shown' }
  | { reading: 'reduced'; planes: DomainPlane[] };

/**
 * How WG read an imported model's acoustic domain (PLAN.md M1c-auto,
 * `server/cadlink/domain_interpretation.py`), as the ingestion record states it.
 * `reading` is what was solved: the whole model (`full`, WG's own validated
 * mirror cuts included), a model already cut in CAD mirrored on recorded
 * evidence (`reduced`), or a model that looks cut solved unmirrored (`as-shown`).
 */
export interface DomainInterpretation {
  contract: string;
  manifest_domain?: 'absent' | 'automatic' | 'declared';
  reading: 'full' | 'reduced' | 'as-shown';
  /** The pre-cut planes mirrored because of the evidence (CAD frame). */
  planes: DomainPlane[];
  /** Planes WG cut itself after its mirror test. */
  wg_cut_planes?: string[];
  domain_planes?: string[];
  looks_cut: DomainPlane[];
  ambiguous: DomainPlane[];
  evidence: {
    source: 'declaration' | 'user' | 'user-lineage' | 'cad-provenance' | 'lineage' | null;
    features?: Array<{ plane: DomainPlane; kind: string; name: string }>;
    applied?: boolean;
    [key: string]: unknown;
  };
  choices: DomainReading[];
  [key: string]: unknown;
}

export interface DomainInterpretationView {
  ingestId: string;
  available: boolean;
  interpretation?: DomainInterpretation;
  remembered?: Record<string, unknown> | null;
  /** A Change the user made that the prepared model does not show yet. */
  pending?: DomainReading | null;
}

export function getDomainInterpretation(ingestId: string, fetcher: typeof fetch = fetch): Promise<DomainInterpretationView> {
  return jsonRequest(`/api/cadlink/domain-interpretation?ingestId=${encodeURIComponent(ingestId)}`, undefined, fetcher);
}

/** Change: remember the user's reading for the model's lineage. Solve prepares it again. */
export function changeDomainReading(
  ingestId: string,
  reading: DomainReading,
  fetcher: typeof fetch = fetch,
): Promise<DomainInterpretationView> {
  return jsonRequest('/api/cadlink/domain-interpretation', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ingestId, ...reading }),
  }, fetcher);
}
