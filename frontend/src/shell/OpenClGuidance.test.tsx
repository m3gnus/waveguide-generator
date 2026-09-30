import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import guidance from '../../../shared/opencl-driver-guidance.v1.json';
import componentSource from './OpenClGuidance.tsx?raw';
import { OpenClGuidance, isOpenClGuidanceData, type OpenClGuidancePlatform, type OpenClGuidanceReason } from './OpenClGuidance';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('OpenClGuidance', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    vi.doUnmock('../../../shared/opencl-driver-guidance.v1.json');
    vi.resetModules();
  });

  it.each<OpenClGuidancePlatform>(['windows', 'linux'])('renders the %s summary, alternatives, steps and applicable warnings', (platform) => {
    act(() => root.render(<OpenClGuidance platform={platform} />));
    const block = guidance.platforms[platform];
    const warnings = guidance.warnings.filter((warning) => warning.platforms.includes(platform));
    const alternatives = guidance.gpu_alternatives.filter((entry) => entry.platforms.includes(platform));
    expect(host.querySelector('section')?.getAttribute('aria-label')).toBe(guidance.labels.ariaLabel);
    expect(host.querySelector('h3')?.textContent).toBe(guidance.labels.heading);
    expect([...host.querySelectorAll('p')].map((p) => p.textContent))
      .toEqual([block.summary, ...alternatives.map((entry) => entry.text),
        ...block.steps.map((step) => step.note), ...warnings.map((warning) => warning.text)]);
    const links = [...host.querySelectorAll('a')];
    expect(links.map((a) => ({ label: a.textContent, url: a.getAttribute('href') })))
      .toEqual(block.steps.map(({ label, url }) => ({ label, url })));
    for (const link of links) {
      expect(link.target).toBe('_blank');
      expect(link.rel.split(' ')).toEqual(expect.arrayContaining(['noopener', 'noreferrer']));
    }
    for (const reason of Object.values(guidance.reasons)) {
      expect(host.textContent).not.toContain(reason);
    }
  });

  it.each<OpenClGuidanceReason>(['no_device', 'smoke_test_failed', 'smoke_test_timeout', 'inventory_timeout', 'pocl_windows'])('renders the shared %s reason', (reason) => {
    act(() => root.render(<OpenClGuidance platform="windows" reason={reason} />));
    expect(host.querySelector('p')?.textContent).toBe(guidance.reasons[reason]);
    for (const [code, text] of Object.entries(guidance.reasons)) {
      if (code !== reason) expect(host.textContent).not.toContain(text);
    }
  });

  it('renders the shared infinite-baffle numba notice first, and nothing without the prop', () => {
    act(() => root.render(<OpenClGuidance platform="windows" notice="infinite_baffle_numba" reason="no_device" />));
    const paragraphs = [...host.querySelectorAll('section > p')].map((node) => node.textContent);
    expect(paragraphs[0]).toBe(guidance.notices.infinite_baffle_numba);
    expect(paragraphs[1]).toBe(guidance.reasons.no_device);
    act(() => root.render(<OpenClGuidance platform="windows" />));
    expect(host.textContent).not.toContain(guidance.notices.infinite_baffle_numba);
  });

  it('updates the platform and clears a previous reason without retaining the Windows warning', () => {
    act(() => root.render(<OpenClGuidance platform="windows" reason="no_device" />));
    act(() => root.render(<OpenClGuidance platform="linux" />));
    expect(host.textContent).toContain(guidance.platforms.linux.summary);
    expect(host.textContent).not.toContain(guidance.reasons.no_device);
    expect(host.textContent).not.toContain(guidance.warnings[0].text);
    expect(host.querySelectorAll('a')).toHaveLength(guidance.platforms.linux.steps.length);
  });

  it('validates the shipped JSON shape', () => {
    expect(isOpenClGuidanceData(guidance)).toBe(true);
  });

  it.each<OpenClGuidancePlatform>(['windows', 'linux'])('filters GPU alternatives for %s and updates them on platform changes', async (platform) => {
    const otherPlatform = platform === 'windows' ? 'linux' : 'windows';
    const data = structuredClone(guidance);
    data.gpu_alternatives = [
      { id: 'applicable', platforms: [platform], text: 'Applicable alternative' },
      { id: 'other', platforms: [otherPlatform], text: 'Other alternative' },
    ];
    vi.doMock('../../../shared/opencl-driver-guidance.v1.json', () => ({ default: data }));
    const { OpenClGuidance: Component } = await import('./OpenClGuidance');
    act(() => root.render(<Component platform={platform} />));
    expect(host.querySelectorAll('p')[1]?.textContent).toBe(data.gpu_alternatives[0].text);
    expect(host.textContent).not.toContain(data.gpu_alternatives[1].text);
    act(() => root.render(<Component platform={otherPlatform} />));
    expect(host.querySelectorAll('p')[1]?.textContent).toBe(data.gpu_alternatives[1].text);
    expect(host.textContent).not.toContain(data.gpu_alternatives[0].text);
  });

  it.each<OpenClGuidancePlatform>(['windows', 'linux'])('renders exactly the JSON step URLs for %s', (platform) => {
    act(() => root.render(<OpenClGuidance platform={platform} />));
    expect([...host.querySelectorAll('[href]')].map((link) => link.getAttribute('href')))
      .toEqual(guidance.platforms[platform].steps.map((step) => step.url));
  });

  it('reads distinct heading and accessibility wording from JSON', async () => {
    const data = structuredClone(guidance);
    data.title = 'Updated title';
    data.labels = { heading: 'Updated heading', ariaLabel: 'Updated accessibility label' };
    vi.doMock('../../../shared/opencl-driver-guidance.v1.json', () => ({ default: data }));
    const { OpenClGuidance: Component } = await import('./OpenClGuidance');
    act(() => root.render(<Component platform="windows" />));
    expect(host.querySelector('h3')?.textContent).toBe(data.labels.heading);
    expect(host.querySelector('section')?.getAttribute('aria-label')).toBe(data.labels.ariaLabel);
  });

  it('keeps visible wording and all URL literals out of the component source', () => {
    const source = componentSource;
    for (const text of [guidance.title, ...Object.values(guidance.labels), ...Object.values(guidance.reasons),
      ...guidance.gpu_alternatives.map((entry) => entry.text)]) {
      expect(source).not.toContain(text);
    }
    expect(source).not.toMatch(/(?:[a-z][a-z0-9+.-]*:)?\/\/[^\s]/i);
    expect(source).not.toMatch(/(?:aria-label|title|alt|placeholder)\s*=\s*["']/);
    const start = source.indexOf('return <section');
    expect(start).toBeGreaterThanOrEqual(0);
    const jsx = source.slice(start);
    expect(jsx).not.toMatch(/>\s*[A-Za-z][^<>{}]*</);
    expect(jsx).not.toMatch(/\{\s*["'`]/);
  });

  const omissions: [string, (data: typeof guidance) => void][] = [
    ['platform entry', (data) => { Reflect.deleteProperty(data.platforms, 'windows'); }],
    ['warnings', (data) => { Reflect.deleteProperty(data, 'warnings'); }],
    ['GPU alternatives', (data) => { Reflect.deleteProperty(data, 'gpu_alternatives'); }],
    ['steps', (data) => { Reflect.deleteProperty(data.platforms.windows, 'steps'); }],
    ['reason line', (data) => { Reflect.deleteProperty(data.reasons, 'no_device'); }],
    ['reasons', (data) => { Reflect.deleteProperty(data, 'reasons'); }],
  ];

  it.each(omissions)('handles a missing %s without throwing or empty paragraphs', async (name, omit) => {
    const data = structuredClone(guidance);
    omit(data);
    vi.doMock('../../../shared/opencl-driver-guidance.v1.json', () => ({ default: data }));
    const { OpenClGuidance: Component } = await import('./OpenClGuidance');
    act(() => root.render(<Component platform="windows" reason="no_device" />));
    const paragraphs = [...host.querySelectorAll('p')].map((p) => p.textContent);
    expect(paragraphs.every((text) => text && text.trim().length > 0)).toBe(true);
    if (name === 'platform entry') {
      expect(host.innerHTML).toBe('');
      act(() => root.render(<Component platform="linux" />));
      expect(host.textContent).toContain(guidance.platforms.linux.summary);
      return;
    }
    const expected = [
      ...(name === 'reason line' || name === 'reasons' ? [] : [guidance.reasons.no_device]),
      guidance.platforms.windows.summary,
      ...(name === 'GPU alternatives' ? [] : guidance.gpu_alternatives.map((entry) => entry.text)),
      ...(name === 'steps' ? [] : guidance.platforms.windows.steps.map((step) => step.note)),
      ...(name === 'warnings' ? [] : guidance.warnings.map((warning) => warning.text)),
    ];
    expect(paragraphs).toEqual(expected);
    expect([...host.querySelectorAll('a')].map((link) => link.getAttribute('href')))
      .toEqual(name === 'steps' ? [] : guidance.platforms.windows.steps.map((step) => step.url));
    if (name === 'steps') expect(host.querySelector('ul')).toBeNull();
  });

  const malformed: [string, unknown][] = [
    ['null', null],
    ['title', { ...guidance, title: 123 }],
    ['labels', { ...guidance, labels: null }],
    ['platforms', { ...guidance, platforms: null }],
    ['platform entry', { ...guidance, platforms: { windows: null } }],
    ['steps', { ...guidance, platforms: { windows: { summary: 'summary', steps: {} } } }],
    ['step', { ...guidance, platforms: { windows: { summary: 'summary', steps: [null] } } }],
    ['warnings', { ...guidance, warnings: {} }],
    ['warning', { ...guidance, warnings: [null] }],
    ['warning platforms', { ...guidance, warnings: [{ id: 'warning', text: 'warning', platforms: [null] }] }],
    ['GPU alternatives', { ...guidance, gpu_alternatives: {} }],
    ['GPU alternative', { ...guidance, gpu_alternatives: [null] }],
    ['GPU alternative id', { ...guidance, gpu_alternatives: [{ ...guidance.gpu_alternatives[0], id: '' }] }],
    ['GPU alternative text', { ...guidance, gpu_alternatives: [{ ...guidance.gpu_alternatives[0], text: 123 }] }],
    ['GPU alternative platforms', { ...guidance, gpu_alternatives: [{ ...guidance.gpu_alternatives[0], platforms: null }] }],
    ['GPU alternative platform', { ...guidance, gpu_alternatives: [{ ...guidance.gpu_alternatives[0], platforms: ['macos'] }] }],
    ['reason', { ...guidance, reasons: { no_device: 123 } }],
  ];

  it.each(malformed)('rejects malformed %s at module load without crashing the app', async (_name, data) => {
    expect(isOpenClGuidanceData(data)).toBe(false);
    vi.doMock('../../../shared/opencl-driver-guidance.v1.json', () => ({ default: data }));
    const { OpenClGuidance: Component } = await import('./OpenClGuidance');
    act(() => root.render(<Component platform="windows" reason="no_device" />));
    expect(host.innerHTML).toBe('');
  });
});
