import { useMicToggle, useVoiceSnapshot } from '@/lib/voice-session-react';

/** Track sources by the names the controls use (string keys, no SDK enum). */
export type TrackSource = 'camera' | 'microphone' | 'screen_share';

export interface PublishPermissions {
  camera: boolean;
  microphone: boolean;
  screenShare: boolean;
  data: boolean;
}

/** Mic and data are allowed only in speaker mode (docs/voice-transport.md).
 *  Camera and screen share have no path in the voice transport yet. */
export function usePublishPermissions(): PublishPermissions {
  const speaker = useVoiceSnapshot().mode === 'speaker';

  return {
    camera: false,
    microphone: speaker,
    screenShare: false,
    data: speaker,
  };
}

export interface UseInputControlsProps {
  saveUserChoices?: boolean;
  onDisconnect?: () => void;
  onDeviceError?: (error: { source: TrackSource; error: Error }) => void;
}

export interface InputToggle {
  enabled: boolean;
  pending: boolean;
  toggle: (next?: boolean) => void | Promise<void>;
}

export interface UseInputControlsReturn {
  /** Level source for the mic visualizer (`useAudioLevel('mic')`). */
  microphoneTrack?: 'mic';
  microphoneToggle: InputToggle;
  cameraToggle: InputToggle;
  screenShareToggle: InputToggle;
  handleAudioDeviceChange: (deviceId: string) => void;
  handleVideoDeviceChange: (deviceId: string) => void;
  handleMicrophoneDeviceSelectError: (error: Error) => void;
  handleCameraDeviceSelectError: (error: Error) => void;
}

const noop = () => {};

export function useInputControls({
  onDeviceError,
}: UseInputControlsProps = {}): UseInputControlsReturn {
  const mic = useMicToggle();

  // Device selection is not in the voice transport: no device lists, no
  // persisted choices. Camera and screen share are never on.
  const microphoneToggle: InputToggle = {
    enabled: mic.enabled,
    pending: false,
    toggle: mic.toggle,
  };
  const cameraToggle: InputToggle = { enabled: false, pending: false, toggle: noop };
  const screenShareToggle: InputToggle = { enabled: false, pending: false, toggle: noop };

  return {
    microphoneTrack: 'mic',
    cameraToggle,
    microphoneToggle,
    screenShareToggle,
    handleAudioDeviceChange: noop,
    handleVideoDeviceChange: noop,
    handleMicrophoneDeviceSelectError: (error: Error) =>
      onDeviceError?.({ source: 'microphone', error }),
    handleCameraDeviceSelectError: (error: Error) => onDeviceError?.({ source: 'camera', error }),
  };
}
