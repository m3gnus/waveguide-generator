import type { ReactNode } from 'react';
import type { DomainProblem, Lowered } from '../adapter';
import type { ContextDrawingItem, SketchContext } from '../context';
import type { DimensionLayout } from '../dimensionGraphics';
import type { NewConstraint } from '../model';
import type { Diagnosis, DragResult } from '../session';
import type { SnapResult } from '../snap';
import type { Capabilities, DragTarget, XY } from '../solver/types';
import type { Bounds, View, Viewport } from '../view';

/** World x maps right and world y maps up. The domain supplies their meaning. */
export interface AxisSpec { label: string; unit: 'mm'; clamp?: { min?: number; max?: number } }
export interface WorkspaceAxes { x: AxisSpec; y: AxisSpec }
export type EditStatus = { kind: 'valid' } | { kind: 'unfinished'; hint: string }
  | { kind: 'invalid'; problems: DomainProblem[] };
export type DisplayItem = { id: string; role: string; editable: boolean; state?: string[]; label?: string } & (
  | { kind: 'point'; at: XY; glyph?: 'dot' | 'centre' | 'corner' | 'smooth' }
  | { kind: 'polyline'; points: XY[]; closed?: boolean; stale?: boolean }
  | { kind: 'arc'; start: XY; end: XY; center: XY; ccw: boolean }
  | { kind: 'circle'; center: XY; radius: number }
  | { kind: 'handle'; at: XY; anchor: XY; owner: string }
  | { kind: 'dimension'; layout: DimensionLayout; text: string; driving: boolean });
export type ContextDrawItem = ContextDrawingItem;
export interface DisplayModel {
  items: DisplayItem[];
  context: ContextDrawItem[];
  bounds(all: boolean): Bounds;
  /** Screen-space room reserved for labels, independent of the world bounds. */
  fitPadding?: { top: number; bottom: number; right: number; offsetX: number; margin: number };
}
export interface DisplayUI {
  selection: string[]; hover?: string; scale: number;
  /** Additional tool/solver highlights; these do not change the selection. */
  highlighted?: string[];
  context?: SketchContext;
  /** Visible world bounds for unbounded reference lines. */
  bounds?: Bounds;
  viewport?: Viewport;
  view?: View;
  names?: boolean;
  hiddenPoints?: string[];
}
export interface HitCandidate { id: string; kind: 'point' | 'curve' | 'region'; role?: string; gap: number }
export interface ToolPointer {
  phase: 'down' | 'move' | 'up'; at: XY; hit?: string; snap?: SnapResult | null;
  tolerance: number; reference?: boolean;
}
export interface ToolDescriptor<S, C> {
  id: string; group: string; label: string; shortcut?: string; icon: string;
  hint(state: S): string;
  pointer(event: ToolPointer, state: S, local: unknown): { local?: unknown; commands?: C[]; preview?: DisplayItem[]; done?: boolean };
  key?(event: KeyboardEvent, state: S, local: unknown): { local?: unknown; commands?: C[]; consumed: boolean };
}
export interface PropertiesSection { id: string; title: string; render(): ReactNode }
export interface DomainAdapter<TDoc, S, C> {
  id: string; axes: WorkspaceAxes;
  seed(doc: TDoc, authoring: unknown | undefined): S;
  lower(state: S): Lowered<TDoc>;
  status(state: S): EditStatus;
  serializeAuthoring(state: S): unknown;
  apply(state: S, command: C): S | { error: string };
  remove(state: S, selection: string[]): C[];
  compare?(before: S, after: S): string;
  display(state: S, ui: DisplayUI): DisplayModel;
  hits(state: S, at: XY, tolerance: number): HitCandidate[];
  marquee(state: S, box: Bounds, crossing: boolean): string[];
  dragSet(state: S, selection: string[], hit: string): { ids: string[]; move(state: S, delta: XY, at: XY): C[] } | undefined;
  tools: ToolDescriptor<S, C>[];
  properties(state: S, selection: string[], session: WorkspaceSession<S, C>): PropertiesSection[];
  solver?: SolverBinding<S>;
}
export interface SolverBinding<S> { open(state: S): Promise<SolveHandle<S>> }
export interface SolveHandle<S> {
  capabilities: Capabilities;
  diagnose(state: S): Promise<{ state: S; diagnosis: Diagnosis }>;
  beginDrag(ids: string[]): Promise<void>;
  dragTo(targets: DragTarget[]): Promise<DragResult>;
  endDrag(): Promise<Diagnosis>;
  constraint?(state: S, c: NewConstraint, opts: { reference: boolean }): Promise<S>;
  dimension?(state: S, id: string, value: number): Promise<S>;
  destroy(): void;
}
export interface WorkspaceSession<S, C> {
  readonly current: S; readonly status: EditStatus; readonly busy: boolean;
  readonly solver?: { capabilities: Capabilities; diagnosis?: Diagnosis };
  readonly rejection?: unknown; readonly notice?: string;
  command(c: C | C[] | ((s: S) => C | C[])): Promise<S>;
  beginDrag(ids: string[]): Promise<void>;
  dragTo(targets: { id: string; at: XY }[]): Promise<{ stopped: boolean; reason?: string }>;
  endDrag(cancel?: boolean): Promise<void>;
  undo(redo?: boolean): Promise<void>;
  preview?(c: C): Promise<S>; confirm?(): Promise<S>; cancelPreview?(): void;
  constraint?(c: NewConstraint, reference?: boolean, placement?: boolean): Promise<S>;
  dimension?(id: string, value: number, mergePlacement?: boolean): Promise<S>;
}
