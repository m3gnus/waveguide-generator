export function resolvePhaseReferenceDistance(results) {
  const metadata = results?.metadata || {};
  const directivityDistance = Number(metadata?.directivity?.effective_distance_m);
  if (Number.isFinite(directivityDistance) && directivityDistance > 0) {
    return directivityDistance;
  }
  const observationDistance = Number(metadata?.observation?.effective_distance_m);
  if (Number.isFinite(observationDistance) && observationDistance > 0) {
    return observationDistance;
  }
  return null;
}

export function resolvePhaseTimeConvention(results) {
  const metadata = results?.metadata || {};
  const explicitPhase = String(metadata?.phase_time_convention || '')
    .trim()
    .toLowerCase()
    .replaceAll('_', '-')
    .replaceAll(' ', '');
  if (
    explicitPhase === 'exp(+ikr)' ||
    explicitPhase === 'e(+ikr)' ||
    explicitPhase === '+ikr' ||
    explicitPhase === 'positive' ||
    explicitPhase === 'positive-spatial'
  ) {
    return 'metal';
  }
  if (
    explicitPhase === 'exp(-ikr)' ||
    explicitPhase === 'e(-ikr)' ||
    explicitPhase === '-ikr' ||
    explicitPhase === 'negative' ||
    explicitPhase === 'negative-spatial' ||
    explicitPhase === 'legacy'
  ) {
    return 'bempp';
  }

  const engine = String(metadata?.engine || '')
    .trim()
    .toLowerCase();
  if (engine === 'hornlab-bempp-bem') {
    return 'metal';
  }

  const selected = String(metadata?.device_interface?.selected || '')
    .trim()
    .toLowerCase()
    .replaceAll('_', '-');
  if (selected === 'metal' || selected === 'bempp-cl-numba' || selected === 'bempp-cl-opencl') {
    return 'metal';
  }

  const backend = String(metadata?.solver_backend || '')
    .trim()
    .toLowerCase()
    .replaceAll('_', '-');
  if (backend === 'metal' || backend === 'hornlab-metal' || backend === 'hornlab-metal-bem') {
    return 'metal';
  }
  if (backend === 'bempp' || backend === 'bempp-cl' || backend === 'bemppcl') {
    return 'bempp';
  }
  if (metadata?.metal && typeof metadata.metal === 'object') {
    return 'metal';
  }
  return null;
}

export function isRhoCNormalizedImpedance(results) {
  const metadata = results?.metadata || {};
  const units = String(metadata?.impedance_units || metadata?.impedance?.units || '')
    .trim()
    .toLowerCase()
    .replaceAll(' ', '');
  const normalization = String(
    metadata?.impedance_normalization || metadata?.impedance?.normalization || ''
  )
    .trim()
    .toLowerCase()
    .replaceAll('-', '_');
  const quantity = String(metadata?.impedance_quantity || metadata?.impedance?.quantity || '')
    .trim()
    .toLowerCase();

  return (
    units === 'z/(rho*c)' ||
    units === 'z/rhoc' ||
    normalization === 'rho_c' ||
    quantity === 'specific_acoustic_impedance'
  );
}
