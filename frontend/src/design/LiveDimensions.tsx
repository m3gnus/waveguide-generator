import { parameterPanelValidity } from './parameterPanelValidity';
import { Fragment, useSyncExternalStore } from 'react';
import { previewSocket } from '../api/previewSocket';
import { workspaceModeStore } from '../stores/workspaceMode';
import { currentDocumentLoad, useDesignStore } from '../stores/design';

function formatted(value: unknown, count: number): string | null {
  if (!Array.isArray(value) || value.length !== count
      || !value.every((item) => typeof item === 'number' && Number.isFinite(item) && item > 0)) return null;
  return value.map((item: number) => item.toFixed(1)).join(' × ');
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
  const formula = useDesignStore((state) => state.design.formula);
  const preview = useSyncExternalStore(previewSocket.subscribe, previewSocket.getSnapshot, previewSocket.getSnapshot);
  const mode = useSyncExternalStore(workspaceModeStore.subscribe, workspaceModeStore.getSnapshot, workspaceModeStore.getSnapshot).mode;
  const invalidDraft = useSyncExternalStore(parameterPanelValidity.subscribe, parameterPanelValidity.getSnapshot, parameterPanelValidity.getSnapshot);
  const frame = preview.dimensionsFrame ?? preview.frame;
  const metadata = frame?.header.previewMetadata;
  const supported = metadata !== undefined && 'dimensions_mm' in metadata;
  const ownsDocument = frame?.documentLoad === undefined || frame.documentLoad === currentDocumentLoad();
  const visible = supported && !preview.awaitingDocumentFrame
    && (ownsDocument || preview.dimensionsUnavailable);

  // After New/Open the retained frame is the previous document's: show nothing
  // until this document has a frame of its own.
  if (!visible || mode !== 'parametric') return null;
  const pending = !preview.dimensionsUnavailable && metadata!.dimensions_status === 'pending';
  const dimensions = preview.dimensionsUnavailable || metadata!.dimensions_status === 'unavailable' ? null
    : pending ? preview.lastCanonicalDimensions : metadata!.dimensions_mm;
  const current = Boolean(dimensions) && preview.displayedRevision === revision
    && frame?.header.designRevision === revision
    && !pending && !preview.stale && !preview.error && !invalidDraft;
  const mouth = formatted(dimensions?.mouth_opening, 2);
  const overall = formatted(dimensions?.horn_overall, 3);
  const enclosure = formatted(dimensions?.enclosure_overall, 3);
  const source = formatted(dimensions?.source_diameter, 2);
  const body = formatted(dimensions?.body_overall, 3);
  const mouthValues = dimensions?.mouth_opening;
  const overallValues = dimensions?.horn_overall;
  const wider = mouth && overall && mouthValues && overallValues
    && overallValues.slice(0, 2).some((value, index) => Math.abs(value - mouthValues[index]) > .05 + 1e-9);
  const parts = formula === 'SOURCE-DISK'
    ? [source && `Source ${source}`, body && `Body ${body}`]
    : [mouth && `Mouth ${mouth}`, overall && `Depth ${overallValues![2].toFixed(1)}`,
      wider && `Overall ${overallValues!.slice(0, 2).map((value) => value.toFixed(1)).join(' × ')}`,
      enclosure && `Enclosure ${enclosure}`];
  const content = parts.filter(Boolean).join(' · ');
  const state = pending ? 'Updating dimensions' : !content ? 'Dimensions unavailable'
    : current ? 'Current preview' : 'Last valid preview';
  const requested = formattedTarget(metadata!.dimensions_requested_mm?.mouth_opening);
  const morphDetail = mouth && requested && requested !== `${mouth} mm`
    ? `Mouth requested ${requested}, effective ${mouth} mm.` : '';
  const title = [state, morphDetail].filter(Boolean).join('. ');
  return <><span className={`viewport-dimensions${!current || !content ? ' stale' : ''}`}
    data-testid="design-dimensions" title={title}
    aria-label={`Design dimensions. ${content ? `${content} mm. ` : ''}${title}`}>
    {content && <>{parts.filter(Boolean).map((part, index) => <Fragment key={index}>{index > 0 && ' · '}<span>{part}</span></Fragment>)} mm</>}
  </span><span className="sr-only" data-testid="dimensions-status" aria-live="polite" aria-atomic="true">{state}</span></>;
}
