import { beforeEach, describe, expect, it, vi } from 'vitest';
import { resetCadReturnStore, useCadReturnStore } from './cadReturn';
import { consumeParkedSolveCommand, parkedSolveCommandStore } from './solveCommand';


describe('parked CAD solve acknowledgement', () => {
  beforeEach(() => {
    resetCadReturnStore();
    parkedSolveCommandStore.clear();
    useCadReturnStore.setState({
      selectedBundle: {
        name: 'speaker.wgreturn',
        bundlePath: 'wgreturn/speaker.wgreturn',
        modifiedAt: '2026-08-22T00:00:00Z',
        readable: true,
        documentName: 'Speaker',
        requestId: null,
        sourceCount: 0,
        instanceCount: 0,
        designIds: [],
        sources: [],
      },
    });
    parkedSolveCommandStore.park({
      commandId: 'cmd-1',
      bundlePath: 'wgreturn/speaker.wgreturn',
      blockers: [],
      parkedAt: '2026-08-22T00:00:00Z',
    });
  });

  it('keeps the command and rejects visibly when outcome persistence fails', async () => {
    const failed = vi.fn<typeof fetch>().mockResolvedValue(new Response(
      JSON.stringify({ detail: 'ledger unavailable' }),
      { status: 503, headers: { 'Content-Type': 'application/json' } },
    ));

    await expect(consumeParkedSolveCommand('job-1', failed)).rejects.toThrow(
      /job was created.*acknowledgement failed.*Retry/s,
    );
    expect(parkedSolveCommandStore.getSnapshot().command?.commandId).toBe('cmd-1');

    const recovered = vi.fn<typeof fetch>().mockResolvedValue(new Response(
      JSON.stringify({ state: 'accepted', cleared: true }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    ));
    await expect(consumeParkedSolveCommand('job-1', recovered)).resolves.toBeUndefined();
    expect(parkedSolveCommandStore.getSnapshot().command).toBeNull();
  });
});
