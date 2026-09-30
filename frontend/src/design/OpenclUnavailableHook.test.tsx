import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import guidance from '../../../shared/opencl-driver-guidance.v1.json';
import type { EngineCapability } from '../jobs/actions';
import type { OpenClGuidancePlatform, OpenClGuidanceReason } from '../shell/OpenClGuidance';
import { OpenclUnavailableHook } from './OpenclUnavailableHook';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const reasons = Object.keys(guidance.reasons) as OpenClGuidanceReason[];

describe('OpenclUnavailableHook', () => {
  let host: HTMLDivElement;
  let root: Root;
  const engine: EngineCapability = {
    name: 'bempp', available: true, reason: 'unparsed prose', version: null,
    fast_paths: [], assembly_backend: 'numba',
  };

  beforeEach(() => {
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });
  afterEach(() => {
    act(() => root.unmount());
    host.remove();
  });

  it.each(reasons)('renders the shared Windows block and %s reason', (reason) => {
    act(() => root.render(<OpenclUnavailableHook platform="windows"
      engine={{ ...engine, opencl_unavailable_reason: reason }} />));
    const block = guidance.platforms.windows;
    expect([...host.querySelectorAll('p')].map((p) => p.textContent)).toEqual([
      guidance.reasons[reason], block.summary,
      ...guidance.gpu_alternatives.filter((entry) => entry.platforms.includes('windows')).map((entry) => entry.text),
      ...block.steps.map((step) => step.note),
      ...guidance.warnings.filter((warning) => warning.platforms.includes('windows')).map((warning) => warning.text),
    ]);
    expect([...host.querySelectorAll('a')].map((a) => ({ label: a.textContent, url: a.getAttribute('href') })))
      .toEqual(block.steps.map(({ label, url }) => ({ label, url })));
  });

  it.each(Object.keys(guidance.platforms) as OpenClGuidancePlatform[])('renders %s without a reason', (platform) => {
    act(() => root.render(<OpenclUnavailableHook platform={platform} engine={engine} />));
    const block = guidance.platforms[platform];
    expect([...host.querySelectorAll('p')].map((p) => p.textContent)).toEqual([
      block.summary,
      ...guidance.gpu_alternatives.filter((entry) => entry.platforms.includes(platform)).map((entry) => entry.text),
      ...block.steps.map((step) => step.note),
      ...guidance.warnings.filter((warning) => warning.platforms.includes(platform)).map((warning) => warning.text),
    ]);
    expect([...host.querySelectorAll('a')].map((a) => a.getAttribute('href')))
      .toEqual(block.steps.map((step) => step.url));
  });

  it.each(['opencl', null, undefined] as const)('renders nothing for backend %s', (backend) => {
    act(() => root.render(<OpenclUnavailableHook platform="windows"
      engine={{ ...engine, assembly_backend: backend, opencl_unavailable_reason: 'no_device' }} />));
    expect(host.innerHTML).toBe('');
  });

  it.each(['darwin', 'macos', 'unknown', 'toString', null, undefined])('renders nothing on host %s', (platform) => {
    act(() => root.render(<OpenclUnavailableHook platform={platform} engine={engine} />));
    expect(host.innerHTML).toBe('');
  });

  it('renders nothing for another engine or before capabilities arrive', () => {
    act(() => root.render(<OpenclUnavailableHook platform="windows" engine={{ ...engine, name: 'metal' }} />));
    expect(host.innerHTML).toBe('');
    act(() => root.render(<OpenclUnavailableHook platform="windows" engine={undefined} />));
    expect(host.innerHTML).toBe('');
  });
});
