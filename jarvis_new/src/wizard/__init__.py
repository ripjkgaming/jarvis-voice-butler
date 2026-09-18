"""First-run installer wizard for the Jarvis desktop app.

Drives the Phase 3 install checklist end to end: system deps (dnf +
flatpak), the two Python venvs (via uv), voice + wake models, API keys,
the bundled livekit-server, a mic/speaker probe, and the bridge /health
gate. State is persisted so reruns skip steps that are already green.

Design constraints:
- The wizard must run BEFORE the venvs exist (fresh checkout), so the
  core (state, deps catalog, command builders) is stdlib-only.
- I/O modules defer third-party imports (sounddevice etc.) until their
  step actually runs.
- Everything is fail-soft: a missing tool degrades one step, never the
  whole run.

Step order mirrors the plan's wizard checklist:
  sysdeps -> livekit -> venvs -> models -> keys -> audio -> gate
"""

from __future__ import annotations

WIZARD_VERSION = "1.0.0"
STATE_FILENAME = "wizard-state.json"
