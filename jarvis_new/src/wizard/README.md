# First-run wizard (`src/wizard/`)

Installs + configures the Jarvis desktop app end to end. Runs BEFORE the
Python venvs exist (core is stdlib-only), so it works on a fresh
checkout. State is persisted at `$JARVIS_HOME/wizard-state.json` and
reruns skip green steps.

## Steps (plan order)

| step      | what it does                                                     |
|-----------|------------------------------------------------------------------|
| `sysdeps` | system tools via `dnf` + `flatpak`; `/dev/uinput` udev consent   |
| `venvs`   | `uv sync` ×2 (main 3.14, wake 3.11) — drops `tflite_runtime`     |
| `models`  | Piper voice (61MB) + openwakeword resources                      |
| `keys`    | `GOOGLE_API_KEY` (required), Brave/You + OSINT (optional)        |
| `audio`   | mic/speaker probe                                                |
| `gate`    | bridge `/health` gate (finale; needs the shell running)          |

## Usage

```bash
scripts/jarvis-wizard status            # JSON step state
scripts/jarvis-wizard check             # checks only, no installs
scripts/jarvis-wizard run               # full flow (skips green steps)
scripts/jarvis-wizard run --step livekit
scripts/jarvis-wizard run --force       # redo everything
scripts/jarvis-wizard deps              # system-deps checklist + cmds
scripts/jarvis-wizard hf-audit          # HF cache inventory
scripts/jarvis-wizard prune-hf          # delete unreferenced HF models
```

Direct: `PYTHONPATH=src .venv/bin/python -m wizard <cmd>`.

## Delivery model

No frozen bundle: ship `uv` + `pyproject.toml` + `uv.lock`; the `venvs`
step installs on first run (~10 min). System deps:
`scripts/system-deps.sh` (`--check` / `--yes`); udev rule:
`scripts/udev-uinput.sh` (consent screen).

No LiveKit server is vendored: the voice transport is the in-repo WebSocket hub (docs/voice-transport.md),
so the wizard no longer vendors a server or writes `livekit.yaml`/LIVEKIT keys.
Stale LiveKit files from older installs are ignored, never deleted.

## Env contract (frozen, from `env_cfg.rs`)

Call tokens are signed with `$JARVIS_HOME/voice_secret` (created on first
use by the token minter); no LIVEKIT_* keys are written.

## Tests

```bash
uv run pytest tests/test_wizard.py
```