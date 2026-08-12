import { useCallback, useState } from 'react';
import { sendDesignToCad, type WgLinkExportResponse } from '../api/designIo';
import { useDesignStore } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { filenameStem } from '../viewport/presentation';

export function sentToCadMessage(result: WgLinkExportResponse): string {
  return `Sent to CAD · sequence ${result.sequence} · ${result.bundlePath}`;
}

/** Write a .wglink bundle for the design on screen.
 *
 * Shared by the design file menu and the CAD Link panel so the two entry points
 * cannot drift: both adopt the identity the server committed the export against,
 * which is what keeps the CAD-link badge honest after an unsaved design is sent.
 * Failures are raised, so each call site presents them its own way.
 */
export function useSendToCad(): { send: () => Promise<WgLinkExportResponse>; sending: boolean } {
  const design = useDesignStore((state) => state.design);
  const revision = useDesignStore((state) => state.designRevision);
  const filename = useDocumentStore((state) => state.filename);
  const identity = useDocumentStore((state) => state.identity);
  const setCadLink = useDocumentStore((state) => state.setCadLink);
  const [sending, setSending] = useState(false);

  const send = useCallback(async () => {
    setSending(true);
    try {
      const result = await sendDesignToCad(design, revision, filenameStem(filename), identity);
      // The export committed this design state to the registry, so the CAD link
      // is current. The unsaved dot stays lit: no .cfg was written.
      if (result.identity) setCadLink(result.identity, 'current');
      return result;
    } finally {
      setSending(false);
    }
  }, [design, filename, identity, revision, setCadLink]);

  return { send, sending };
}
