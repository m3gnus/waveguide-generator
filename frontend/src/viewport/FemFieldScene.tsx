import { useMemo } from 'react';
import type { FieldPlaneSpec } from '../api/fieldPlane';
import type { DecodedFemField } from '../api/femField';
import { FemFieldView, type FemFieldRendering } from './FemFieldView';
import type { SlicePlane } from './femField';
import { useFieldPlaneStore } from './fieldPlaneStore';

export function femSlicePlane(plane: FieldPlaneSpec): SlicePlane {
  const [ux, uy, uz] = plane.axis_u;
  const [vx, vy, vz] = plane.axis_v;
  return { origin: plane.origin_m.map((value) => value * 1000) as [number, number, number],
    normal: [uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx] };
}

export function FemFieldScene({ field, rendering, sequential, unitsPerMetre }: {
  field: DecodedFemField; rendering: FemFieldRendering; sequential: readonly string[]; unitsPerMetre: number;
}) {
  const plane = useFieldPlaneStore((state) => state.plane);
  const mode = useFieldPlaneStore((state) => state.displayMode);
  const animating = useFieldPlaneStore((state) => state.animating);
  const animationSpeed = useFieldPlaneStore((state) => state.animationSpeed);
  const slicePlane = useMemo(() => plane ? femSlicePlane(plane) : null, [plane]);
  return <FemFieldView domain={field.domain} values={field.values} plane={slicePlane}
    mode={mode} rendering={rendering} sequential={sequential} unitsPerMetre={unitsPerMetre}
    animating={animating} animationSpeed={animationSpeed}/>;
}
