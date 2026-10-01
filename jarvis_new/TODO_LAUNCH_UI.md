# TODO — launch fix, universal launcher, computer-use test, UI revamps

Started 2026-09-27. Status: [ ] todo · [~] in progress · [x] done

## 1. KCalc bug (every "open X" launched KCalc)
- [x] Root cause: LiveKit commits a handoff tool's call+output to the OLD
      agent's chat_ctx. SystemAgent was built with no context, so it never
      saw "open spotify"; the only concrete app in its instructions was the
      Calculator worked example -> open_app("calculator") -> kcalc.
- [x] Failing regression test: tests/test_handoff_task.py
- [x] Fix: carry the task (and research topic) into the specialist's on_enter
- [x] De-bias SYSTEM_INSTRUCTIONS worked example (generic, not Calculator)

## 2. Universal launcher (any app or website, low latency)
Order of resolution for "open <thing>":
- [x] a. Installed apps: .desktop Name/GenericName/Keywords/Exec, PATH,
         flatpak exports — fuzzy scored index, cached
- [x] b. Brave browser history (copy of the locked SQLite, read-only):
         sites whose title/host match, ranked by visit count + recency
- [x] c. Closest-matching installed app (lower-confidence fuzzy)
- [x] d. Else Google search -> open the first organic result
- [x] Arbiter: local Ollama `jarvis-router` (Qwen 1.9B Q4) picks ONE
      candidate index or "search" — only when a/b/c are ambiguous; exact
      hits skip the LLM entirely. Hard timeout, heuristic fallback.
- [x] Wire into SystemTools.open_app and bridge /route (phone): a generic
      "open/launch/start X" regex now reaches the launcher before Needle
- [ ] Needle router still has the 8-app Literal ("open dolphin" -> spotify
      @1.0); only reached by paraphrases like "bring up X" now — low priority
- [x] Tests: resolver unit tests (fake index/history/LLM), latency budget

## 3. Computer-use skills test (sandboxed)
- [x] Nested headless session (Xvfb/Weston or container) so tests never
      touch the real desktop
- [x] Drive open_app / desktop_screenshot / locate / click / type chain
      against real apps inside the sandbox; report pass/fail per skill

- [x] Result: 7/7 skills pass (scripts/sandbox_computer_use.py). OCR fixed
      (2x Lanczos + psm 11) — it read nothing off app UIs before.

## 4. Phone UI revamp (JarvisLink, jarvis_new/phone)
- [x] Target: Flutter jarvis_mobile (owner choice)
- [x] Built by opencode (muse-spark-1.3 free), reviewed + verified by Claude
- [x] Implemented; flutter analyze clean (4 old infos), 11/11 tests
- [ ] Build + install APK and eyeball on device

## 5. Laptop HUD revamp — movie-accurate (jarvis_new/frontend + shell)
- [x] Style: Iron Man 1-2 (owner choice)
- [x] Built by opencode (muse-spark-1.3 free), reviewed + verified by Claude
- [x] Implemented + shell/ui rebuilt; tsc + build pass; footer overlap fixed
