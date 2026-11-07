/** Plain password input; values never appear in decorative echoes. */
export function DecodeLine(p: {
  disabled: boolean;
  placeholder: string;
  onSubmit: (pw: string) => void;
  inputRef: { current: HTMLInputElement | null };
}) {
  return <form class="line decode" onSubmit={(e) => {
    e.preventDefault();
    const v = p.inputRef.current?.value ?? '';
    if (v && !p.disabled) p.onSubmit(v);
  }}>
    <input ref={p.inputRef} type="password" name="passphrase" autocomplete="current-password"
      enterkeyhint="go" aria-label="Passphrase" placeholder={p.placeholder} maxLength={1024}
      disabled={p.disabled} data-first="" />
    <button type="submit" disabled={p.disabled}>Continue</button>
  </form>;
}
export function clearDecode(input: HTMLInputElement | null) {
  if (input) { input.value = ''; input.dispatchEvent(new Event('input', { bubbles: true })); }
}
