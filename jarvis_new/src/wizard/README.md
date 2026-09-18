# First-run wizard (`src/wizard/`)

Installs + configures the Jarvis desktop app end to end. Runs BEFORE the
Python venvs exist (core is stdlib-only), so it works on a fresh
checkout. State is persisted at `$JARVIS_HOME/wizard-state.json` and
reruns skip green steps.

## Steps (plan order)

| step      | what it does                                                     |
|-----------|------------------------------------------------------------------|
| `sysdeps` | system tools via `dnf` + `flatpak`; `/dev/uinput` udev consent   |
| `livekit` | vendored `livekit-server` + `livekit.yaml` + env rewire (Phase 0)|
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
`scripts/udev-uinput.sh` (consent screen). `livekit-server` is vendored
per-arch to `~/.jarvis/bin` by the `livekit` step, matching the shell's
`$JARVIS_LIVEKIT_BIN` contract.

## Env contract (frozen, from `env_cfg.rs`)

The `livekit` step writes the same keypair to three places so they never
drift: `~/.jarvis/livekit.yaml` (0600), repo `.env.local`,
`frontend/.env.local`. `LIVEKIT_URL=ws://127.0.0.1:7880`, key =
`devkey`. Existing keypairs are preserved on rerun.

## Tests

```bash
uv run pytest tests/test_wizard.py
```