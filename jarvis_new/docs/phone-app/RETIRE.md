# Retiring JarvisLink 3.x (prepared, not executed)

This is the reviewable plan for PLAN.md §15. **Nothing has been removed.** The native app
(`phone/`, v3.6.1, versionCode 19), its APKs and its server routes are untouched and keep
working.

The helper `scripts/phone_retire.sh` is a dry run by default. It only reads, and it prints
exactly what each step would archive, remove or leave. Its two acting modes refuse to run
unless `RETIRE_CONFIRM="retire it"` is set.

```console
scripts/phone_retire.sh            # dry run: the full R0–R4 report, changes nothing
```

## R0: gate (all four must be true)

- [ ] Gates P1–P7 are green.
- [ ] The on-phone checklist in `RUNBOOK.md` §7 is signed off by the user.
- [ ] The PWA has been the **only** phone client for **7 consecutive days**, with no
      blocker. The old APK stays installed as a fallback the whole time.
- [ ] The user said **"retire it"**.

Rules that hold throughout:
- No commit without the user's go-ahead in that session, and never a push.
- No `--force`, no history rewriting, no `git clean`.
- Never uninstall the app from the phone; the user does that if they want to.

## R1: archive (copy only)

```console
RETIRE_CONFIRM="retire it" scripts/phone_retire.sh --archive
```

- Copies every `~/JarvisLink-v*.apk` (v1.0 to v2.0, 13 files today) and the last built 3.x
  APK (`phone/app/build/outputs/apk/debug/app-debug.apk`, saved as `JarvisLink-3.6.1.apk`)
  to `~/.jarvis/archive/jarvislink/`. The originals in `~` stay.
- Writes `SHA256SUMS` and a `README.txt` with the tag, the restore command and the checksums.
- Creates the **local** tag `jarvislink-3.6.1-final` at HEAD. It is never pushed, and the
  step refuses if the tag already exists.

The dry run prints every source path, its archive name and its sha256 before you run this.

## R2: remove the native source (one commit, on a branch)

```console
git switch -c retire/jarvislink        # never on main/master
RETIRE_CONFIRM="retire it" scripts/phone_retire.sh --stage-r2
```

Before it does anything, this step checks that the R1 tag exists and the archive checksums
verify. Then it runs `git rm -r phone/` (153 tracked files). The deletion is **staged, not
committed**.

In the same commit, update the references the dry run lists:
- `README.md`: change the phone section to point only at the PWA.
- `REMOTE_ACCESS_PLAN.md` and `src/remote/PROTOCOL.md`: add a one-line note that the
  JarvisLink `RemoteActivity` references are historical and the phone client is now the PWA.
- Any `AGENTS.md` or `CLAUDE.md` line about `phone/` or gradle.

Untracked build output is **not deleted by any script**. Remove it by hand if you want:
`phone/app/build` (233 MB), `phone/.gradle` (5.3 MB), `phone/build`, `phone/local.properties`.

## R3: server code used only by the old app (one commit per item, after a caller audit)

The dry run greps `src frontend shell desktop scripts tests`, excluding `phone/`, and prints
the hits. An item is removed only when its sole remaining callers are its own tests.

| Item | Decision | Audit today (2026-11-07) |
|---|---|---|
| `POST /camera/frame`, `GET /camera/latest` | remove after audit | 2 hits; check them |
| `POST /type` | remove after audit | 5 hits |
| `POST /talk` | **keep** if the wake listener or shell uses it | 6 hits |
| `src/mic_uplink.py` + `shell/src-tauri/src/manager.rs` supervision + `tests/test_mic_uplink_framing.py` | remove after audit; `cargo build` must stay green; drop `JARVIS_MIC_PORT`/`JARVIS_MIC_BIND` from docs | 18 hits incl. manager.rs |
| `/summon` | keep | — |
| `GET/POST /phone/v1/calls` | keep, dormant (`phone_calls.py` is imported by agent, proactive engine, scheduled tasks, mint_token) | — |
| `{"guest": true}` body stamp | keep, mark deprecated in a comment | — |
| `/token /route /chat /tool /mic /room /sys /captions /phone/telemetry /phone/v1/command /session /capabilities`, `phone_api.py`, `phone_session.py`, `mint_token.py` | keep (used by the HUD or the PWA) | — |
| `tests/test_phone.py` and tests that only cover removed items | remove with their item, then run the full suite | — |

R3 edits code, so it is deliberately not scripted. Do it as reviewed commits.

## R4: network tightening (config, with the user's OK)

The bridge already binds `127.0.0.1`. The old APK reaches it only through the
`tailscale serve` TCP forward `tcp://ripjk.tailea4e7f.ts.net:4317 → 127.0.0.1:4317`. After
R2:
1. Ask the user whether anything else on the tailnet uses port 4317.
2. Remove that forward only. Check the syntax against `tailscale serve --help` first; it is
   commonly `tailscale serve --tcp=4317 off`. Never use `tailscale serve reset`, which would
   also remove the PWA mounts.
3. Confirm the PWA still works over HTTPS, and that
   `nc -vz ripjk.tailea4e7f.ts.net 4317` now fails.

If someone set `JARVIS_BRIDGE_BIND` to the tailnet IP in the meantime, set it back to
`127.0.0.1` and restart `voice-butler.service` with the user's OK.

## R5: report

List the commits, the archive path and `SHA256SUMS`, the untracked directories left for
manual deletion, and every R3 item kept, with the reason.

**Rollback:** `git checkout jarvislink-3.6.1-final -- phone`, then reinstall an archived APK
(`adb install -r ~/.jarvis/archive/jarvislink/JarvisLink-3.6.1.apk`).
