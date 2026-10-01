'use client';

import { AnimatePresence, motion } from 'motion/react';
import { useRoom } from '@/hooks/hud/use-room-state';

/**
 * Buttonless call indicator. The Tauri webview has no WebRTC (system
 * WebKitGTK exposes no RTCPeerConnection), so the HUD can never join a
 * LiveKit room — voice lives entirely in the native wake client. This
 * watcher mirrors the call lifecycle instead: a live wake room reads as
 * an ongoing call, silence reads as standby. No buttons, no errors.
 */
export function ViewController() {
  // Shared /room poll (one loop for the whole HUD, paused while hidden).
  const room = useRoom();

  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={room ? 'incall' : 'standby'}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.4 }}
        className="hud-linking"
        data-state={room ? 'incall' : 'standby'}
        aria-live="polite"
      >
        <span
          className="hud-linking__pulse"
          data-state={room ? 'incall' : 'standby'}
          aria-hidden="true"
        />
        {room ? `in call · ${room}` : 'standing by, Sir…'}
      </motion.div>
    </AnimatePresence>
  );
}
