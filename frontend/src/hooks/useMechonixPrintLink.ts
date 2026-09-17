import { useEffect, useRef } from 'react';
import { api } from '../api/client';
import type { LibraryFileListItem } from '../api/client';
import { isSlicedLibraryFile } from '../utils/libraryFiles';

/** Open the existing print dialog for precisely the linked file. Never submit a print. */
export function loadMechonixPrintFile(
  id: string,
  canPrint: boolean,
  getFile: (id: number) => Promise<LibraryFileListItem>,
  open: (file: LibraryFileListItem) => void,
  fail: (message: string) => void,
): () => void {
  let cancelled = false;
  if (!/^[1-9][0-9]*$/.test(id) || !Number.isSafeInteger(Number(id))) {
    fail('Ungültige Bambuddy-Datei. Bitte die Dateiliste in Restock aktualisieren.');
  } else if (!canPrint) {
    fail('Keine Berechtigung zum Drucken in Bambuddy.');
  } else {
    Promise.resolve().then(() => getFile(Number(id))).then(file => {
      if (cancelled) return;
      if (!isSlicedLibraryFile(file)) throw new Error('Diese Datei ist noch nicht für den Druck vorbereitet.');
      open({ ...file, variant_group_id: null });
    }).catch(error => {
      if (!cancelled) fail(error instanceof Error ? error.message : 'Druckdatei nicht verfügbar. Bitte die Dateiliste in Restock aktualisieren.');
    });
  }
  return () => { cancelled = true; };
}

export function useMechonixPrintLink(
  id: string | null,
  canPrint: boolean,
  open: (file: LibraryFileListItem) => void,
  fail: (message: string) => void,
) {
  const seen = useRef<string | null>(null);
  useEffect(() => {
    if (!id || seen.current === id) return;
    return loadMechonixPrintFile(id, canPrint,
      fileId => api.getLibraryFile(fileId).then(file => ({ ...file, fs_modified_at: null })),
      file => { seen.current = id; open(file); },
      message => { seen.current = id; fail(message); });
  }, [id, canPrint, open, fail]);
}
