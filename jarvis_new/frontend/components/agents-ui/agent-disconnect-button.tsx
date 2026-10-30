'use client';

import { type ComponentProps } from 'react';
import { type VariantProps } from 'class-variance-authority';
import { PhoneOffIcon } from 'lucide-react';
import { Button, type buttonVariants } from '@/components/ui/button';
import { cn } from '@/lib/shadcn/utils';
import { useVoiceSession } from '@/lib/voice-session-react';

/**
 * Props for the AgentDisconnectButton component.
 */
export interface AgentDisconnectButtonProps
  extends ComponentProps<'button'>,
    VariantProps<typeof buttonVariants> {
  /**
   * Custom icon to display. Defaults to PhoneOffIcon.
   */
  icon?: React.ReactNode;
  /**
   * The size of the button.
   * @default 'default'
   */
  size?: 'default' | 'sm' | 'lg' | 'icon';
  /**
   * The variant of the button.
   * @default 'destructive'
   */
  variant?: 'default' | 'outline' | 'destructive' | 'ghost' | 'link';
  /**
   * The children to render.
   */
  children?: React.ReactNode;
  /**
   * The callback for when the button is clicked.
   */
  onClick?: (event: React.MouseEvent<HTMLButtonElement>) => void;
}

/**
 * A button to disconnect from the current agent session.
 * Ends the call (voice session disconnect) when clicked.
 *
 * @extends ComponentProps<'button'>
 *
 * @example
 * ```tsx
 * <AgentDisconnectButton onClick={() => console.log('Disconnecting...')} />
 * ```
 */
export function AgentDisconnectButton({
  icon,
  size = 'default',
  variant = 'destructive',
  children,
  onClick,
  ...props
}: AgentDisconnectButtonProps) {
  const { disconnect } = useVoiceSession();
  const handleClick = (event: React.MouseEvent<HTMLButtonElement>) => {
    onClick?.(event);
    disconnect();
  };

  return (
    <Button size={size} variant={variant} onClick={handleClick} {...props}>
      {icon ?? <PhoneOffIcon />}
      {children ?? <span className={cn(size?.includes('icon') && 'sr-only')}>End call</span>}
    </Button>
  );
}
