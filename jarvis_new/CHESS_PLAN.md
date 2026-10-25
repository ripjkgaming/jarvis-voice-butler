# Jarvis Games — Chess build plan (locked)

Chess is game one of the Games capability: **one chess core, two front doors** (Voice Mode, Classic Mode), its own
local voice engine, no LiveKit and no Gemini. Stockfish chooses and judges moves; a free OpenRouter model only
explains them. Nothing happens unless asked, and **stop means stop**. Install and run: `docs/chess/CHESS.md`.

## Core and engines

- `src/games/base.py` — `GameModule` protocol + registry. `src/games/` imports no `livekit` / `google.genai`
  (enforced by `tests/test_chess_isolation.py`).
- `src/games/chess/state.py` — python-chess state: undo/redo, every ending, clocks (increment, pause), saves.
- `src/games/chess/engine.py` — Stockfish over UCI (`Hash 64`, `Threads 2`, async, off the event loop) with an
  honest weaker built-in `MiniEngine` fallback when the binary is missing.
- `src/games/chess/voice_moves.py` + `commands.py` + `text_norm.py` — Appendix A as a grammar: wrappers, homophones,
  NATO files, SAN, captures, castling, promotion, disambiguation answers; legality decides, ambiguity asks.
- `src/games/chess/game.py` (+ `intents.py`, `analysis.py`) — one command API shared by voice, mouse and keyboard;
  take-over, spectator, review, hints, continuous modes, stop.
- `src/games/chess/commentary.py` / `chat.py` / `templates.py` — OpenRouter (httpx, free `models` fallback array,
  JSON validation, grounding check, 20/min + 50/day quota, cache by FEN+move+preset) and offline templates.
- `src/games/server.py` — loopback WebSocket (snapshots + commands) and static UI; `src/games/app.py`, `cli.py`, `lock.py`,
  `window.py` (single game at a time, focus on second launch).

## Voice engine (`src/games/voice/`, local only)

- capture (sounddevice 16 kHz, PipeWire-shared) → Silero VAD (ONNX) endpointing (350 ms, 8 s max) → faster-whisper
  `base.en` int8 CPU (loaded once, warmed, chess-biased) → parser / OpenRouter chat → Piper `en_GB-alan` (sentence by
  sentence, ≤100 ms chunks, interruptible, cached lines).
- `wake_watch.py` + `src/wake_events.py` + small change in `src/wake_client.py`: "hey Jarvis" pauses chess listening;
  chess never mutes the wake client; resume 1.5 s after `call_ended`, status-poll fallback, 10-minute resume-only rule.
- `src/audio_shared.py` — LiveKit-free helpers extracted from `local_stt.py` / `local_voice.py` (Jarvis unchanged).

## Front ends

- Board UI: `frontend/app/chess/page.tsx` + `components/chess/*` (SVG line-art pieces, holo grid, square codes on every
  square, intent arrow, 1/2 badges, shatter/promotion/mate canvas bursts, take-over border, reduced-motion aware).
- Tauri `chess` window (`open_chess_window` / `close_chess_window`, CSP, capabilities, `jarvis chess`).
- `chess` command + ASCII cinematic (`src/games/chess/intro/`: 7 scenes, outro, knight spinner) and `chess --tui` (Textual).
- `jarvis` side: `launch_chess` tool (spawn `chess --voice`, then end the call), prompt routing, wizard checks.

## Stages

- [x] Stage 1 — Plan and conflict report (this file)
  - conflicts found: see "Deviations" below
- [x] Stage 2 — `src/games/` core and `GameModule`; settings (`~/.jarvis/chess.json`)
- [x] Stage 3 — State and Stockfish (+ built-in fallback)
  - tests: `test_chess_state.py`
- [x] Stage 4 — Command API and games server
  - tests: `test_games_server.py`, `test_chess_commands.py`, `test_chess_game_flow.py`
- [x] Stage 5 — Voice parser, Appendix A fixtures first
  - tests: `test_chess_voice_moves.py` (grammar expansion ≥95 % clean, ≥90 % noised, zero illegal moves; 493 hand-picked
    rows in `voice_corpus.json` under `chess_moves`)
- [x] Stage 6 — Chess Voice Engine (shared mic, VAD, whisper, Piper, barge-in) and `chess --mic-test`
  - tests: `test_chess_voice_engine.py`
- [x] Stage 6b — Wake-event broadcast + pause/resume (§2.3b)
  - tests: `test_wake_events.py`, `test_chess_wake_priority.py`
