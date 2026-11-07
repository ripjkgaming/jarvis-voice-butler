# Jarvis handover (2026-11-07)

Repo: `/mnt/data/jarvis-voice-butler/jarvis_new` (Python, uv; voice runtime in `src/jarvis_rt`; HUD frontend in `frontend/`, Next.js). Read `AGENTS.md` first. Use `uv` for everything. Run `uv run ruff format` only on files you own and `uv run ruff check` elsewhere.

## Ground rules (from the owner)
- The working tree has a lot of uncommitted work from several sessions. Do NOT run `git stash`, `git reset`, `git checkout` or `git clean`. Do not commit unless the owner asks. Never reformat files you did not change.
- Do not restart the bridge, voice agent, Brave or any service without the owner's OK. Put restart steps in a runbook instead.
- UI and visual design is done by the main assistant, not delegated. The HUD is voice-controlled: no mouse-dependent interaction.
- Style: Locke/Jarvis ASCII-glyph look, cyan #5fe3ff / ice / amber, dense, real data only (no fake demo data in production code).
- TDD for backend behaviour. Never claim success without verification.

## What is DONE (all uncommitted)
1. **Voice fixes.** `src/jarvis_rt/guards.py` (`RepeatGuard`, `is_foreign_speech`) is wired into `src/jarvis_rt/live.py`: repeated sentences are muted and non-English transcripts (noise heard as Korean/Chinese etc.) are dropped and their tool calls rejected. Tests: `tests/test_rt_guards.py`. Prompt rules added in `src/prompts.py`.
2. **Insights drawer no longer opens by voice.** `open_insights` / `close_insights` were removed from `src/system/panel_tools.py` and `src/phone_session.py`. It opens only by manual click.
3. **Usage stats.** `src/system/{sysmon,sysdiag,sysdetail,sysmon_tools}.py` plus `background.py`: ten read-only tools (cpu/memory/gpu/temps/disk/network/power/process stats, `full_report`, `debug_system`) with per-process breakdowns. Reports are saved in `~/.jarvis/reports/`. Tests: `tests/test_sysmon*.py`, `tests/test_sysdiag.py`, `tests/test_background.py`.
4. **Guest mode after watch mode** fixed in `src/watch_mode.py` (origin-tracked guest file, stop ends guest immediately, stale latch reconcile). Tests: `tests/test_watch_guest_lifecycle.py`.
5. **Orb progress rings** (multiple concentric task rings, critically damped smooth fill): `frontend/components/stark/reactor-progress.tsx`, `frontend/lib/ring-progress.ts`, `src/jobs.py` mirrors jobs into the activity feed. Preview with `pnpm dev` and `/?ringdemo=6`.
6. **Phone app LOCKE//LINK** built through plan phase P7.1: `phone-web/` (Vite + TS PWA, morph page transitions), `src/phone_device.py`, `src/phone_webauthn.py`, `src/phone_control.py`, bridge hooks, scripts (`scripts/phone_pair.py`, `phone_web_deploy.sh`, `phone_retire.sh`). Docs: `docs/phone-app/{BRIEF,PLAN,RUNBOOK,RETIRE}.md`.
7. **Always Aware** (screen-aware assistant, ships OFF): `src/aware/` modules for state, scheduler, skip rules, capture, vision, log, spans, ledger, memory feed, sidecar, gate, offers, feasibility, takeover (code and file), undo journal, file editing, transactions audit log. Voice control in `src/system/aware_tools.py` and `src/agent.py`; HUD glyph `frontend/components/stark/aware-glyph.tsx`. Docs: `docs/always-aware/{PLAN,COORD,SPIKE,RUNBOOK}.md`. About 749 related tests passed at last run, all on fakes.

