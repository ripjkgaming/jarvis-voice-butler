/** Passkeys (platform authenticator, ES256) and the step-up flow (PLAN.md §7.2).
 * Options travel as JSON with base64url buffers; responses are sent back the
 * same way. The private key never leaves the phone's authenticator. */
import { api } from './api';

export function b64uToBuf(s: string): ArrayBuffer {
  const pad = '='.repeat((4 - (s.length % 4)) % 4);
  const bin = atob((s + pad).replace(/-/g, '+').replace(/_/g, '/'));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
}

export function bufToB64u(buf: ArrayBuffer | ArrayBufferView): string {
  const u8 = buf instanceof ArrayBuffer ? new Uint8Array(buf) : new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
  let bin = '';
  for (let i = 0; i < u8.length; i++) bin += String.fromCharCode(u8[i]);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

export function creationOptions(o: Json): PublicKeyCredentialCreationOptions {
  return {
    ...o,
    challenge: b64uToBuf(o.challenge),
    user: { ...o.user, id: b64uToBuf(o.user.id) },
    excludeCredentials: (o.excludeCredentials ?? []).map((c: Json) => ({ ...c, id: b64uToBuf(c.id) })),
  } as PublicKeyCredentialCreationOptions;
}

export function requestOptions(o: Json): PublicKeyCredentialRequestOptions {
  return {
    challenge: b64uToBuf(o.challenge),
    rpId: o.rpId,
    timeout: o.timeout,
    userVerification: o.userVerification,
    allowCredentials: (o.allowCredentials ?? []).map((c: Json) => ({ type: 'public-key', id: b64uToBuf(c.id), transports: c.transports })),
  };
}

export function registrationJSON(cred: PublicKeyCredential): Json {
  const r = cred.response as AuthenticatorAttestationResponse;
  return {
    id: cred.id,
    rawId: bufToB64u(cred.rawId),
    type: cred.type,
    response: { clientDataJSON: bufToB64u(r.clientDataJSON), attestationObject: bufToB64u(r.attestationObject) },
  };
}

export function assertionJSON(cred: PublicKeyCredential): Json {
  const r = cred.response as AuthenticatorAssertionResponse;
  return {
    id: cred.id,
    rawId: bufToB64u(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: bufToB64u(r.clientDataJSON),
      authenticatorData: bufToB64u(r.authenticatorData),
      signature: bufToB64u(r.signature),
      userHandle: r.userHandle ? bufToB64u(r.userHandle) : null,
    },
  };
}

export const webauthnAvailable = () => typeof PublicKeyCredential !== 'undefined' && !!navigator.credentials;

/** Create a passkey from server options and register it. */
export async function registerPasskey(options: Json): Promise<void> {
  const cred = (await navigator.credentials.create({ publicKey: creationOptions(options) })) as PublicKeyCredential | null;
  if (!cred) throw new Error('passkey cancelled');
  await api('POST', '/phone/v1/passkey', { credential: registrationJSON(cred) });
}

/** Fresh fingerprint for `purpose` ("gate" or a step-up action). */
export async function assertion(purpose: string): Promise<Json> {
  const opts = await api<Json>('POST', '/phone/v1/challenge', { purpose });
  const cred = (await navigator.credentials.get({ publicKey: requestOptions(opts) })) as PublicKeyCredential | null;
  if (!cred) throw new Error('fingerprint cancelled');
  return assertionJSON(cred);
}