- [x] Stage 6a — `launch_chess` in Jarvis (launch, then end call)
  - tests: `test_launch_chess.py`
- [x] Stage 7 — On-demand and stop system (§2.4)
  - tests: `test_chess_on_demand.py`
- [x] Stage 8 — Chess window and labelled board (Next static export + Tauri window)
- [x] Stage 9 — Board animations
- [x] Stage 10 — Classic Mode mouse and keyboard
- [x] Stage 11 — `chess` CLI, install script, PATH-conflict check
  - tests: `test_chess_cli.py`
- [x] Stage 12 — ASCII intro, outro and spinner
  - tests: `test_chess_intro.py`
- [x] Stage 13 — OpenRouter commentary and templates
  - tests: `test_chess_commentary.py`
- [x] Stage 14 — Take-over and spectator
- [x] Stage 15 — Review and saves
- [x] Stage 16 — Settings (voice and UI)
- [x] Stage 17 — Wizard checks (`OPENROUTER_API_KEY`, Stockfish)
  - tests: `test_chess_wizard.py`
- [x] Stage 18 — `--tui` (Textual)
  - tests: `test_chess_tui.py`
- [x] Stage 19 — Gates and acceptance (see "Gate results")

## Deviations from the brief (flagged, not silent)

1. **Port 4320, not 4318.** Jarvis's `mic_uplink.py` already listens on TCP 4318 (shell `manager.rs`). Games server =
   `ws://127.0.0.1:4320`, static board = `:4321`. The Tauri CSP allows `ws://127.0.0.1:4320`.
2. **Lightweight chess-only shell = a Brave/Chrome `--app` window** on the locally served static board, not a second
   Tauri binary. When the Jarvis shell is running with the chess window it uses that (`jarvis-shell chess`).
3. **Silero VAD via onnxruntime on the existing `~/.jarvis/models/silero_vad.onnx`**, not the `silero-vad` pip package
   (so it is dropped from the `chess` extra); frames are 512 samples (32 ms), Silero's native size, not 30 ms.
4. **Whisper prompt is a short vocabulary list**, and decoding is one greedy pass (`temperature=0`, `max_new_tokens=48`).
   A prompt spelling out whole moves made `base.en` repeat them back (13 s, 40 repeats); measured 0.5–0.8 s now.
   The model loads with `local_files_only` first, because the HF hub otherwise pings huggingface.co for a cached model.
5. **Intro skirmish** plays `1.e4 e5 2.Nf3 Nc6 3.Bc4 Nf6` and then `4.Nxe5 Nxe5`, because the brief wants a capture
   shatter but its listed line has no capture. All legal, generated with python-chess.
6. **Plain-letter shortcuts need the command bar unfocused** (the bar is always focused by default, and `a`–`h`/`Q`
   are chess letters). With the bar focused, Shift+F/H/A/E/T/L work; Ctrl+Q quits anywhere. Documented in the overlay.
7. **Hand-over of `opencode` delegation:** the configured model `muse-spark-1.3-contributor-free` now returns
   "has been deprecated", so no work could be offloaded; everything was built directly.
8. `sherpa-onnx` is installed in `.venv` but is not in `pyproject.toml`; `uv sync --extra chess` removed it, so it was
   reinstalled (`uv pip install sherpa-onnx==1.13.8`). Any exact `uv sync` will remove it again (pre-existing gap).
9. `pnpm build` re-exported `shell/ui` (tracked) from the current frontend sources, as the project's build always does.

## Gate results

Recorded at the end of the build in the final report (pytest for chess + existing suites, ruff on the new code,
tsc, `next lint`, `cargo check`). The repository had **pre-existing** ruff failures (1293 findings, 191 unformatted files)
and unrelated uncommitted work before this build; those are untouched.

## Manual acceptance (needs a person at the machine)

Voice Mode: a hands-free game incl. capture, castling, disambiguation, promotion, "explain that move", hint, take-over
at move 10 and back at 15; "keep analysing" then "stop" mid-sentence; review by voice; Wi-Fi off; launched from
"Jarvis, let's play chess" (room closes); mid-game "hey Jarvis, what's the time?"; `chess --voice` with Jarvis stopped.
Classic Mode: `chess` intro → board → mouse game; keyboard-only game; `--no-intro`; `--tui`; Ctrl+C at every stage.
Everything automatable is covered by tests; the microphone/room steps above were not performed by the agent.
