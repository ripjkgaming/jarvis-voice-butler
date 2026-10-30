import { ReactNode, useEffect } from 'react';
import { toast as sonnerToast } from 'sonner';
import { WarningIcon } from '@phosphor-icons/react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { CLOSE, isTerminalClose } from '@/lib/voice-session';
import { useVoiceSession, useVoiceSnapshot } from '@/lib/voice-session-react';

interface ToastProps {
  title: ReactNode;
  description: ReactNode;
}

function toastAlert(toast: ToastProps) {
  const { title, description } = toast;

  return sonnerToast.custom(
    (id) => (
      <Alert onClick={() => sonnerToast.dismiss(id)} className="bg-accent w-full md:w-[364px]">
        <WarningIcon weight="bold" />
        <AlertTitle>{title}</AlertTitle>
        {description && <AlertDescription>{description}</AlertDescription>}
      </Alert>
    ),
    { duration: 10_000 }
  );
}

/** Closes that end a call on purpose or by the room owner, not a failure. */
const NORMAL_ENDS: number[] = [CLOSE.CLIENT_REQUESTED, CLOSE.ROOM_CLOSED];

export function useAgentErrors() {
  const { disconnect } = useVoiceSession();
  const { closeCode, lastError } = useVoiceSnapshot();

  useEffect(() => {
    if (closeCode === null || !isTerminalClose(closeCode) || NORMAL_ENDS.includes(closeCode)) {
      return;
    }

    toastAlert({
      title: 'Session ended',
      description: lastError ? <p className="w-full">{lastError}</p> : undefined,
    });

    disconnect();
  }, [closeCode, lastError, disconnect]);
}
