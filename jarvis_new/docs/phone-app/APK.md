# LOCKE//LINK Android build

Updated 2026-11-07. Build output: `phone-web/android/dist/LOCKE-LINK.apk`;
a second copy is placed at `~/Downloads/LOCKE-LINK.apk`. This is a signed
release build, not a claim that phone pairing or calls have been verified.

## Implementation

The checkout already contained a small native Android WebView wrapper; this
handover preserves it rather than replacing it with the proposed Capacitor
wrapper. It bundles the same `phone-web` application under its real HTTPS
origin, forwards API and voice requests to that origin, requests microphone
permission, denies cleartext traffic and cross-origin navigation, and uses
the supplied adaptive/monochrome icons. Android 9 (API 28) or later is required;
the target and compile SDK are 35. There is no background call service.

Passkeys use AndroidX WebKit's feature-gated Web Authentication support plus
Credential Manager. The release certificate's public Digital Asset Links
statement is generated at `phone-web/public/.well-known/assetlinks.json`.
Deploy this alongside the web app. The association must be available at
`https://ripjk.tailea4e7f.ts.net/.well-known/assetlinks.json` with HTTP 200 and
`Content-Type: application/json`, without redirects. Private tailnet access
may prevent association verification by the credential provider; this needs
an on-device test. Do not weaken the server's WebAuthn origin checks if it
fails. The Chrome-installed PWA remains the browser path.

References: [Android WebView passkeys](https://developer.android.com/identity/sign-in/credential-manager-webview),
[Digital Asset Links prerequisites](https://developer.android.com/identity/credential-manager/prerequisites).

## Rebuild

From `phone-web/`:

```console
pnpm check
bash scripts/build-apk.sh
```

Use the existing user-level SDK at `~/Android/Sdk` and JDK 21 at
`~/.local/share/jdk-21.0.12.1+1` (or set `ANDROID_HOME` and `LINK_JAVA_HOME`).
No sudo or Android Studio is needed. The script builds the web bundle,
regenerates the association statement, signs the release, verifies its APK
signature and records SHA-256 and package/permission metadata next to the APK.
Do not build the APK while Playwright is reading `phone-web/dist`: rebuilding
replaces the assets under the preview server.

Build for another origin or an update:

```console
LINK_ORIGIN=https://host.tailnet.ts.net LINK_VERSION_NAME=1.1.0 LINK_VERSION_CODE=2 bash scripts/build-apk.sh
```

Keep the same signing key and increase the version code for upgrades. An
origin change also needs matching `JARVIS_PHONE_ORIGIN`, deployment and device
association verification. Rebuild for origin changes to keep the CSP and app
association aligned; a runtime origin override alone cannot update them.

## Signing material

The keystore and password file are outside the repository:
`~/.jarvis/android/locke-link.keystore` and `~/.jarvis/android/keystore.pass`.
Back up both securely. Never add them to Git. The build creates them only if
the key does not already exist. The generated assetlinks file contains only
the public certificate fingerprint.

## Install and verify on the phone

1. Complete `RUNBOOK.md`: enable tailnet HTTPS, configure the bridge origin,
   restart only with the owner's approval, and deploy the web app plus the
   association file. This work has not restarted or deployed any service.
2. Copy the APK to the phone and allow installation from that source, or use
   `adb install -r android/dist/LOCKE-LINK.apk` with an explicitly connected
   device. Keep the phone connected to Tailscale.
3. Pair using a fresh code, register a passkey, open the gate, then test lock
   and fingerprint-protected unlock. Verify a real microphone call, caption
   delivery, interruption, reconnection, and permission denial/retry.
4. Check the app with keyboard open, system bars, and large fonts. Browser
   screenshots at 360x640, 393x851 and 412x915 are in
   `phone-web/test-results/shots/`; they use a fake bridge and are layout
   verification only.

Real device/tailnet operation, passkey association, and microphone lifecycle
are still unverified. Do not retire the old app until the owner has used the
new app for seven days and explicitly says to retire it.

## ASCII animation update

The phone renderer now uses precomputed polar geometry for layered encoder
rings, counter-rotating helix trails, a curved sphere lattice, travelling glyph
packets and navigation/call-state pulses. Listening and speaking ease between
states; audio levels drive the signal ripples. Motion phase is integrated,
so speed changes do not jump. Idle renders at 30 fps and live calls at 60 fps
in an OffscreenCanvas worker; the fallback uses the same painter. Reduced
motion renders a static field, including during a call, and hidden surfaces
stop rendering. Existing reversible element morphs remain in place.

Preview recording: `artifacts/phone-app/ascii-animation.webm` (mock bridge,
state transitions and page morphs). The extended power controls have 48px
minimum touch targets, including at 360x640.

The browser service worker now skips reloading on its first claim, preserving
first-run pairing; subsequent worker upgrades still reload once. This path is
excluded in the Android wrapper, which serves its bundled files directly.

For repeatable visual timing checks on this shared desktop, Playwright runs
functional tests with two workers and the 4x-throttle trace afterwards in its
own dependent project. Glyph continuity is measured per 16.7ms of elapsed
time, with a separate absolute jump bound, rather than assuming the host
always schedules at 60Hz. Thresholds are unchanged. Set `LINK_TRACE=1` when
running the performance project to save the raw frame trace under test-results.

## Verification at delivery

TypeScript, 75 unit tests, production build and bundle size budgets pass.
All 34 functional browser tests pass, including the three portrait sizes,
reversible morphs, call states, runtime reduced motion, the canvas fallback,
CSP, passkey simulation and offline PWA installation. The 4x CPU performance
check still fails its 4ms p95 target: the latest full run measured 6.65ms p95
and 12.61ms worst frame, with no forced layout detected in application rAF.
An earlier isolated run measured 3.13ms p95; that does not establish a
consistent pass. No threshold was relaxed. This remains an optimisation gap,
and the APK has not been exercised on a physical phone.

`uv run pytest -q tests/test_aware_*.py`: 591 passed. Relevant Python files
pass Ruff. The APK release build and APK signature verification pass.
