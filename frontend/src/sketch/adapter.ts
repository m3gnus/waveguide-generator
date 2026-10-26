export interface DomainProblem { code: string; ids: string[]; message: string; severity?: 'warning' }
export type Reference = { id: string; kind: 'axis' | 'baffle' }
  | { id: string; kind: 'limit'; radius: number; label: string; snapRadius?: number; locked: boolean; point?: string };
export type Lowered<T> = { ok: true; document: T } | { ok: false; problems: DomainProblem[] };
/** Keep the first message for a diagnosis of the same code and entities. */
export function uniqueProblems(problems: DomainProblem[]): DomainProblem[] {
  const seen = new Set<string>();
  return problems.filter(problem => {
    const key = JSON.stringify([problem.code, [...new Set(problem.ids)].sort()]);
    if (seen.has(key)) return false;
    seen.add(key); return true;
  });
}
