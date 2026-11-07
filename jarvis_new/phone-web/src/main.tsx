/** Boot: service worker, #pair= fragment, mount <App>. */
import { render } from 'preact';
import { App, swReg } from './app';
import { sw } from './store';
import './theme.css';

// The pairing code rides in the fragment (never sent to a server log) and is
// removed from the address bar and history at once.
const m = location.hash.match(/^#pair=([0-9A-Za-z-]{10,16})$/);
const pairCode = m ? m[1] : null;
if (location.hash) history.replaceState(null, '', location.pathname + location.search);

// The Android shell serves the bundle itself (android/); no service worker there.
if ('serviceWorker' in navigator && window.isSecureContext && !navigator.userAgent.includes('LockeLinkApp')) {
  void navigator.serviceWorker
    .register('./sw.js', { updateViaCache: 'none', scope: './' })
    .then((reg) => {
      swReg.reg = reg;
      // Cold launch with a waiting version: apply it now.
      if (reg.waiting && navigator.serviceWorker.controller) {
        reg.waiting.postMessage('SKIP_WAITING');
        return;
      }
      reg.addEventListener('updatefound', () => {
        const nw = reg.installing;
        nw?.addEventListener('statechange', () => {
          if (nw.state === 'installed' && navigator.serviceWorker.controller) sw.value = { updateReady: true };
        });
      });
      setInterval(() => void reg.update().catch(() => {}), 30 * 60 * 1000);
    })
    .catch(() => {});
  let controlled = !!navigator.serviceWorker.controller;
  let reloaded = false;
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    // First install only claims this page; its bundle already matches the worker.
    // Reload only when replacing a controller, preserving first-run pairing.
    if (!controlled) { controlled = true; return; }
    if (reloaded) return;
    reloaded = true;
    location.reload();
  });
}

render(<App pairCode={pairCode} />, document.getElementById('app')!);
