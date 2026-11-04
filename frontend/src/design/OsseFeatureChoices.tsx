import { useState } from 'react';
import { resolveDesignExpressions, serializeDesignDocument } from '../api/designIo';
import { mergeDesignForOsseFeatures, osseInputsRequiringNumbers, osseNumberLabel, resolveOsseBodyExpressions, useDesignStore, type DesignDocument } from '../stores/design';
import { osseFeatures, osseFormula, OSSE_FEATURE_CONFLICT, type OsseFeatures } from './osseFeatures';
import { PARAMETRIC_CONTROLS } from './parametricControlRegistry';
import { SelectRow } from './SelectRow';

const choices = [
  { key: 'throat', label: 'Throat', options: [['none', 'None'], ['authored', 'Adapter (authored)'], ['controls', 'Adapter (controls)']] },
  { key: 'mouth', label: 'Mouth end', options: [['plain', 'Plain'], ['roundover', 'Roundover'], ['arc', 'Terminating arc']] },
  { key: 'axial', label: 'Axial scale', options: [['off', 'Off'], ['on', 'On']] },
] as const;

export function OsseFeatureChoices({ design, onChanged }: { design: DesignDocument; onChanged: () => void }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const features = osseFeatures(design.formula, design)!;

  const choose = async (nextFeatures: OsseFeatures) => {
    if (!osseFormula(nextFeatures) || pending) return;
    const state = useDesignStore.getState();
    const current = state.design;
    setError(null);
    setPending(true);
    try {
      const isCurrent = () => useDesignStore.getState().designRevision === state.designRevision
        && useDesignStore.getState().loadGeneration === state.loadGeneration;
      const inputs = osseInputsRequiringNumbers(current);
      const paths = Object.keys(inputs);
      const resolved = paths.length ? await resolveDesignExpressions(current, paths) : {};
      if (!isCurrent()) return;
      const candidate = mergeDesignForOsseFeatures(resolveOsseBodyExpressions(current, resolved), nextFeatures);
      // The existing read-only serializer validates the complete mesher contract.
      await serializeDesignDocument(candidate, 'OSSE', fetch, undefined, null);
      if (!isCurrent()) return;
      const replacements = paths.map((path) => inputs[path]?.raw != null
        ? `${osseNumberLabel(path)}: ${path} = ${inputs[path]!.raw} will become ${resolved[path]}.`
        : `${osseNumberLabel(path)} is unset; its current default will become ${resolved[path]}.`);
      if (paths.length && !window.confirm(`Switching will use these plain numbers:\n\n${replacements.join('\n')}\n\nUndo restores the complete previous design and its expressions. Continue?`)) return;
      if (!isCurrent()) return;
      useDesignStore.getState().setOsseFeatures(nextFeatures, resolved);
      onChanged();
    } catch (cause) {
      if (cause instanceof Error && cause.message.startsWith('Native features require')) setError(cause.message);
      else if (cause instanceof Error && !(cause instanceof TypeError) && !cause.message.startsWith('Request failed:')) {
        setError(`Cannot switch: ${cause.message.replace(/\.$/, '')}. The current design was kept.`);
      } else setError('Could not validate the new OSSE features. Try again; the current design was kept.');
    } finally {
      setPending(false);
    }
  };

  return <fieldset className="osse-features" aria-busy={pending} data-control-reveal-id={PARAMETRIC_CONTROLS.osseFeatures.reveal.id}>
    <legend className="sr-only">OSSE features</legend>
    {choices.map(({ key, label, options }) => <SelectRow key={key}>
      <label htmlFor={`osse-feature-${key}`}>{label}</label>
      <select id={`osse-feature-${key}`} value={features[key]} disabled={pending} onChange={(event) => {
        void choose({ ...features, [key]: event.target.value } as OsseFeatures);
      }}>
        {options.map(([value, name]) => {
          const disabled = !osseFormula({ ...features, [key]: value } as OsseFeatures);
          return <option key={value} value={value} disabled={disabled} title={disabled ? OSSE_FEATURE_CONFLICT : undefined}>
            {name}{disabled ? ` — ${OSSE_FEATURE_CONFLICT}` : ''}
          </option>;
        })}
      </select>
    </SelectRow>)}
    {error && <p className="field-warning" role="alert">{error}</p>}
  </fieldset>;
}
