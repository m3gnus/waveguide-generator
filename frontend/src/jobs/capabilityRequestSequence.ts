// One issue clock per query client, shared by fetches and solve acceptance.
// Reading the clock does not make an in-flight response newer than a solve.
const sequences = new WeakMap<object, number>();

export function latestCapabilityRequest(scope: object): number {
  return sequences.get(scope) ?? 0;
}

export function issueCapabilityRequest(scope: object): number {
  const sequence = latestCapabilityRequest(scope) + 1;
  sequences.set(scope, sequence);
  return sequence;
}
