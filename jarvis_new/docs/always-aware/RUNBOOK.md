# Always Aware: runbook

Always Aware **ships off**. Nothing is captured until you turn it on by voice.
Three switches each stop it on their own, whatever the others say:

| Switch | Effect |
| --- | --- |
| `JARVIS_AWARE=0` in the bridge environment | The sidecar thread never starts and every voice "on" is refused. |
| `touch ~/.jarvis/aware/KILL` | Checked on every tick and every takeover step. Nothing is captured while the file exists. |
| Voice: "always aware off" | Sets `~/.jarvis/aware/state.json` `enabled: false`. |

Paths in this file are relative to `/mnt/data/jarvis-voice-butler/jarvis_new`.

## 1. Enable

1. Restart Jarvis once so the bridge loads the new sidecar and routes, and
   the voice agent loads the `awareness` tool and its fast route. Jarvis
   never restarts himself. Quit and relaunch the Jarvis desktop app
   (`app-Jarvis@autostart`), which starts the bridge, or restart
   `src/bridge.py` and the voice agent the way you normally do.
2. Check the bridge is serving the status route (add your bearer token if
   one is configured):
   ```console
   curl -s -H "Authorization: Bearer $(cat ~/.jarvis/bridge_token)" \
     http://127.0.0.1:${JARVIS_BRIDGE_PORT:-4317}/aware/status
   # {"ok": true, "state": "off", ...}
   ```
   4317 is the default port; use `JARVIS_BRIDGE_PORT` if you changed it.
3. Say **"Jarvis, always aware on"**. The first tick runs about 60 s after
   the bridge starts, then every 2 to 5 minutes. The HUD glyph under the
   voice readout shows:

   | Glyph | State |
   | --- | --- |
   | `·` | off |
   | `○` | armed, with the countdown to the next tick |
   | `◉` | capturing. Lit only during the 10 s burst. |
   | `◌` | skipped, with the reason (idle, sensitive, guest...) |
   | `‖` | paused |
   | `?` (amber) | an offer is waiting for your answer |
   | `◎` (amber) | a takeover is running |

   The same glyph also appears in the standby badge.
4. Optional: start in **shadow mode** ("shadow mode on"). The gate still runs
   and offers are logged, but nothing is spoken. Review the logs (§4) before
   you say "shadow mode off".

## 2. Brave: load the locke-ext tab worker (web takeovers only)

Logging and code/file takeovers work without this step. Web takeovers of
your own tabs need the `jarvis-tabs` worker in locke-ext. Today the Brave
launcher has `--remote-debugging-port=9222` but **no `--load-extension`**,
so the gate refuses web offers (`no_tab_move`).

1. Edit `~/.local/share/applications/brave-browser.desktop`. On **every**
   `Exec=` line, add this right after `/usr/bin/brave-browser-stable`:
   ```
   --load-extension=/mnt/data/jarvis-voice-butler/jarvis_new/desktop/brave/locke-ext,/mnt/data/jarvis-voice-butler/jarvis_new/desktop/brave/locke-theme
   ```
   Keep `--remote-debugging-port=9222` on the main `Exec=` line. If you edit
   by hand, `desktop/brave/install.sh` refuses to write this file because it
   did not create it.
   Alternative: open `brave://extensions`, turn on Developer mode, choose
   Load unpacked and pick `desktop/brave/locke-ext`.
2. Quit Brave completely: menu, then Exit. Check that this prints nothing:
   ```console
   pgrep -fa brave-browser-stable
   ```
   Brave's "continue running background apps" setting can keep it alive.
3. Start Brave again from the launcher or the taskbar.
4. Verify:
   ```console
   curl -s 127.0.0.1:9222/json/version          # answers
   ss -ltn | grep 9222                           # 127.0.0.1:9222 only
   PYTHONPATH=src uv run python -c "import asyncio,aware.brave_live as b;print(asyncio.run(b.probe()))"
   ```
   The probe only lists targets and never touches a tab. The MV3 worker
   sleeps after about 30 s idle. `brave_live` then opens `jarvis-tabs.html`
   in its own minimised background window and closes it afterwards.

## 3. Voice commands

