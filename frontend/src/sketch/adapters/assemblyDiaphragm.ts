/** Assembly storage and Workspace compatibility for the existing source sketch controller. */
import type { SourceDocument } from '../../api/sourceEditor';
import type { PreviewSnapshot } from '../../api/previewSocket';
import { currentDocumentLoad } from '../../stores/design';
import { HF_RADIATOR_ID, type AssemblyWoofer, type AssemblyWooferShape, type DesignAssembly } from '../../stores/assembly';
import { SketchController, type Authoring } from '../controller';
import type { Command } from '../commands';
import { editRecord } from '../workspace/editRecord';
import type { ProjectBinding } from '../workspace/projectHistoryAuthority';
import type { WorkspaceSession } from '../workspace/types';
import { sourceContourAdapter, seed, type SourceMeta } from './sourceContour';

export type Diaphragm = NonNullable<DesignAssembly['hf_source']> | AssemblyWooferShape;

/** The mesher's canonical source readout, never an estimate from horn parameters. */
export function previewThroat(preview: PreviewSnapshot, revision: number): { radius: number; capHeight?: number } | undefined {
  if (preview.frame?.documentLoad !== currentDocumentLoad() || preview.displayedRevision !== revision || preview.stale) return;
  const metadata = preview.frame.header.previewMetadata as { source_cap_height_mm?: number; dimensions_mm?: { source_diameter?: [number, number] } } | undefined;
  const diameter = metadata?.dimensions_mm?.source_diameter;
  if (!diameter?.every(v => Number.isFinite(v) && v > 0) || Math.abs(diameter[0] - diameter[1]) > 1e-7) return;
  return { radius: diameter[0] / 2, capHeight: Number.isFinite(metadata?.source_cap_height_mm) ? metadata?.source_cap_height_mm : undefined };
}
export function diaphragmFor(assembly: DesignAssembly, id: string): Diaphragm | undefined {
  return id === HF_RADIATOR_ID ? assembly.hf_source : assembly.woofers?.find(w => w.id === id)?.shape;
}

export function unfinishedDiaphragms(assembly: DesignAssembly) {
  return [{ id: HF_RADIATOR_ID, label: 'HF diaphragm', shape: assembly.hf_source },
    ...(assembly.woofers ?? []).map(w => ({ id: w.id, label: w.label, shape: w.shape }))]
    .filter(item => item.shape?.kind === 'sketched' && (item.shape.status && item.shape.status !== 'valid' || item.shape.authoring?.pending === true || !item.shape.diaphragm));
}

/** Bind the array by identity, never by an index that Open or deletion could reuse. */
export function diaphragmBinding(id: string, onMissing: () => void): ProjectBinding<Authoring<SourceMeta>> {
  const path = id === HF_RADIATOR_ID ? 'assembly.hf_source' : 'assembly.woofers';
  const shapeAt = (values: Readonly<Record<string, unknown>>) => id === HF_RADIATOR_ID
    ? values[path] as Diaphragm | undefined : (values[path] as AssemblyWoofer[] | undefined)?.find(w => w.id === id)?.shape;
  return { paths: [path], onMissing,
    read(values) {
      const shape = shapeAt(values);
      if (shape?.kind !== 'sketched') return;
      return readDiaphragm(shape, id);
    },
    write(state, values) {
      const shape = shapeAt(values)!;
      const record = editRecord(sourceContourAdapter, state, shape.diaphragm as SourceDocument | null ?? null);
      // Pending envelopes must name the last valid document so the shared parser can restore the draft.
      const next = { ...shape, diaphragm: record.document, status: record.status,
        authoring: { ...(record.authoring as object), document: record.document } };
      return { [path]: id === HF_RADIATOR_ID ? next : (values[path] as AssemblyWoofer[]).map(w => w.id === id ? { ...w, shape: next } : w) };
    },
  };
}

/** Only adapts signatures; solving, transactions, drags and history stay in SketchController. */
export function diaphragmSession(controller: SketchController<SourceMeta>): WorkspaceSession<Authoring<SourceMeta>, Command> {
  return {
    historyKind: 'project', subscribeHistory: controller.subscribeHistory,
    get current() { return controller.current; },
    get status() { return sourceContourAdapter.status(controller.current); },
    get busy() { return controller.busy; },
    get notice() { return controller.notice; },
    get solver() { return controller.capabilities ? { capabilities: controller.capabilities, diagnosis: controller.diagnosis } : undefined; },
    command: async command => { await controller.command(command); return controller.current; },
    beginDrag: ids => controller.beginDrag(ids),
    dragTo: async targets => {
      const first = targets[0];
      if (!first) return { stopped: false };
      return controller.dragTo(first.id, first.at, targets.map(t => ({ pointId: t.id, x: t.at.x, y: t.at.y })));
    },
    endDrag: cancel => controller.endDrag(cancel), undo: redo => controller.undo(redo),
    constraint: async (c, reference, placement) => { await controller.constraint(c, reference, placement); return controller.current; },
    dimension: async (id, value, placement) => { await controller.dimension(id, value, placement); return controller.current; },
  };
}

export function revertDiaphragm(shape: Diaphragm): Diaphragm {
  if (!shape.diaphragm) return 'radiator_id' in shape ? { radiator_id: HF_RADIATOR_ID, kind: 'classic' } : { kind: 'piston' };
  return { ...shape, status: 'valid', authoring: { ...(sourceContourAdapter.serializeAuthoring(seed(shape.diaphragm as SourceDocument)) as object), document: shape.diaphragm } };
}

export function readDiaphragm(shape: Diaphragm, id: string) {
  const document = shape.diaphragm as SourceDocument | undefined ?? sourceIdentity(id, id === HF_RADIATOR_ID ? 'normal' : 'axial');
  const authoring = !shape.diaphragm && shape.authoring ? { ...shape.authoring, document } : shape.authoring;
  return sourceContourAdapter.seed(document, authoring);
}

export function sourceIdentity(id: string, motion: 'normal' | 'axial'): SourceDocument {
  return { contour: { version: 1, physical_source_id: id, rim_id: 'rim', points: [], segments: [] },
    drive: { channel_id: id, motion, weights: {} } };
}
