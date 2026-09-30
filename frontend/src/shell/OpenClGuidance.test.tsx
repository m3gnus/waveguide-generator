import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import guidance from '../../../shared/opencl-driver-guidance.v1.json';
import { OpenClGuidance, type OpenClGuidancePlatform, type OpenClGuidanceReason } from './OpenClGuidance';

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
  });

  it.each<OpenClGuidancePlatform>(['windows', 'linux'])('renders the %s summary, steps and applicable warnings', (platform) => {
    act(() => root.render(<OpenClGuidance platform={platform} />));
    const block = guidance.platforms[platform];
    const warnings = guidance.warnings.filter((warning) => warning.platforms.includes(platform));
    expect(host.querySelector('section')?.getAttribute('aria-label')).toBe('CPU OpenCL runtime');
    expect([...host.querySelectorAll('p')].map((p) => p.textContent))
      .toEqual([block.summary, ...block.steps.map((step) => step.note), ...warnings.map((warning) => warning.text)]);
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

  it.each<OpenClGuidanceReason>(['no_device', 'smoke_test_failed', 'smoke_test_timeout', 'pocl_windows'])('renders the shared %s reason', (reason) => {
    act(() => root.render(<OpenClGuidance platform="windows" reason={reason} />));
    expect(host.querySelector('p')?.textContent).toBe(guidance.reasons[reason]);
    for (const [code, text] of Object.entries(guidance.reasons)) {
      if (code !== reason) expect(host.textContent).not.toContain(text);
    }
  });

  it('updates the platform and clears a previous reason without retaining the Windows warning', () => {
    act(() => root.render(<OpenClGuidance platform="windows" reason="no_device" />));
    act(() => root.render(<OpenClGuidance platform="linux" />));
    expect(host.textContent).toContain(guidance.platforms.linux.summary);
    expect(host.textContent).not.toContain(guidance.reasons.no_device);
    expect(host.textContent).not.toContain(guidance.warnings[0].text);
    expect(host.querySelectorAll('a')).toHaveLength(guidance.platforms.linux.steps.length);
  });
});
