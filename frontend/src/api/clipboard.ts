/** Start the write in the click gesture, before asynchronous data is ready.
 * WebKit otherwise refuses a clipboard write after encoding or fetching. */
export async function writePendingClipboardItem(mimeType: string, blob: Promise<Blob>): Promise<void> {
  // A refused or unsupported write may never consume its pending data.
  void blob.catch(() => undefined);
  await navigator.clipboard.write([new ClipboardItem({ [mimeType]: blob })]);
}
