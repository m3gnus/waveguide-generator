import type { JSONValue, NewConstraint } from './model';
import type { SnapResult } from './snap';

export type PointRef = string | { x: number; y: number; snap?: SnapResult | null };
export type Command =
  | { kind: 'addPoint'; x: number; y: number }
  | { kind: 'addLine'; from: PointRef; to: PointRef }
  | { kind: 'addTangentArc'; from: string; to: PointRef }
  | { kind: 'addArc3'; start: PointRef; end: PointRef; through: { x: number; y: number } }
  | { kind: 'addConstraint'; constraint: NewConstraint }
  | { kind: 'removeConstraint'; id: string }
  | { kind: 'setDimension'; id: string; value: number }
  | { kind: 'setDriving'; id: string; driving: boolean }
  | { kind: 'movePoint'; id: string; x: number; y: number }
  | { kind: 'deleteEntity'; id: string }
  | { kind: 'deletePoint'; id: string }
  | { kind: 'convertEntity'; id: string; to: 'arc' | 'line' }
  | { kind: 'splitAt'; entity: string; x: number; y: number }
  | { kind: 'setEntityData'; id: string; data: JSONValue }
  | { kind: 'setConstruction'; id: string; construction: boolean };
