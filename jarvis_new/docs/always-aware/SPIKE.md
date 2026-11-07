# Always Aware: Phase 0 spike results

Script: `scripts/aware_spike.py` (`encode`, `gemini N`, `cdp`, `worker`,
`capture`). Every Gemini call in the spike used **synthetic fixture clips**
(`tests/fixtures/aware/`), never Sir's real screens. PLAN.md §8 lists the
questions. This file holds the answers we have and marks the rest as
unverified.

## Measured

| Question | Result |
| --- | --- |
| Clip size per monitor (6 frames, 0.6 fps, libx264 `-preset veryfast -crf 32`, 1280/960 px long edge) | **9 to 44 KB** per clip. PLAN.md estimated 80 to 250 KB, so uploads are about 5x smaller. |
| Encode CPU | About **0.25 s of ffmpeg CPU per monitor** (`-threads 1`, `nice 19`, `ionice -c3`). Well inside the load guard. |
| Gemini call cost (`gemini-3.8-flash`, JSON schema, `maxOutputTokens 800`) | About **1.1k to 1.5k total tokens per call**, from `usageMetadata.totalTokenCount`. |
| Video tokens | About **396 video tokens per clip, whatever the resolution**. Neither the request-level `mediaResolution` (LOW vs MEDIUM) nor the per-part setting changed the video token count. |
| Daily cost at about 140 calls a day | About 0.2M tokens a day, under the 1.0M/day cap (`JARVIS_AWARE_DAILY_TOKENS`). The 240 calls/day cap is the limit that applies first. |
| Password fixture (a frame showing a password dialog title) | The model returned **`sensitive: true`**. `vision.parse` then keeps only the summary and drops the opportunities (tested). Local deny rules skip or black out this window before upload anyway, so this is a second line of defence. |
| Brave CDP bind | `--remote-debugging-port=9222` from `~/.local/share/applications/brave-browser.desktop`. `ss -ltn` shows it bound to **127.0.0.1:9222 only**. `/json/version` answers. |
| `connect_over_cdp` attach/detach | It lists the pages, opens and closes one scratch background window, then detaches, and Brave stays up. P4 found that by default Playwright sends `Browser.setDownloadBehavior`, which would redirect Sir's downloads, and applies focus emulation to every tab. `brave_live` always passes `no_defaults=True`. |
| Tab move without reload | Proven in a **throwaway headless Chromium** with a test copy of the `jarvis-tabs.js` worker. `chrome.windows.create({tabId, focused:false})` and `chrome.tabs.move` keep `sessionStorage` and typed input, the returned tab is not activated, and the emptied window closes itself (P4, `tests/test_aware_tabsplit*.py`). |
| locke-ext in the live Brave | **Not loaded.** The current Brave launcher has **no `--load-extension` flag**, and the unpacked locke-ext id (`ehkdghkfpaekijgchmmhfgmnoimbfalf`) is not registered in the profile. So the jarvis-tabs worker is absent today, and web takeovers of Sir's existing tabs fail the gate with `no_tab_move` / `executor:no_tab_worker`. Fixing this takes one Brave restart (see RUNBOOK.md). |

## Decisions taken from the numbers

- **Capture path A** (`spectacle -b -n -f` six times, cropped per monitor,
  with the HUD rectangle discarded before anything else touches it) is what
  `src/aware/capture.py` implements. Paths B and C were not pursued: B
  captures only the monitor under the cursor, and C may not run headless.
- Resolution choice makes no difference to cost, so `vision.build_body`
  keeps the request-level rule (MEDIUM for one clip, LOW for two or more).
  This is for legibility only.
- The budget ledger (`src/aware/ledger.py`) enforces 240 calls and 1.0M
  tokens a day. At 70% of either cap only the active monitor is sent; at
  100% ticks stop for the day.

## Unverified (needs Sir's machine or a live session)

- **Real `spectacle -f` timing and CPU/RSS for 6 frames on all 3 outputs**,
  and whether the full-desktop image size matches the kscreen geometry on the
  HDR/scaled output. `capture.scale_for` refuses a mismatch with
  `monitors_changed`, which fails closed, but it has not been run against
  the live outputs. Run `uv run python scripts/aware_spike.py capture`
  locally. It uploads nothing and deletes the frames at once.
- **Redaction geometry from the live KWin window info**
  (`capture.live_windows`). It has been tested with fakes only.
- **Text legibility** of 1280 px MEDIUM clips with real code or terminal
  text (fixtures only so far).
- **Minimised-window throttling** of a Jarvis Brave window
  (`Emulation.setFocusEmulationEnabled`, `Page.setWebLifecycleState`), and
  the **KWin move by the `⟦J⟧` caption** in the live session.
- **The locke-ext worker in the real Brave**, which needs the restart above.
- **`hud_screen` in the live session.** The sidecar treats a missing
  `jarvis-shell` window as `hud_unknown` and excludes the primary output.
  This is privacy-safe, but on this machine the HUD may not be on the
  primary output.
- The free-tier data-use caveat (PLAN.md §7) stands. Nothing above changes
  it.
