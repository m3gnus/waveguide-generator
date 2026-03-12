import { buildGeometryMeshFromShape } from '../../geometry/pipeline.js';
import { GeometryModule } from './index.js';
import { DesignModule } from '../design/index.js';
import { GlobalState } from '../../state.js';

/**
 * Prepare mesh data for viewport rendering.
 * Consumes GlobalState and returns { vertices, indices, preparedParams }.
 */
export function prepareViewportMesh() {
  const designTask = DesignModule.task(
    DesignModule.importState(GlobalState.get(), {
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
