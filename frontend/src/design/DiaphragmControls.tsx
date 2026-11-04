import { useEffect, useState, useSyncExternalStore } from 'react';
import { sourceEditorApi } from '../api/sourceEditor';
import { previewSocket } from '../api/previewSocket';
import { failureWords, ProgramFault } from '../api/httpError';
import { sourceContourAdapter, seed } from '../sketch/adapters/sourceContour';
import { seedPreset } from '../sketch/adapters/sourcePreset';
import { previewThroat, sourceIdentity } from '../sketch/adapters/assemblyDiaphragm';
import { currentDocumentLoad, serializeDesign, useDesignStore } from '../stores/design';
import { commitAssembly, emptyAssembly, HF_RADIATOR_ID, setHfSource, setWooferShape, type AssemblyWooferShape } from '../stores/assembly';
import { Notice } from '../shell/Notice';
import { SelectRow } from './SelectRow';

/** Expansion is the shared preset generator; conversion publishes only after it succeeds. */
export function DiaphragmSelect({ id, openProfile }: { id: string; openProfile(id: string): void }) {
  const design = useDesignStore(s => s.design);
  const revision = useDesignStore(s => s.designRevision);
  const preview = useSyncExternalStore(previewSocket.subscribe, previewSocket.getSnapshot, previewSocket.getSnapshot);
  const throat = previewThroat(preview, revision);
  const assembly = design.assembly;
  const hf = id === HF_RADIATOR_ID;
  const woofer = assembly?.woofers?.find(w => w.id === id);
  const kind = hf ? assembly?.hf_source?.kind ?? 'classic' : woofer?.shape.kind ?? 'piston';
  const waitingForCap = hf && kind === 'classic' && (!throat || design.source.shape !== 2 && throat.capHeight === undefined);
  // SourceDocument requires strictly increasing radii; it cannot encode a vertical recess collar.
  const recessed = !hf && woofer?.shape.kind === 'piston' && !!woofer.shape.recess_mm;
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [live] = useState(() => ({ current: true }));
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  async function change(value: string) {
    if (value === kind) return;
    const before = useDesignStore.getState().design, load = currentDocumentLoad();
    const original = JSON.stringify(hf ? before.assembly?.hf_source : woofer);
    setBusy(true); setError('');
    try {
      let shape: AssemblyWooferShape;
      if (value !== 'sketched') shape = { kind: value as 'piston' | 'ath_cone' };
      else {
        if (waitingForCap || recessed) return;
        const radius = hf ? throat!.radius : woofer!.radius_mm;
        const height = hf ? throat!.capHeight ?? 0 : 0;
        const preset = kind === 'ath_cone' ? 'cone' : height > 0 ? 'dome' : 'flat';
        // The ATH cone's published independent dimensions, passed to the shared cone builder.
        const dimensions: Record<string, number> = preset === 'cone' ? { radius_mm: radius, depth_mm: .4 * radius, cap_radius_mm: .35 * radius,
          cap_height_mm: (.525 - Math.sqrt(.525 ** 2 - .35 ** 2)) * radius } : preset === 'dome'
            ? { radius_mm: radius, height_mm: height } : { radius_mm: radius };
        const expanded = await sourceEditorApi.expand(preset, dimensions, sourceIdentity(id, hf ? 'normal' : 'axial'));
        const document = structuredClone(expanded.document);
        const reflected = hf && before.source.curvature === -1 && height > 0;
        if (reflected) {
          document.contour.points.forEach(p => { p.z_mm = -p.z_mm; });
          document.contour.segments.forEach(p => { if (p.center_mm) p.center_mm[1] = -p.center_mm[1]; if (p.kind === 'arc') p.direction = p.direction === 'cw' ? 'ccw' : 'cw'; });
        }
        const author = reflected ? seed(document) : seedPreset(document, preset, dimensions);
        if (sourceContourAdapter.status(author).kind !== 'valid') throw new ProgramFault('The expanded profile did not pass the source contract.');
        shape = { kind: 'sketched', diaphragm: document, status: 'valid',
          authoring: { ...(sourceContourAdapter.serializeAuthoring(author) as object), document } };
      }
      const current = useDesignStore.getState().design;
      const hornChanged = hf && JSON.stringify({ ...serializeDesign(current), assembly: undefined }) !== JSON.stringify({ ...serializeDesign(before), assembly: undefined });
      if (!live.current || hornChanged || load !== currentDocumentLoad() || JSON.stringify(hf ? current.assembly?.hf_source : current.assembly?.woofers?.find(w => w.id === id)) !== original) return;
      const next = current.assembly ?? emptyAssembly();
      commitAssembly(hf ? setHfSource(next, id, value === 'classic' ? { kind: 'classic', radiator_id: HF_RADIATOR_ID }
        : { ...shape, kind: 'sketched', radiator_id: HF_RADIATOR_ID }) : setWooferShape(next, id, shape));
    } catch (reason) { if (live.current) setError(failureWords(reason, 'WG could not create this profile. Keep the current diaphragm and try again.', 'Create diaphragm profile')); }
    finally { if (live.current) setBusy(false); }
  }
  return <>
    <SelectRow><label htmlFor={`diaphragm-${id}`}>{hf ? 'HF diaphragm' : 'Diaphragm'}</label>
      <select id={`diaphragm-${id}`} value={kind} disabled={busy} onChange={event => void change(event.currentTarget.value)}>
        {hf ? <option value="classic">Classic</option> : <><option value="piston">Flat piston</option><option value="ath_cone">ATH cone</option></>}
        {!recessed && <option value="sketched" disabled={waitingForCap}>{hf ? 'Sketched' : 'Sketched profile'}</option>}
      </select></SelectRow>
    {kind === 'sketched' && <button className="btn" type="button" onClick={() => openProfile(id)}>Edit profile</button>}
    {waitingForCap && <p className="section-note">Wait for the live preview of a circular throat before converting the classic cap. Enable Live update in the main workspace if it is paused.</p>}
    {recessed && <p className="section-note">A recessed piston cannot be converted without changing its collar. To start from a flush piston, set Recess to 0 first.</p>}
    {busy && <p role="status">Creating profile…</p>}
    {error && <Notice tone="error">{error}</Notice>}
  </>;
}
