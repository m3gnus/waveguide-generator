import { SketchKit, type SketchCanvasProps } from './kit/sketchKit';
import { sourceDisplay, sourceInitialView } from './adapters/sourceDisplay';

/** Source-compatible facade; the kit and workspace hold no source presentation rules. */
export function SketchCanvas(props: SketchCanvasProps) {
  return <SketchKit {...props} presentation={{
    axes: { x: { label: 'x', unit: 'mm' }, y: { label: 'y', unit: 'mm' } },
    display: (sketch, ui) => sourceDisplay({ sketch, meta: { references: props.references } }, ui, props.problems),
    initialView: sourceInitialView,
  }}/>;
}
export type { SketchCanvasProps } from './kit/sketchKit';
export type { WorkspaceSession } from './workspace/types';
