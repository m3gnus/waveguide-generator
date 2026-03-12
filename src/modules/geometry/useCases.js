import { buildGeometryMeshFromShape } from '../../geometry/pipeline.js';
import { GeometryModule } from './index.js';
import { DesignModule } from '../design/index.js';

function requireViewportState(state) {
  if (!state || typeof state !== 'object') {
    throw new Error('Geometry viewport use cases require an explicit application state snapshot.');
  }
  return state;
}

/**
 * Prepare mesh data for viewport rendering.
 * Consumes an explicit app-state snapshot and returns { vertices, indices, groups, preparedParams }.
 */
export function prepareViewportMesh(state) {
  const viewportState = requireViewportState(state);
  const designTask = DesignModule.task(
    DesignModule.importState(viewportState, {
      applyVerticalOffset: true
    })
  );
  const preparedParams = DesignModule.output.preparedParams(designTask);

  const geometryTask = GeometryModule.task(GeometryModule.importDesign(designTask), {
    adaptivePhi: false
  });
  const geometryShape = GeometryModule.output.shape(geometryTask);
  const { vertices, indices, groups } = buildGeometryMeshFromShape(geometryShape, {
    adaptivePhi: false
  });

  return { vertices, indices, groups, preparedParams };
}
