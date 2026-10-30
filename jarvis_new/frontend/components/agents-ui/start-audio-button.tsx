import { type ComponentProps } from 'react';
import { Button } from '@/components/ui/button';
import { useVoiceSession } from '@/lib/voice-session-react';

/**
 * Props for the StartAudioButton component.
 */
export interface StartAudioButtonProps extends ComponentProps<'button'> {
  /**
   * The size of the button.
   * @defaultValue 'default'
   */
  size?: 'default' | 'sm' | 'lg' | 'icon' | 'icon-sm' | 'icon-lg';
  /**
   * The variant of the button.
   * @defaultValue 'default'
   */
  variant?: 'default' | 'destructive' | 'outline' | 'secondary' | 'ghost' | 'link';
  /**
   * The label text to display on the button.
   */
  label: string;
}

/**
 * A button that allows users to start audio playback.
 * Required for browsers that block autoplay of audio.
 * Only renders when audio playback is blocked.
 *
 * @extends ComponentProps<'button'>
 *
 * @example
 * ```tsx
 * <StartAudioButton label="Click to allow audio playback" />
 * ```
 */
export function StartAudioButton({
  size = 'default',
  variant = 'default',
  label,
  onClick,
  ...props
}: StartAudioButtonProps) {
  const { session } = useVoiceSession();

  return (
    <Button
      size={size}
      variant={variant}
      {...props}
      onClick={(event) => {
        // Resuming the AudioContext needs the user gesture of this click.
        void session.resumeAudio();
        onClick?.(event);
      }}
    >
      {label}
    </Button>
  );
}
