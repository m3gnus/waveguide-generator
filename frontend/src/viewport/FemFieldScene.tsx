import { useMemo } from 'react';
import type { FieldPlaneSpec } from '../api/fieldPlane';
import type { DecodedFemField } from '../api/femField';
import { FemFieldView, type FemFieldRendering, type FemDisplayMode } from './FemFieldView';
import { femNormalVelocitySlice, type SlicePlane } from './femField';
import { useFieldPlaneStore } from './fieldPlaneStore';

export function femSlicePlane(plane: FieldPlaneSpec): SlicePlane {
  const [ux, uy, uz] = plane.axis_u;
  const [vx, vy, vz] = plane.axis_v;
  return { origin: plane.origin_m.map((value) => value * 1000) as [number, number, number],
    normal: [uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx] };
}

export function FemFieldScene({ field, rendering, sequential, unitsPerMetre, planeField, displayMode, manualWindow, quantity, scope }: {
  field: DecodedFemField;
  planeField?: import('../api/femField').FemPlaneField | null;
  displayMode?: FemDisplayMode;
  scope?: import('../api/femField').FemPlaneScope;
  quantity?: import('../api/femField').FemPlaneQuantity;
  manualWindow?: import('./fieldPlaneColor').FieldPlaneValueWindow | null;
  rendering: FemFieldRendering; sequential: readonly string[]; unitsPerMetre: number;
}) {
  const plane = useFieldPlaneStore((state) => state.plane);
  const mode = useFieldPlaneStore((state) => state.displayMode);
  const animating = useFieldPlaneStore((state) => state.animating);
  const animationSpeed = useFieldPlaneStore((state) => state.animationSpeed);
  const slicePlane = useMemo(() => plane ? femSlicePlane(plane) : null, [plane]);
  return <FemFieldView domain={field.domain} values={field.values} plane={slicePlane}
    mode={displayMode ?? mode} planeField={planeField} includeInterior={scope === 'combined'} manualWindow={manualWindow} velocity={quantity === 'velocity_normal' ? { frequencyHz: field.frequencyHz, density: field.payload.density_kg_per_m3 ?? 1.21 } : undefined} rendering={rendering} sequential={sequential} unitsPerMetre={unitsPerMetre}
    animating={animating} animationSpeed={animationSpeed}/>;
}

export function femInteriorDisplayValues(field: DecodedFemField, plane: FieldPlaneSpec | null,
  quantity: import('../api/femField').FemPlaneQuantity) {
  return quantity === 'velocity_normal' && plane
    ? femNormalVelocitySlice(field.domain, field.values, femSlicePlane(plane), field.frequencyHz,
      field.payload.density_kg_per_m3 ?? 1.21) : field.values;
}
