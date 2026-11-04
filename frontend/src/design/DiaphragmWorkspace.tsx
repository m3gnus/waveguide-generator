import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { previewSocket } from '../api/previewSocket';
import { Notice } from '../shell/Notice';
import { DeveloperDetails } from '../shell/DeveloperDetails';
import { SketchController, type Authoring } from '../sketch/controller';
import { diaphragmBinding, diaphragmSession, diaphragmFor, previewThroat, readDiaphragm } from '../sketch/adapters/assemblyDiaphragm';
import { sourceContourAdapter, withSourceContext, withSourceLimit, type SourceMeta, type SourceLimit } from '../sketch/adapters/sourceContour';
import { hfThroatContext, wooferAssemblyContext } from '../sketch/adapters/sourceContext';
import type { FrontViewContext } from '../sketch/adapters/frontView';
import { presetDimensionProblem } from '../sketch/adapters/sourcePresetMapping';
import { ProjectHistoryAuthority } from '../sketch/workspace/projectHistoryAuthority';
import { Workspace } from '../sketch/workspace/Workspace';
import { HF_RADIATOR_ID, commitAssembly, setHfSource, type DesignAssembly } from '../stores/assembly';
import { useDesignStore } from '../stores/design';
import { PhasePlugControls } from './PhasePlugControls';

