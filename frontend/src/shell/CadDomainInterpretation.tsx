import { useEffect, useRef, useState } from 'react';
import {
  changeDomainReading,
  getDomainInterpretation,
  type DomainInterpretation,
  type DomainReading,
} from '../api/domainInterpretation';

function planeWords(plane: string): string {
  return `${plane.charAt(0)} = 0`;
}

function joinPlanes(planes: readonly string[]): string {
  return planes.map(planeWords).join(' and ');
}

/** What evidence made WG mirror a model already cut in CAD, in words. */
function evidenceWords(interpretation: DomainInterpretation): string | null {
  const evidence = interpretation.evidence ?? { source: null };
  const names = (evidence.features ?? [])
    .filter((feature) => interpretation.planes.includes(feature.plane))
    .map((feature) => feature.name);
  switch (evidence.source) {
    case 'cad-provenance': return names.length ? names.join(', ') : 'recorded in Fusion';
    case 'lineage': return names.length ? `${names.join(', ')}, as before` : 'as before';
    case 'user': return 'your choice';
    case 'user-lineage': return 'your earlier choice';
    case 'declaration': return 'declared in Fusion';
    default: return null;
  }
}

/**
 * The model card's domain line (PLAN.md M1c-auto): what WG solves and why, in
 * one line. "Half model · cut at x = 0 (Split Body 3)", "Solved as shown ·
 * looks cut at x = 0", "Quarter model · …", "Full model · WG mirrors it at …".
 * `change` says whether the card offers Change: the readings the record offers.
 */
export function domainLine(interpretation: DomainInterpretation): { text: string; change: boolean } {
  const change = (interpretation.choices ?? []).length > 0;
  if (interpretation.reading === 'reduced') {
    const all = (interpretation.domain_planes ?? interpretation.planes).filter((plane) => plane === 'x0' || plane === 'y0');
    const kind = all.length >= 2 ? 'Quarter model' : 'Half model';
    const why = evidenceWords(interpretation);
    const extra = all.filter((plane) => !interpretation.planes.includes(plane as never));
    const cut = `cut at ${joinPlanes(interpretation.planes)}${why ? ` (${why})` : ''}`;
    return { text: [kind, cut + (extra.length ? `, mirrored at ${joinPlanes(extra)}` : '')].join(' · '), change };
  }
  if (interpretation.reading === 'as-shown') {
    const mine = interpretation.evidence?.source === 'user' || interpretation.evidence?.source === 'user-lineage';
    const looks = interpretation.looks_cut ?? [];
    return {
      text: [
        mine ? 'Solved as shown (your choice)' : 'Solved as shown',
        looks.length ? `looks cut at ${joinPlanes(looks)}` : null,
      ].filter(Boolean).join(' · '),
      change,
    };
  }
  const mirrored = (interpretation.wg_cut_planes ?? []).filter((plane) => plane === 'x0' || plane === 'y0');
  return { text: mirrored.length ? `Full model · WG mirrors it at ${joinPlanes(mirrored)}` : 'Full model', change };
}

/** A reading, as the Change control names it. */
export function readingWords(reading: DomainReading): string {
  if (reading.reading === 'as-shown') return 'Solve it as shown, unmirrored';
  return `${reading.planes.length >= 2 ? 'Quarter' : 'Half'} model, cut at ${joinPlanes(reading.planes)}`;
}

function sameReading(a: DomainReading | null | undefined, b: DomainReading): boolean {
  if (!a || a.reading !== b.reading) return false;
  return a.reading === 'as-shown' || JSON.stringify(a.planes) === JSON.stringify((b as { planes: string[] }).planes);
}

/**
 * The domain line on a CAD model's Solve card, with Change. Nothing here asks
 * and nothing here prepares: a Change is remembered for the model's project,
 * and Solve prepares the model again under it.
 */
export function CadDomainInterpretation({ ingestId, interpretation, fetcher = fetch }: {
  ingestId: string;
  interpretation: DomainInterpretation;
  fetcher?: typeof fetch;
}) {
  const [changing, setChanging] = useState(false);
  const [pending, setPending] = useState<DomainReading | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestGeneration = useRef(0);
  const renderedIngestId = useRef(ingestId);
  if (renderedIngestId.current !== ingestId) {
    renderedIngestId.current = ingestId;
    requestGeneration.current += 1;
  }
  useEffect(() => {
    let current = true;
    const generation = ++requestGeneration.current;
    setChanging(false);
    setPending(null);
    setSaving(false);
    setError(null);
    // A Change made earlier (in another window, say) that this preparation
    // does not show yet. Advisory: the line stands without it.
    void getDomainInterpretation(ingestId, fetcher)
      .then((view) => {
        if (current && generation === requestGeneration.current && view.ingestId === ingestId) {
          setPending(view.pending ?? null);
        }
      })
      .catch(() => undefined);
    return () => { current = false; };
  }, [fetcher, ingestId]);
  const { text, change } = domainLine(interpretation);
  const choose = (reading: DomainReading) => {
    const generation = ++requestGeneration.current;
    setSaving(true);
    setError(null);
    void changeDomainReading(ingestId, reading, fetcher)
      .then((view) => {
        if (generation === requestGeneration.current && view.ingestId === ingestId) {
          setPending(view.pending ?? null);
          setChanging(false);
        }
      })
      .catch((reason: unknown) => {
        if (generation === requestGeneration.current) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      })
      .finally(() => {
        if (generation === requestGeneration.current) setSaving(false);
      });
  };
  return <div className="cad-domain" data-domain-reading={interpretation.reading}>
    <p className="cad-domain-line">
      <span>{text}</span>
      {change && <>{' · '}<button
        className="link-button"
        data-action="change-domain"
        title="Read this model another way. Solve prepares it again; runs already solved keep their domain."
        onClick={() => setChanging(!changing)}
      >Change</button></>}
    </p>
    {changing && <div className="cad-domain-choices" role="group" aria-label="Solve this model as">
      {interpretation.choices.map((reading) => <button
        key={readingWords(reading)}
        className="link-button"
        data-action="choose-domain"
        disabled={saving}
        aria-pressed={sameReading(pending, reading)}
        onClick={() => choose(reading)}
      >{readingWords(reading)}</button>)}
    </div>}
    {pending && <p className="cad-detail cad-domain-pending" role="status">
      Solve prepares it again: {readingWords(pending).replace(/^Solve it /, '').toLowerCase()}.
    </p>}
    {error && <p className="cad-domain-error" role="alert">Could not change how this model is read: {error}</p>}
  </div>;
}
