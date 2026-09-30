'use client';

/** Drafts window client: reply drafts the mail watcher wrote for Sir.
 *  Contract: src/drafts_ui.py + bridge GET /drafts, POST /drafts/close.
 *  Fail-soft: null means the bridge could not be reached. */
import { bridgeGet, bridgePost } from '@/lib/bridge';

export type DraftStatus = 'pending' | 'announced';

export type ReplyDraft = {
  id: string;
  to: string;
  subject: string;
  body: string;
  summary?: string;
  sender?: string;
  created?: number | string;
  status: DraftStatus;
  priority?: 'high' | 'normal' | string;
};

export async function listDrafts(): Promise<ReplyDraft[] | null> {
  const j = await bridgeGet<{ ok?: boolean; drafts?: ReplyDraft[] }>('/drafts');
  return j?.drafts ?? null;
}

/** Ask the bridge to hide this window. It only closes when no drafts remain. */
export async function closeIfEmpty(): Promise<boolean> {
  const j = await bridgePost<{ ok?: boolean; closed?: boolean }>('/drafts/close', {});
  return j?.closed === true;
}