export function DiaphragmWorkspace({ id, assembly, resolved, onMissing }: {
  id: string; assembly: DesignAssembly; resolved?: FrontViewContext; onMissing(): void;
}) {
  const design = useDesignStore(s => s.design);
  const revision = useDesignStore(s => s.designRevision);
  const preview = useSyncExternalStore(previewSocket.subscribe, previewSocket.getSnapshot, previewSocket.getSnapshot);
  const dragging = useDesignStore(s => !!s.dragSnapshot);
  const hf = id === HF_RADIATOR_ID, shape = diaphragmFor(assembly, id)!;
  const woofer = assembly.woofers?.find(w => w.id === id);
  const [error, setError] = useState(''), [detail, setDetail] = useState('');
  const radius = hf ? previewThroat(preview, revision)?.radius : woofer?.radius_mm;
  // Do not attach a stale throat or baffle to a newly opened document.
  const baffle = resolved?.context.baffle;
  const center = resolved?.context.woofers?.find(w => w.radiator_id === id);
  const sketchContext = useMemo(() => {
    if (hf) return radius === undefined ? undefined : hfThroatContext(radius, assembly.hf_source?.phase_plugs ?? []);
    if (!woofer || !baffle || !center) return;
    return wooferAssemblyContext({ width_mm: baffle.x1_mm - baffle.x0_mm, height_mm: baffle.y1_mm - baffle.y0_mm,
      depth_mm: design.enclosure.depth, front_z_mm: 0, horn_xy_mm: [0, 0], horn_length_mm: 0, mouth_radius_mm: 0,
      woofer_xy_mm: [center.x_mm - (baffle.x0_mm + baffle.x1_mm) / 2, center.y_mm - (baffle.y0_mm + baffle.y1_mm) / 2], aperture_radius_mm: woofer.radius_mm },
      shape.diaphragm?.contour.points.at(-1)?.r_mm ?? woofer.radius_mm);
  }, [hf, radius, JSON.stringify(assembly.hf_source?.phase_plugs), JSON.stringify(baffle), JSON.stringify(center), design.enclosure.depth, woofer?.radius_mm, shape.diaphragm]);
  const contextRef = useRef(sketchContext); contextRef.current = sketchContext;
  const initialHost = useRef<{ context: NonNullable<typeof sketchContext>; limit: SourceLimit }>(undefined);
  if (!initialHost.current && sketchContext && radius !== undefined) initialHost.current = {
    context: sketchContext, limit: { kind: hf ? 'throat' : 'aperture', radius, hard: hf },
  };
  const hasHost = !!initialHost.current;
  const [state, setState] = useState<Authoring<SourceMeta>>(() => readDiaphragm(shape, id));
  const [controller, setController] = useState<SketchController<SourceMeta>>();
  const [ready, setReady] = useState(false);
  const [panel, setPanel] = useState<HTMLDivElement | null>(null);
  const missing = useRef(onMissing); missing.current = onMissing;
  useEffect(() => {
    if (!initialHost.current) return;
    let live = true;
    setReady(false);
    const binding = diaphragmBinding(id, () => missing.current());
    let opening = true;
    // Opening context is diagnostic state. Looking at a saved sketch must not rewrite its geometry.
    const authority = new ProjectHistoryAuthority({ ...binding, read: values => {
      const value = binding.read(values);
      const limit = opening ? initialHost.current!.limit : value?.meta.limit ?? initialHost.current!.limit;
      opening = false;
      return value && withSourceContext(withSourceLimit(value, limit), contextRef.current ?? initialHost.current!.context);
    } });
    const next = new SketchController<SourceMeta>({ initial: authority.current, authority, references: s => s.meta.references,
      apply: sourceContourAdapter.apply, changed: setState, preview: setState,
      failed: reason => { setError('WG could not open this sketch. Close and reopen Sources and baffle to try again.'); setDetail(String(reason)); },
      dimensionProblem: (s, key, value) => presetDimensionProblem(s.sketch, s.meta, key, value),
    });
    setState(authority.current); setController(next);
    void next.ready.then(() => { if (live) setReady(true); }, () => {});
    return () => { live = false; next.destroy(); };
  }, [id, hasHost]);
  useEffect(() => {
    if (!controller || !sketchContext || radius === undefined || dragging) return;
    const limit: SourceLimit = { kind: hf ? 'throat' : 'aperture', radius, hard: hf };
    const refreshed = withSourceContext(withSourceLimit(controller.current, limit), sketchContext);
    if (JSON.stringify(refreshed) === JSON.stringify(controller.current)) return;
    if (JSON.stringify(refreshed.sketch) === JSON.stringify(controller.current.sketch)) {
      controller.authority.replace(refreshed); setState(refreshed); return;
    }
    // Context following is an amend in the existing controller, never another Undo step.
    void controller.replaceContext(s => withSourceContext(withSourceLimit(s, limit), sketchContext), 'Reference context changed; dimensions kept')
      .catch(() => setError('The host dimensions conflict with this sketch. Undo the host change, or adjust the profile.'));
  }, [controller, sketchContext?.fingerprint, radius, hf, dragging]);
  const session = useMemo(() => controller && diaphragmSession(controller), [controller]);
  const adapter = useMemo(() => ({ ...sourceContourAdapter,
    display: (s: Authoring<SourceMeta>, ui: Parameters<typeof sourceContourAdapter.display>[1]) => sourceContourAdapter.display(s, { ...ui, context: contextRef.current }),
  }), []);
  return <div className="front-body" role="tabpanel" aria-label={hf ? 'HF diaphragm' : woofer?.label}>
    <div className="front-canvas">{panel && <Workspace adapter={adapter} state={state} session={ready ? session : undefined} slots={{ properties: panel }}/>}</div>
    <aside className="front-panel" aria-label="Diaphragm properties">
      <h3>{hf ? 'HF diaphragm' : woofer?.label}</h3>
      {error && <Notice tone="error">{error}</Notice>}
      {detail && <DeveloperDetails><pre>{detail}</pre></DeveloperDetails>}
      {!sketchContext && <p className="section-note">Waiting for the host context… {hf ? 'Enable Live update in the main workspace if it is paused.' : 'Return to Front to retry if it does not arrive.'}</p>}
      <div ref={setPanel}/>
      {hf && <section aria-label="Phase plugs"><PhasePlugControls plugs={assembly.hf_source?.phase_plugs ?? []} onChange={phase_plugs => {
        const current = useDesignStore.getState().design.assembly;
        if (current?.hf_source?.kind === 'sketched') commitAssembly(setHfSource(current, id, { ...current.hf_source, phase_plugs }));
      }}/></section>}
    </aside>
  </div>;
}