## What is NOT done / unfinished
1. **Phone UI layout polish.** An agent was killed mid-work. Known problems: DECK screen has a large empty gap between the POWER tile and the PC MIC tile (about 40% of the screen); CORE has wasted space between the transcript box and the command line. Goal: taller tiles filling the height, a compact live-status block (lock, guest, presence, battery, link latency, call state, real data only), bigger transcript area anchored near the command line, 48dp touch targets, check Pixel 7, 360x640 and 412x915. The interruptible element-morph page transitions (no swipe or slide animations) must keep working. `phone-web/` may be mid-edit: run `pnpm check` in `phone-web/` first.
2. **Android APK was never built.** Plan: thin Capacitor wrapper around `phone-web` with the adaptive icon from `docs/phone-app/icon/`, mic permission, HTTPS only, default origin `https://ripjk.tailea4e7f.ts.net`, signed with a new keystore stored outside the repo in `~/.jarvis/android/`. Output `phone-web/android/dist/LOCKE-LINK.apk`. The root disk is about 88% full, so keep the SDK footprint small and download user-level tooling only (no sudo).
3. **Always Aware Phase 4 (live Brave control) is incomplete.** `src/aware/brave_live.py`, `tabsplit.py`, `payments.py` may be partial. The last edit in progress was fixing `page_totals` (`innerText` has a blank line between paragraphs, so the total sits two lines below its label) plus a unit test. 18 ruff findings are open in these files. Intended design (owner-approved in `docs/always-aware/PLAN.md` section 4 and 9): attach to the running Brave over CDP at 127.0.0.1:9222; only the task tab(s) move to a window on the secondary monitor via the locke-ext background worker (`desktop/brave/locke-ext/jarvis-tabs.js`), user tabs stay on the main monitor and never reload; payments/sends only when merchant, exact amount, currency, items and saved card match the offer, every send/submit/payment goes through `src/aware/transactions.py`. Never automate bank-app, 3-D Secure or one-time-code steps. Test only against throwaway tabs and a local fake checkout, never a real payment.
4. **Always Aware gaps:** web takeovers cannot pass the gate until `brave_live.worker_ok()` and `payments.saved_method(host, label)` exist (P2 requested them). The gate's second opinion uses Gemini, not ChatGPT-first as the plan said. Your Brave has no `--load-extension` flag, so the extension is not loaded live.
5. **Never verified live:** real screen capture timing and monitor geometry, sensitive-window blackout with live window positions, HUD-monitor detection, the listening turn after an offer, minimised Brave window throttling, any real phone or tailnet use.

## Owner actions still needed (not code)
- Enable HTTPS certificates for the tailnet in the Tailscale admin console, then follow `docs/phone-app/RUNBOOK.md` (set `JARVIS_PHONE_ORIGIN`, restart `voice-butler.service`, deploy, pair).
- Restart the Jarvis app once so the bridge and voice agent load the new code (voice fixes, usage stats, Always Aware). Always Aware stays off until the owner says "always aware on". Kill switch: `touch ~/.jarvis/aware/KILL`.
- The phone retirement (removing the old JarvisLink 3.0) only happens after the owner uses the new app for 7 days and says "retire it". `scripts/phone_retire.sh` is a dry run by default.

## Known issues to be aware of
- `src/system/` had 8 files accidentally reformatted by `ruff format` (formatting only): `core.py`, `files_tools.py`, `look_tools.py`, `projects_tools.py`, `window_ctl.py`, `window_tools.py`, `research_pdfs.py`, `monitor_window_move.py`. Other sessions' pending edits there may need a re-read.
- `tests/test_rt_live.py::test_server_interrupt_while_agent_speaks` failed once and later passed; another session's `requires_response_validation` change in `src/jarvis_rt/live.py` and `session.py` may affect it. `tests/test_rt_stale_tools` had 4 failures from other sessions' uncommitted `src/jarvis_rt` edits.
- `frontend` test `power-confirmation.test.cjs` fails (cannot resolve `@/lib/shared-poll`, from another session's uncommitted `school-menus.tsx`).
- The throwaway phone bridge test wrote 3 `phone_*` lines into the real `~/.jarvis/actions.log`.
- Bridge and voice agent processes started before this work still run old code until restarted.

## Suggested order
1. `git status`, run the aware tests (`uv run pytest -q tests/test_aware_*.py`), run `pnpm check` in `phone-web/`.
2. Finish the phone UI layout polish, re-run checks, refresh screenshots in `phone-web/test-results/shots/`.
3. Build the APK (see item 2 above) and document it in `docs/phone-app/APK.md`.
4. Finish Always Aware Phase 4 only after reading `docs/always-aware/PLAN.md` sections 4 and 9; read `docs/always-aware/COORD.md` for the module contracts.
