import { describe, expect, it, vi } from 'vitest';
import { loadMechonixPrintFile } from '../../hooks/useMechonixPrintLink';
import type { LibraryFileListItem } from '../../api/client';

const file = { id: 145, filename: 'part.gcode.3mf', file_type: 'gcode.3mf', variant_group_id: 7 } as LibraryFileListItem;
const settle = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };

describe('Restock print link', () => {
  it.each(['0', '-1', '1.5', '1e2', '1junk', '9007199254740992'])('rejects invalid ID %s', async id => {
    const get = vi.fn().mockResolvedValue(file);
    const open = vi.fn();
    const fail = vi.fn();
    loadMechonixPrintFile(id, true, get, open, fail);
    await settle();
    expect(get).not.toHaveBeenCalled();
    expect(open).not.toHaveBeenCalled();
    expect(fail).toHaveBeenCalledOnce();
  });

  it('opens exactly the linked file without selecting its variant group', async () => {
    const get = vi.fn().mockResolvedValue(file);
    const open = vi.fn();
    loadMechonixPrintFile('145', true, get, open, vi.fn());
    await settle();
    expect(get).toHaveBeenCalledWith(145);
    expect(open).toHaveBeenCalledWith({ ...file, variant_group_id: null });
  });

  it('requires print permission before reading a file', async () => {
    const get = vi.fn();
    const open = vi.fn();
    const fail = vi.fn();
    loadMechonixPrintFile('145', false, get, open, fail);
    await settle();
    expect(get).not.toHaveBeenCalled();
    expect(open).not.toHaveBeenCalled();
    expect(fail).toHaveBeenCalledOnce();
  });

  it('does not open source-only or unavailable files', async () => {
    for (const get of [vi.fn().mockResolvedValue({ ...file, filename: 'part.stl', file_type: 'stl' }), vi.fn().mockRejectedValue(new Error('Forbidden'))]) {
      const open = vi.fn();
      const fail = vi.fn();
      loadMechonixPrintFile('145', true, get, open, fail);
      await settle();
      expect(open).not.toHaveBeenCalled();
      expect(fail).toHaveBeenCalledOnce();
    }
  });

  it('ignores a response after navigation or unmount', async () => {
    const open = vi.fn();
    const fail = vi.fn();
    const cancel = loadMechonixPrintFile('145', true, vi.fn().mockResolvedValue(file), open, fail);
    cancel();
    await settle();
    expect(open).not.toHaveBeenCalled();
    expect(fail).not.toHaveBeenCalled();
  });
});
