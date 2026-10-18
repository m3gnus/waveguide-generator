import type { Sketch } from './model';
export interface DomainProblem { code: string; ids: string[]; message: string }
export interface Reference { id: string; kind: 'axis' | 'baffle' }
export type Lowered<T> = { ok: true; document: T } | { ok: false; problems: DomainProblem[] };
export interface SketchAdapter<TDoc, TMeta> {
  id: string;
  references: Reference[];
  seed(document: TDoc): { sketch: Sketch; meta: TMeta };
  lower(sketch: Sketch, meta: TMeta): Lowered<TDoc>;
  rules(sketch: Sketch, meta: TMeta): DomainProblem[];
}

/** Keep the first message for a diagnosis of the same code and entities. */
export function uniqueProblems(problems: DomainProblem[]): DomainProblem[] {
  const seen = new Set<string>();
  return problems.filter(problem => {
    const key = JSON.stringify([problem.code, [...new Set(problem.ids)].sort()]);
    if (seen.has(key)) return false;
    seen.add(key); return true;
  });
}