| Say | Does |
| --- | --- |
| "always aware on" / "always aware off" | enable / disable |
| "pause awareness for an hour" / "... until 5" / "resume awareness" | pause (24 h max) |
| "awareness status" | on/paused/shadow, calls and tokens today, last skip reason |
| "what did you see" / "what am I doing" | the last 3 seen ticks, in at most 2 sentences |
| "what was I doing at 3pm" / "half an hour ago" / "this morning" | spans (±15 min), then that day's notes |
| "shadow mode on" / "shadow mode off" | log offers without speaking them |
| "stop offering code fixes / forms / downloads / that" / "you can offer code fixes again" | mute or unmute a kind (or a subject) |
| "yes / go ahead / take over", "no / not now", "never offer that" | answer a pending offer (only while one is pending) |
| "stop" / "cancel that" / "I've got it", "carry on", "leave it there" | takeover control (only while a takeover runs) |
| "undo that" / "undo the last three", "show me", "what did you try" | journal (through the `awareness` tool) |
| "set a spend limit of fifty pounds" / "remove the spend limit" | per-transaction cap, audited (off by default) |
| "what have you paid for this week" / "what did you send today" | reads `~/.jarvis/aware/transactions.jsonl` |

Clear phrasings are answered by a regex fast route without the model. Other
phrasings go through the single `awareness(action, arg)` tool. Both are
owner only, so guest sessions are refused.

## 4. Where things are

| Path (under `~/.jarvis/aware/`, all 0600/0700) | What |
| --- | --- |
| `state.json` | enabled, paused_until, shadow, muted, spend_cap, HUD phase |
| `log/YYYY-MM-DD.jsonl` | one line per tick, skips included (kept 14 days) |
| `spans/YYYY-MM-DD.json` | collapsed sessions for "what was I doing at..." (kept 90 days) |
| `budget.json` | calls and tokens per day (240 calls, 1.0M tokens; over 70% only the active monitor is sent, at 100% ticks stop) |
| `audit.jsonl` | every upload (sha256, bytes, model), spend-cap change and takeover action |
| `transactions.jsonl` | every send, submit and payment, including failed ones |
| `offers.jsonl`, `offers/` | offer history and the pending offer (90 s TTL) |
| `memory_feed.json` | which spans were already written into `Memory/Daily/<date>.md` under `## Seen` |

Read today's log:
```console
jq -c '{t:(.ts|todate),o:.outcome,r:.reason,s:.summary,g:.opportunities}' ~/.jarvis/aware/log/$(date +%F).jsonl | tail
```

Tuning knobs (bridge environment): `JARVIS_AWARE_DAILY_CALLS` (240) and
`JARVIS_AWARE_DAILY_TOKENS` (1000000). For extra deny rules, add
`~/.jarvis/aware/deny.json`:
`{"apps": [...], "titles": [...], "hosts": [...], "paths": [...]}`.

## 5. Kill switch, fastest first

1. `touch ~/.jarvis/aware/KILL`. This takes effect at the next tick and the
   next takeover step, with no restart needed. `rm` the file to allow it again.
2. Say "always aware off".
3. For a permanent off, set `JARVIS_AWARE=0` in the bridge environment and
   restart the bridge.

A running takeover also stops on "stop" / "I've got it". It reverts its own
file and code changes from the journal, unless you edited those files since,
and hands the Brave tab back. A bridge restart in the middle of a run returns
any tab still marked `⟦J⟧` to its window on the next start.

## Handover verification (2026-11-07)

`uv run pytest -q tests/test_aware_*.py`: 591 passed (including throwaway
Chromium and a local fake checkout; no real payment or user tab was driven).
`worker_ok` and `saved_method` are present. The total parser handles blank
paragraph separators. The verifier now uses the existing text-only ChatGPT
CLI first, at low reasoning with no tools, with Gemini fallback for unavailable
or malformed replies. Valid model rejections are final.

The user-input test waits for the result element, rather than matching the
word "Ready" already present in the "Make ready" button. Its simulated user
click skips rAF stability polling in the minimised throwaway window and
still uses trusted browser input.

Live KWin/monitor geometry, sensitive-window blackout, actual offer/listening
turns, minimised Brave timing, and extension loading on the real profile are
still unverified. The launcher and services have not been changed or restarted.
Follow section 2 only with the owner's approval. Always Aware remains off.


## Phase 4 follow-up

See [PHASE4.md](PHASE4.md) for the current implementation, structured browser
plan contract, 686-test verification, live preflight evidence and remaining
KDE checks. The read-only readiness command is:

```console
uv run python scripts/aware_brave_preflight.py
```

Exit 1 with `no_tab_worker` is expected until the owner loads locke-ext.
This command never opens, reloads, moves or closes a tab, enables Always
Aware, or restarts a service. The throwaway movement check is:

```console
uv run python scripts/aware_spike.py worker
```

It tests temporary Chromium and fake KWin, not the owner's Brave desktop.
