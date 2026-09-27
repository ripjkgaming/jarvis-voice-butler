'use client';

/** Project archive client: background research (Claude Sonnet) and coding
 *  (opencode) jobs the bridge runs, plus the voice-driven UI command bus.
 *  Contract: src/projects.py + bridge /projects routes. Fail-soft. */
import { bridgeGet, bridgePost } from '@/lib/bridge';

export type ProjectKind = 'research' | 'code';
export type ProjectStatus = 'running' | 'done' | 'failed' | 'cancelled';

export type ProjectMeta = {
  id: string;
  kind: ProjectKind;
  title: string;
  prompt?: string;
  status: ProjectStatus;
  model?: string;
  variant?: string | null;
  directory?: string | null;
  created_at: number;
  updated_at: number;
  summary?: string;
  sources?: string[];
  error?: string | null;
};

export type ProjectDocument = { name: string; title?: string; text: string };

export type ProjectDetail = ProjectMeta & {
  report?: string;
  documents?: ProjectDocument[];
};

/** Voice → UI commands queued by the bridge ("open the second document"). */
export type UiCommand = {
  seq: number;
  action: 'show' | 'select' | 'open_document' | 'close_document' | 'scroll' | 'back' | 'filter';
  /** select: by id (bridge-resolved name match) or by 1-based list index. */
  project_id?: string;
  index?: number;
  filter?: 'all' | 'research' | 'code';
  /** What Sir said, echoed in the voice console. */
  heard?: string;
  mode?: 'start' | 'stop' | 'faster' | 'slower' | 'up' | 'down' | 'top' | 'bottom';
  speed?: 'slow' | 'medium' | 'fast';
};

export async function listProjects(): Promise<ProjectMeta[] | null> {
  const j = await bridgeGet<{ ok?: boolean; projects?: ProjectMeta[] }>('/projects');
  return j?.projects ?? null;
}

export async function getProject(id: string): Promise<ProjectDetail | null> {
  const j = await bridgeGet<{ ok?: boolean; project?: ProjectDetail }>(
    `/projects/${encodeURIComponent(id)}`
  );
  return j?.project ?? null;
}

export type NewProject =
  | { kind: 'research'; topic: string }
  | { kind: 'code'; task: string; directory?: string; variant?: 'high' | 'max' };

export async function createProject(
  body: NewProject
): Promise<{ project?: ProjectMeta; error?: string }> {
  const j = await bridgePost<{ ok?: boolean; project?: ProjectMeta; error?: string }>(
    '/projects',
    body
  );
  if (!j) return { error: 'bridge unreachable' };
  return j.ok ? { project: j.project } : { error: j.error ?? 'rejected' };
}

export async function cancelProject(id: string): Promise<boolean> {
  const j = await bridgePost<{ ok?: boolean }>(`/projects/${encodeURIComponent(id)}/cancel`, {});
  return j?.ok === true;
}

/** Poll the UI command bus. `since` null = fetch the cursor only. */
export async function pollUiCommands(
  since: number | null
): Promise<{ seq: number; commands: UiCommand[] } | null> {
  const q = since === null ? '' : `?since=${since}`;
  const j = await bridgeGet<{ ok?: boolean; seq?: number; commands?: UiCommand[] }>(
    `/projects/ui${q}`
  );
  if (!j || typeof j.seq !== 'number') return null;
  return { seq: j.seq, commands: j.commands ?? [] };
}

/** Documents to show for a project: server list, else synthesized. */
export function documentsOf(p: ProjectDetail | null): ProjectDocument[] {
  if (!p) return [];
  if (p.documents && p.documents.length) return p.documents;
  const docs: ProjectDocument[] = [];
  if (p.report) {
    docs.push({
      name: p.kind === 'code' ? 'output.log' : 'report.md',
      title: p.kind === 'code' ? 'Engine output' : 'Report',
      text: p.report,
    });
  }
  if (p.sources && p.sources.length) {
    docs.push({
      name: 'sources.md',
      title: 'Sources',
      text: p.sources.map((s) => `- ${s}`).join('\n'),
    });
  }
  return docs;
}
