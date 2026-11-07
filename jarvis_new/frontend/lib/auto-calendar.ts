import { bridgeGet, bridgePost } from '@/lib/bridge';

export type CalendarRecord = {
  id?: string;
  title?: string;
  date?: string;
  start?: string;
  location?: string;
  when?: string;
  status?: string;
  source?: string;
  sender?: string;
  reason?: string;
  error?: string;
  google_id?: string;
  event?: { title?: string; date?: string; start?: string; location?: string };
};
export type CalendarStatus = {
  ok?: boolean;
  error?: string;
  enabled?: boolean;
  timezone?: string;
  whatsapp?: { enabled?: boolean; connected?: boolean; last_scan?: number; detail?: string };
  calendar?: {
    configured?: boolean;
    verified_at?: number;
    connected?: boolean;
    account?: string;
    calendar_id?: string;
    detail?: string;
  };
  presence?: { state?: string; detail?: string };
  reminders?: { deferred?: number; detail?: string };
  pending?: CalendarRecord[];
  retry?: CalendarRecord[];
  recent?: CalendarRecord[];
};
export function calendarStatus(signal?: AbortSignal) {
  return bridgeGet<CalendarStatus>('/auto-calendar/status', 8000, {
    signal,
    includeErrorBody: true,
  });
}
export function dismissCalendarSuggestion(id: string) {
  return bridgePost<{ ok?: boolean; error?: string }>('/auto-calendar/review', {
    id,
    action: 'dismiss',
  });
}
