import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { previewSocket } from '../api/previewSocket';
import { currentDocumentLoad, useDesignStore } from '../stores/design';

const readouts = [
  ['mouth_opening', 'Mouth opening', 2],
  ['horn_overall', 'Horn overall', 3],
  ['enclosure_overall', 'Enclosure overall', 3],
] as const;

function formatted(value: unknown, count: number): string | null {
  if (!Array.isArray(value) || value.length !== count
      || !value.every((item) => typeof item === 'number' && Number.isFinite(item) && item > 0)) return null;
  return `${value.map((item: number) => item.toFixed(1)).join(' × ')} mm`;
}

function formattedTarget(value: unknown): string | null {
  if (!Array.isArray(value) || value.length !== 2
      || !value.every((item) => typeof item === 'number' && Number.isFinite(item) && item >= 0)
      || value.every((item) => item === 0)) return null;
  return `${value.map((item: number) => item === 0 ? 'auto' : item.toFixed(1)).join(' × ')} mm`;
}

/** Read only the accepted frame; older meshers simply have no readouts. */
export function LiveDimensions() {
  const revision = useDesignStore((state) => state.designRevision);
  const preview = useSyncExternalStore(previewSocket.subscribe, previewSocket.getSnapshot, previewSocket.getSnapshot);
  const host = useRef<HTMLElement>(null);
  const [invalidDraft, setInvalidDraft] = useState(false);
  const frame = preview.dimensionsFrame ?? preview.frame;
  const metadata = frame?.header.previewMetadata;
  const supported = metadata !== undefined && 'dimensions_mm' in metadata;
  const ownsDocument = frame?.documentLoad === undefined || frame.documentLoad === currentDocumentLoad();
  const visible = supported && !preview.awaitingDocumentFrame
    && (ownsDocument || preview.dimensionsUnavailable);

  // NumberField keeps rejected drafts outside the design store. Observe its
  // public validity state so dimensions cannot look current beside an invalid
  // uncommitted input (including fields in the FREEFORM editors).
  useEffect(() => {
    const panel = host.current?.closest('.param-panel');
    if (!panel) return;
    const update = () => setInvalidDraft(Boolean(panel.querySelector('[aria-invalid="true"]')));
    const observer = new MutationObserver(update);
    observer.observe(panel, { subtree: true, childList: true, attributes: true, attributeFilter: ['aria-invalid'] });
    update();
    return () => observer.disconnect();
  }, [visible]);

  // After New/Open the retained frame is the previous document's: show nothing
  // until this document has a frame of its own.
  if (!visible) return null;
  const pending = !preview.dimensionsUnavailable && metadata!.dimensions_status === 'pending';
  const dimensions = preview.dimensionsUnavailable || metadata!.dimensions_status === 'unavailable' ? null
    : pending ? preview.lastCanonicalDimensions : metadata!.dimensions_mm;
  const current = preview.displayedRevision === revision
    && frame?.header.designRevision === revision
    && !pending && !preview.stale && !preview.error && !invalidDraft;
  return <section ref={host} className="live-dimensions" aria-label="Design dimensions">
    <h3>Design size</h3>
    <p>W × H × D · {pending ? 'Updating dimensions' : dimensions ? current ? 'Current preview' : 'Last valid preview' : 'Dimensions unavailable'}</p>
    <dl className="realized-dimension-list">
      {readouts.map(([key, label, count]) => {
        if (key === 'enclosure_overall' && dimensions && !(key in dimensions)) return null;
        const value = formatted(dimensions?.[key], count);
        const requested = key === 'mouth_opening' ? formattedTarget(metadata!.dimensions_requested_mm?.mouth_opening) : null;
        return <div className="realized-dimension-row" key={key}>
          <dt><span>{label}</span></dt>
          <dd>{value && requested && requested !== value
            ? <>requested {requested}, effective {value}</>
            : value ?? (pending ? 'updating' : 'unavailable')}{value && !current && <small>{pending ? 'updating' : 'last valid'}</small>}</dd>
        </div>;
      })}
    </dl>
  </section>;
}
