/** Mock bridge (route interception) + virtual authenticator for e2e. The mock
 * mirrors the real bridge's status codes and shapes (docs/PHONE_API.md). */
import { generateKeyPairSync, randomBytes } from 'node:crypto';
import type { CDPSession, Page, Route } from '@playwright/test';

export type Laptop = {
  host: string;
  guest: boolean;
  locked: boolean | null;
  screens: 'on' | 'off' | null;
  mic_muted: boolean | null;
  presence: string;
  power: { percent: number; charging: boolean } | null;
};

export class Mock {
  paired = false;
  gate: 'cold' | 'soft' | 'open' = 'cold';
  passkey = false;
  fails = 0;
  lockUntil = 0;
  offline = false;
  failNext: string | null = null;
  delayControlMs = 150;
  controls: { action: string; assertion: boolean }[] = [];
  commands: string[] = [];
  mints = 0;
  pw = 'open sesame';
  laptop: Laptop = {
    host: 'ripjk',
    guest: false,
    locked: false,
    screens: 'on',
    mic_muted: false,
    presence: 'present',
    power: { percent: 87, charging: true },
  };
  voice = { call_live: false, voice_owner: null, agent_state: null, waking: false };

  state() {
    return {
      ok: true,
      api_version: 1,
      ts: Date.now() / 1000,
      laptop: this.laptop,
      voice: this.voice,
      gate: { state: 'open', expires_in_s: 40000, soft_in_s: 600 },
      caps: { remote: false, presence: true, webauthn: true },
      ages: { locked: 0.4, screens: 3.1, mic_muted: 0.2, presence: 22 },
    };
  }

  async install(page: Page) {
    await page.route('**/api/**', (route) => this.handle(route));
  }

  private json(route: Route, status: number, body: unknown, headers: Record<string, string> = {}) {
    return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body), headers });
  }

  private async handle(route: Route) {
    if (this.offline) return route.abort('internetdisconnected');
    const req = route.request();
    const path = new URL(req.url()).pathname.replace(/^\/api/, '');
    const body = req.postDataJSON?.() ?? {};
    if (req.headers()['x-jarvis-phone'] !== '1') return this.json(route, 401, { ok: false, error: 'unauthorized', auth: 'pair' });
    if (path === '/phone/v1/pair') {
      if (body.code !== 'ABCDE12345') return this.json(route, 401, { ok: false, error: 'pairing code invalid or expired' });
      this.paired = true;
      return this.json(route, 200, {
        ok: true,
        device_id: 'jl-1a2b3c4d',
        registration: {
          challenge: 'cmVnaXN0ZXItY2hhbGxlbmdlLTAwMDAwMDAwMDAwMDAwMA',
          rp: { id: 'localhost', name: 'LOCKE//LINK' },
          user: { id: 'amwtMWEyYjNjNGQ', name: 'owner@jl-1a2b3c4d', displayName: 'phone' },
          pubKeyCredParams: [{ type: 'public-key', alg: -7 }],
          authenticatorSelection: { authenticatorAttachment: 'platform', residentKey: 'preferred', userVerification: 'required' },
          attestation: 'none',
          timeout: 60000,
          excludeCredentials: [],
        },
      });
    }
    if (!this.paired) return this.json(route, 401, { ok: false, error: 'unauthorized', auth: 'pair' });
    if (path === '/phone/v1/passkey') {
      this.passkey = !!body.credential?.response?.attestationObject;
      return this.json(route, this.passkey ? 200 : 400, { ok: this.passkey });
    }
    if (path === '/phone/v1/challenge') {
      if (body.purpose === 'register') return this.json(route, 200, { ok: true, registration: (await this.regOpts()) });
      if (!this.passkey) return this.json(route, 409, { ok: false, error: 'no passkey registered' });
      return this.json(route, 200, {
        ok: true,
        challenge: 'Z2F0ZS1jaGFsbGVuZ2UtMDAwMDAwMDAwMDAwMDAwMDAwMDA',
        rpId: 'localhost',
        allowCredentials: [],
        timeout: 60000,
        userVerification: 'required',
      });
    }
    if (path === '/phone/v1/gate') {
      if (Date.now() < this.lockUntil)
        return this.json(route, 429, { ok: false, error: 'lockout', tries_left: 0, retry_after: Math.ceil((this.lockUntil - Date.now()) / 1000) });
      const good = body.assertion ? this.gate === 'soft' && !!body.assertion.response?.signature : body.passphrase === this.pw;
      if (!good) {
        this.fails++;
        const wait = this.fails >= 5 ? 60 * 2 ** (this.fails - 5) : 0;
        if (wait) this.lockUntil = Date.now() + wait * 1000;
        return this.json(route, 401, { ok: false, error: 'denied', tries_left: Math.max(0, 5 - this.fails), retry_after: wait });
      }
      this.fails = 0;
      this.gate = 'open';
      return this.json(route, 200, { ok: true, gate: 'open', expires_in_s: 43200, soft_after_s: 600 });
    }
    if (this.gate !== 'open') return this.json(route, 401, { ok: false, error: 'gate', gate: this.gate });
    if (path === '/phone/v1/state') return this.json(route, 200, this.state());
    if (path === '/phone/telemetry') return this.json(route, 200, { ok: true });
    if (path === '/phone/v1/command') {
      this.commands.push(body.text);
      return this.json(route, 200, { ok: true, reply: `Done: ${body.text}`, via_route: true, api_version: 1 });
    }
    if (path === '/phone/v1/session') {
      this.mints++;
      return this.json(route, 200, {
        ok: true,
        api_version: 1,
        serverUrl: 'ws://127.0.0.1:7880/voice',
        roomName: `room${this.mints}`,
        participantName: 'phone',
        participantToken: `tok${this.mints}`,
      });
    }
    if (path === '/phone/v1/control') return this.control(route, body);
    return this.json(route, 404, { ok: false, error: 'unknown route' });
  }

  private async regOpts() {
    return {
      challenge: 'cmVnaXN0ZXItY2hhbGxlbmdlLTAwMDAwMDAwMDAwMDAwMQ',
      rp: { id: 'localhost', name: 'LOCKE//LINK' },
      user: { id: 'amwtMWEyYjNjNGQ', name: 'owner', displayName: 'phone' },
      pubKeyCredParams: [{ type: 'public-key', alg: -7 }],
      authenticatorSelection: { authenticatorAttachment: 'platform', residentKey: 'preferred', userVerification: 'required' },
      attestation: 'none',
      timeout: 60000,
      excludeCredentials: [],
    };
  }

  private async control(route: Route, body: { action: string; assertion?: { response?: { signature?: string } } }) {
    const a = body.action;
    const stepup = ['guest_off', 'unlock', 'restart', 'shutdown'].includes(a);
    if (stepup && !body.assertion?.response?.signature) return this.json(route, 403, { ok: false, error: 'stepup', stepup: a });
    this.controls.push({ action: a, assertion: !!body.assertion });
    await new Promise((r) => setTimeout(r, this.delayControlMs));
    if (this.failNext === a) {
      this.failNext = null;
      return this.json(route, 200, { ok: false, action: a, status: 'failed', readback: {}, state: this.laptop, error: `${a} failed` });
    }
    const L = this.laptop;
    let status = 'verified';
    if (a === 'guest_on') L.guest = true;
    if (a === 'guest_off') L.guest = false;
    if (a === 'lock') L.locked = true;
    if (a === 'unlock') L.locked = false;
    if (a === 'screens_off') L.screens = 'off';
    if (a === 'screens_on') L.screens = 'on';
    if (a === 'mic_mute') L.mic_muted = true;
    if (a === 'mic_unmute') L.mic_muted = false;
    if (['sleep', 'restart', 'shutdown'].includes(a)) status = 'dispatched_unverified';
    return this.json(route, 200, { ok: true, action: a, status, readback: {}, state: L });
  }
}

