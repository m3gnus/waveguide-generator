import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it } from 'vitest';
import { expectNoJargon } from '../testing/expectNoJargon';
import type { ImportedMeshScene } from './importedMesh';
import { MeshReportCard, meshReport, previewSurfaceNote } from './MeshReport';

const mesh = (overrides: Partial<ImportedMeshScene> = {}, tessellation: ImportedMeshScene['tessellation'] = {}): ImportedMeshScene => ({
  name: 'Horn', source: 'solver', ingestId: null, scene: {} as ImportedMeshScene['scene'],
  triangleCount: 3538, solvedTriangleCount: 1769, physicalGroupCount: 3, artifactToken: 'token',
  tessellation, ...overrides,
});
const value = (report: ReturnType<typeof meshReport>, label: string) => report.rows.find((row) => row.label === label)?.value;

describe('preview note', () => {
  it('says the number is the preview, and that the control does not move it', () => {
    const note = previewSurfaceNote({ surface_deviation: { peak_mm: 0.098, measurement_complete: true } });
    expect(note).toContain('Preview surface within 0.098 mm');
    expect(note).toContain('Max surface deviation does not change it');
    expect(note).toContain('Mesh view reports the solve mesh');
  });

  it.each([[undefined], [{ surface_deviation: null }], [{ surface_deviation: { peak_mm: null } }], [{ surface_deviation: { peak_mm: 0.1, measurement_complete: false } }]])('says nothing without a complete measurement', (record) => {
    expect(previewSurfaceNote(record)).toBeNull();
  });
});

describe('solve mesh report', () => {
  it('reports a sampled peak as a lower bound against the CAD surface', () => {
    const report = meshReport(mesh({}, { surface_deviation_mm: null, surface_deviation: { peak_mm: 5.144, triangle_count: 1769, sampled_triangle_count: 256 }, element_shape: { p1_radius_ratio: 0.632 } }));
    expect(report.title).toBe('Solve mesh');
    expect(value(report, 'Deviation target')).toBe('Off');
    expect(value(report, 'Deviation')).toBe('at least 5.144 mm');
    expect(value(report, 'Triangles')).toBe('1,769 solved');
    expect(value(report, 'Element shape')).toBe('0.63');
    expect(report.notes.join(' ')).toContain('highest of 256 sampled triangles out of 1,769, so the true peak can be higher');
    expect(report.notes.join(' ')).toContain('CAD surface the mesh is built from, not the horn formula');
    expect(report.notes.join(' ')).toContain('worst 1% of triangles');
  });

  it('keeps a whole-mesh sample a lower bound too', () => {
    const report = meshReport(mesh({}, { surface_deviation: { peak_mm: 0.2, triangle_count: 12, sampled_triangle_count: 12 } }));
    expect(value(report, 'Deviation')).toBe('at least 0.200 mm');
    expect(report.notes.join(' ')).toContain('highest of 12 sampled triangles, so');
  });

  it('labels a real bound as one', () => {
    const report = meshReport(mesh({}, { surface_deviation_mm: 0.3, surface_deviation: { peak_mm: 0.004, peak_is_upper_bound: true, triangle_count: 12, sampled_triangle_count: 0 } }));
    expect(value(report, 'Deviation target')).toBe('0.300 mm');
    expect(value(report, 'Deviation')).toBe('at most 0.004 mm');
    expect(report.notes.join(' ')).not.toContain('sampled');
  });

  it('keeps a missing observation missing and reports the worst triangle when it is all there is', () => {
    const report = meshReport(mesh({ source: 'cad', triangleCount: 10, solvedTriangleCount: 10 }, { surface_deviation: { peak_mm: null }, element_shape: { min_radius_ratio: 0.21 } }));
    expect(report.title).toBe('Mesh');
    expect(value(report, 'Deviation')).toBe('not measured');
    expect(value(report, 'Triangles')).toBe('10');
    expect(report.rows.some((row) => row.label === 'Deviation target')).toBe(false);
    expect(report.notes.join(' ')).toContain('The worst triangle');
  });

  it.each([['building', 'rebuilding…'], ['stale', 'out of date']] as const)('marks a %s report instead of passing it off as current', (status, words) => {
    const host = document.createElement('div');
    const root = createRoot(host);
    act(() => root.render(<MeshReportCard mesh={mesh({}, { surface_deviation: { peak_mm: 1 } })} status={status} />));
    expect(host.querySelector('.mesh-quality-card.stale')).not.toBeNull();
    expect(host.textContent).toContain(words);
    expectNoJargon(host.textContent ?? '');
    act(() => root.unmount());
  });

  it('reads plainly when current', () => {
    const host = document.createElement('div');
    const root = createRoot(host);
    act(() => root.render(<MeshReportCard mesh={mesh({}, { surface_deviation_mm: null, surface_deviation: { peak_mm: 1, sampled_triangle_count: 2, triangle_count: 4 }, element_shape: { p1_radius_ratio: 0.5 } })} status="current" />));
    expect(host.querySelector('.stale')).toBeNull();
    expect(host.querySelector('[aria-label="Solve mesh report"]')).not.toBeNull();
    expect(host.textContent).not.toMatch(/radius ratio|chord|p1/i);
    expectNoJargon(host.textContent ?? '');
    act(() => root.unmount());
  });
});
