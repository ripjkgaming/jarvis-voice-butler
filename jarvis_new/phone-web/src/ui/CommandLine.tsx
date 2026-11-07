/** §2.3 command line: text + ↵, max 2000 chars. Shares the decode line's
 * outer box so the GATE→CORE morph reads as one object. */
import { useRef, useState } from 'preact/hooks';

export function CommandLine({ disabled, onSend }: { disabled: boolean; onSend: (text: string) => Promise<void> | void }) {
  const [text, setText] = useState('');
  const send = useRef<HTMLButtonElement>(null);
  const submit = (e: Event) => {
    e.preventDefault();
    const t = text.trim();
    if (!t || disabled) return;
    setText('');
    void onSend(t);
  };
  return (
    <form class="m line cmd frame ticks" data-morph="core.cmd" onSubmit={submit}>
      <span class="prompt" aria-hidden="true">
        &gt;
      </span>
      <input
        type="text"
        aria-label="Command"
        maxLength={2000}
        enterkeyhint="send"
        autocomplete="off"
        autocapitalize="off"
        spellcheck={false}
        placeholder={disabled ? 'LINK OFFLINE' : 'Message Jarvis'}
        disabled={disabled}
        value={text}
        onInput={(e) => setText((e.target as HTMLInputElement).value)}
      />
      <button ref={send} class="sq send" type="submit" aria-label="Send" disabled={disabled || !text.trim()}>
        Send
      </button>
    </form>
  );
}
