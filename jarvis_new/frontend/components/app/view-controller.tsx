'use client';

import { AnimatePresence, motion } from 'motion/react';
import { useSessionContext } from '@livekit/components-react';
import type { AppConfig } from '@/app-config';
import { WelcomeView } from '@/components/app/welcome-view';
import { SessionPill } from '@/components/hud/session-pill';

const MotionWelcomeView = motion.create(WelcomeView);

const VIEW_MOTION_PROPS = {
  variants: {
    visible: {
      opacity: 1,
    },
    hidden: {
      opacity: 0,
    },
  },
  initial: 'hidden',
  animate: 'visible',
  exit: 'hidden',
  transition: {
    duration: 0.5,
    ease: 'linear',
  },
};

interface ViewControllerProps {
  appConfig: AppConfig;
}

export function ViewController({ appConfig }: ViewControllerProps) {
  const { isConnected, start } = useSessionContext();

  return (
    <AnimatePresence mode="wait">
      {/* Welcome view — centered card over a blurred HUD */}
      {!isConnected && (
        <div key="welcome-overlay" className="hud-welcome">
          <MotionWelcomeView
            key="welcome"
            {...VIEW_MOTION_PROPS}
            startButtonText={appConfig.startButtonText}
            onStartCall={start}
          />
        </div>
      )}
      {/* Session view — slim floating pill; visuals stay at the orb */}
      {isConnected && (
        <SessionPill
          key="session-pill"
          supportsChatInput={appConfig.supportsChatInput}
          supportsVideoInput={appConfig.supportsVideoInput}
          supportsScreenShare={appConfig.supportsScreenShare}
        />
      )}
    </AnimatePresence>
  );
}
