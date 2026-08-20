/**
 * Turn an imported-solve refusal into advice.
 *
 * `ImportedSolveRefusal` reaches the browser as `"<reason_code>: <message>"`
 * in an HTTP 422 detail. The message is accurate but written for the wire —
 * it names the condition, not the fix, and it leads with an error id nobody
 * can act on. Every `passive_cardioid_topology` refusal is a geometry or role
 * problem that must be corrected in CAD and re-ingested, and the difference
 * between "no PORT_EXIT sources" and "PORT_EXIT split across channels" is the
 * difference between remodelling the port and reassigning a channel. So the
 * conditions are distinguished, not summarised.
 *
 * Anything unrecognised is passed through untouched. A refusal WG cannot
 * explain is still the truest thing it knows.
 */

const CARDIOID_STAGE = 'passive_cardioid_topology';

interface RefusalExplanation {
  /** Substring of the server message that identifies the condition. */
  match: string;
  advice: string;
}

const CARDIOID_EXPLANATIONS: readonly RefusalExplanation[] = [
  {
    match: 'ingestion source tag map',
    advice: 'This ingestion carries no source tag map, so WG cannot tell which mesh faces are the port. '
      + 'Rebuild the mesh from the CAD return before enabling the passive-cardioid campaign.',
  },
  {
    match: 'PORT_EXIT aperture sources',
    advice: 'The return has no port-exit aperture. Mark the face the port vents through as a PORT_EXIT source '
      + '(or PORT_EXIT_L and PORT_EXIT_R for a split port) in CAD, then bring the geometry back and re-ingest.',
  },
  {
    match: 'exactly one MF diaphragm source',
    advice: 'The passive cardioid needs exactly one MF diaphragm to load the rear chamber. '
      + 'Give the return a single source with the MF role — remove, re-role, or merge the others — and re-ingest.',
  },
  {
    match: "MF diaphragm's drive channel",
    advice: 'A coupled solve drives the diaphragm through its motor, so the MF source must sit alone on one drive '
      + 'channel with a complete Thiele-Small model. Assign it its own channel under Drive channels & drivers and '
      + 'fill in the required driver fields, or turn Coupled off.',
  },
  {
    match: 'PORT_EXIT patches in one drive channel',
    advice: 'The port exit is split across drive channels, so the coupled solve cannot treat it as one port. '
      + 'Assign every PORT_EXIT patch to the same drive channel under Drive channels & drivers, or turn Coupled off.',
  },
];

export function explainImportedRefusal(message: string): string {
  const [reasonCode, ...rest] = message.split(': ');
  if (reasonCode.trim() !== CARDIOID_STAGE) return message;
  const detail = rest.join(': ');
  const explanation = CARDIOID_EXPLANATIONS.find(({ match }) => detail.includes(match));
  return explanation ? `Passive cardioid: ${explanation.advice}` : message;
}
