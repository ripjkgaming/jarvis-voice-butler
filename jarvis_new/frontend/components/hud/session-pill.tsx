'use client';

import { useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { useAgent, useSessionContext, useSessionMessages } from '@livekit/components-react';
import { AgentChatTranscript } from '@/components/agents-ui/agent-chat-transcript';
import {
  AgentControlBar,
  type AgentControlBarControls,
} from '@/components/agents-ui/agent-control-bar';
import { useJarvisState } from '@/hooks/hud/use-jarvis-state';

interface SessionPillProps {
  supportsChatInput?: boolean;
  supportsVideoInput?: boolean;
  supportsScreenShare?: boolean;
}

/**
 * Slim floating session pill. Replaces the old fullscreen session view:
 * a state-colored control strip (EQ tick + mic/cam/share/chat/leave)
 * with the transcript as a popover card. All heavy visuals stay at the orb.
 */
export function SessionPill({
  supportsChatInput = true,
  supportsVideoInput = true,
  supportsScreenShare = true,
}: SessionPillProps) {
  const session = useSessionContext();
  const { messages } = useSessionMessages(session);
  const { state: agentState } = useAgent();
  const { color } = useJarvisState();
  const [isChatOpen, setIsChatOpen] = useState(false);

  const controls: AgentControlBarControls = {
    leave: true,
    microphone: true,
    chat: supportsChatInput,
    camera: supportsVideoInput,
    screenShare: supportsScreenShare,
  };

  return (
    <>
      <AnimatePresence>
        {isChatOpen && (
          <motion.div
            key="hud-transcript"
            initial={{ opacity: 0, y: 12, filter: 'blur(8px)' }}
            animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
            exit={{ opacity: 0, y: 12, filter: 'blur(8px)' }}
            transition={{ duration: 0.25, ease: 'easeOut' }}
            className="hud-transcript"
          >
            <AgentChatTranscript
              agentState={agentState}
              messages={messages}
              className="max-h-[46svh] **:data-[slot=message-scroller-content]:p-4"
            />
          </motion.div>
        )}
      </AnimatePresence>

      <div className="hud-pill" style={{ ['--jarvis-state' as string]: color }}>
        <span className="hud-pill__eq" aria-hidden="true">
          {Array.from({ length: 5 }, (_, i) => (
            <i key={i} style={{ animationDelay: `${i * 0.14}s` }} />
          ))}
        </span>
        <AgentControlBar
          variant="livekit"
          controls={controls}
          isChatOpen={isChatOpen}
          isConnected={session.isConnected}
          onDisconnect={session.end}
          onIsChatOpenChange={setIsChatOpen}
          className="hud-pill__controls"
        />
      </div>
    </>
  );
}
