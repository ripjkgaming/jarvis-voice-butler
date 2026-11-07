import { DecodeLine } from '../ui/DecodeLine';

export type GateMode = 'probe' | 'offline' | 'unpaired' | 'pairing' | 'register' | 'cold' | 'soft';

export function Gate(p: {
  mode: GateMode;
  row: string;
  error: string;
  errorTone: 'fail' | 'info';
  busy: boolean;
  inputRef: { current: HTMLInputElement | null };
  errRef: { current: HTMLDivElement | null };
  onPassphrase: (pw: string) => void;
  onFingerprint: () => void;
  onRegister: () => void;
}) {
  const disabled = p.busy || p.mode === 'pairing' || p.mode === 'probe' || p.mode === 'offline';
  const status: Record<GateMode, string> = {
    probe: 'Checking connection…',
    offline: 'Laptop unavailable. Check Tailscale and that Jarvis is running.',
    unpaired: 'Pair this phone with your laptop.',
    pairing: 'Pairing your phone…',
    register: 'Set up fingerprint access or enter your passphrase.',
    cold: 'Enter your passphrase to unlock.',
    soft: 'Unlock with your fingerprint or passphrase.',
  };
  return (
    <section class="page" data-page="gate" aria-label="Unlock">
      <h1>Locke Link</h1>
      <div class="panel">
        <h2>{p.mode === 'unpaired' || p.mode === 'pairing' ? 'Pair phone' : 'Unlock'}</h2>
        <p class="glyphrow" role="status">{status[p.mode]}</p>
        {p.mode === 'unpaired' && (
          <p class="pairbox">On your laptop, run <code>uv run python scripts/phone_pair.py</code>, then scan the QR code, open its link, or paste the pairing code below.</p>
        )}
        <div ref={p.errRef} class={`errline${p.errorTone === 'info' ? ' info' : ''}`} role="alert">
          {p.error}
        </div>
        <DecodeLine
          disabled={disabled}
          placeholder={p.mode === 'unpaired' ? 'Pairing code' : 'Passphrase'}
          onSubmit={p.onPassphrase}
          inputRef={p.inputRef}
        />
        <button
          type="button"
          class="fpbtn"
          hidden={p.mode !== 'soft' && p.mode !== 'register'}
          disabled={p.busy}
          onClick={p.mode === 'register' ? p.onRegister : p.onFingerprint}
        >
          {p.mode === 'register' ? 'Set up fingerprint' : 'Unlock with fingerprint'}
        </button>
      </div>
    </section>
  );
}