export async function virtualAuthenticator(page: Page, seed = false): Promise<CDPSession> {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('WebAuthn.enable');
  const { authenticatorId } = await cdp.send('WebAuthn.addVirtualAuthenticator', {
    options: { protocol: 'ctap2', transport: 'internal', hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true },
  });
  if (seed) {
    // An already-registered passkey for rpId localhost (resident, ES256).
    const { privateKey } = generateKeyPairSync('ec', { namedCurve: 'P-256' });
    await cdp.send('WebAuthn.addCredential', {
      authenticatorId,
      credential: {
        credentialId: randomBytes(16).toString('base64'),
        isResidentCredential: true,
        rpId: 'localhost',
        privateKey: (privateKey.export({ format: 'der', type: 'pkcs8' }) as Buffer).toString('base64'),
        userHandle: Buffer.from('jl-1a2b3c4d').toString('base64'),
        signCount: 0,
      },
    });
  }
  return cdp;
}

/** Fresh app with an already-open gate (the common starting point). */
export async function openApp(page: Page, mock = new Mock()) {
  mock.paired = true;
  mock.passkey = true;
  mock.gate = 'open';
  await mock.install(page);
  await virtualAuthenticator(page, true);
  await page.addInitScript(() => {
    try {
      localStorage.setItem('link.v1', JSON.stringify({ pk: true }));
    } catch {
      /* ignore */
    }
  });
  await page.goto('/');
  await page.locator('[data-page="core"]:not([data-hidden])').waitFor();
  await settled(page);
  return mock;
}

export async function settled(page: Page) {
  await page.waitForFunction(() => {
    const l = (window as unknown as { __link?: { morph: { moving: boolean } } }).__link;
    return !!l && !l.morph.moving;
  }, null, { polling: 50 });
}
