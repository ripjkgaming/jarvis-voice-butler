'use client';

import { CallHud, type CallHudProps } from './call-hud';
import { useCallFeed } from './use-call-events';

/** Drop-in live overlay: listens to the HUD data channel and the bridge
 *  call feed, and shows the Calls HUD while a call exists (null
 *  otherwise). Must render inside
 *  <AgentSessionProvider> / <VoiceSessionProvider>. See WIRING.md. */
export function CallHudLive({
  bridge = true,
  ...props
}: Omit<CallHudProps, 'call' | 'level'> & {
  /** Poll the bridge's /calls feed too (default on; off when the data
   *  channel already carries call events). */
  bridge?: boolean;
}) {
  const { call, level } = useCallFeed({ bridge });
  if (!call) return null;
  return <CallHud call={call} level={level} {...props} />;
}
