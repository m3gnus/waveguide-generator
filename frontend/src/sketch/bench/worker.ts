import { measureRow, type CorpusVariant } from '../solver/bench';
import type { SolverName } from '../solver/types';
self.onmessage = (event: MessageEvent<{ solver: SolverName; n: number; variant: CorpusVariant }>) => {
  void measureRow(event.data.solver, event.data.n, event.data.variant, row => self.postMessage(row));
};
