# Sleep / wake / away

JARVIS//SLEEP: idle sleep covers the screens with a screensaver, a double clap
wakes it, and while asleep the away worker handles mail. After a wake,
`wake_sequence` speaks the away summary and interviews Sir for new tasks.

## Services

Unit files live in `desktop/systemd/`. They are copied to `~/.config/systemd/user/`.

| Unit | Runs | Notes |
|---|---|---|
| `jarvis-sleep-controller.service` | `/usr/bin/python3 -m src.sleep_controller run` | System python: the uv venv has no dbus. |
| `jarvis-away-worker.service` | `uv run --no-sync python -m src.away_worker run` | Dry run. Acts only while asleep. |

The wake client (`src/wake_client.py`, with the double-clap hook) is a sidecar
of `jarvis-shell` and is not a unit. The shell starts and restarts it.

```
cp desktop/systemd/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now jarvis-sleep-controller jarvis-away-worker
systemctl --user status jarvis-sleep-controller jarvis-away-worker
systemctl --user restart jarvis-sleep-controller     # after editing a unit or code
systemctl --user stop jarvis-sleep-controller        # stops the loop; a live screensaver dies with its cgroup
journalctl --user -u jarvis-sleep-controller -f
```

Manual control, with `/usr/bin/python3` from the repo root:
`python -m src.sleep_controller status|sleep|wake`.

## Away worker: going live

It stays in dry run until `JARVIS_AWAY_LIVE=1` is set. Dry run still reads
mail and drafts replies, but logs them as "DRY RUN" and sends nothing.

1. Edit `~/.config/systemd/user/jarvis-away-worker.service` (or the repo copy, then copy it).
2. Uncomment `Environment=JARVIS_AWAY_LIVE=1`.
3. `systemctl --user daemon-reload && systemctl --user restart jarvis-away-worker`.

## Screensaver

Default: `src/sleep_screensaver.py` (the Haiku build), set by
`DEFAULT_SCREENSAVER_MODULE` in `src/sleep_controller.py`. To swap, set the env in
the controller unit: `Environment=JARVIS_SCREENSAVER_MODULE=src.sleep_screensaver_opus`
(or `..._sonnet`), then restart the controller.

Wake clip (about 2.1 s): the reactor collapses, flashes, and a shockwave ring
leaves it. From 0.8 s the glyph layer dissolves cell by cell while the window
fades to transparent (Tk `-alpha`, which works under XWayland). The controller
restores the windows at the same moment (`WAKE_WINDOWS_DELAY_S`, 0.8 s), so they
come back through the fade. A wake sends SIGUSR1 to the running saver, which
plays the clip in its own window and exits, so the desktop never shows between
two windows. With no live saver, the controller spawns `--wake-anim`.

## Wake test mode (no wake sequence)

While `~/.jarvis/no_wake_sequence` exists, a wake still clears the screen and
restores windows, but skips `wake_sequence` (no speech, no interview, no agent
spawns). The skip is logged as "wake sequence skipped". Remove the file to get the
normal wake back:

`rm ~/.jarvis/no_wake_sequence`

## Clap tuning

Clap detection is in `src/clap_detect.py` (pure numpy, no device access). The
main knobs are `PEAK_DB` (20, how far above the floor a clap must be),
`PAIR_LEVEL_DB` (9, how alike the two claps must be), `PAIR_MIN_S`/`PAIR_MAX_S`
(0.20 to 0.80 s gap) and `HF_HZ`/`MIN_HF_SHARE` (the high-frequency check).
Raise `PEAK_DB` for fewer false wakes, lower it if claps are missed. Then
restart the shell's wake client: `kill` its PID (the child of `jarvis-shell`
running `src/wake_client.py`). The shell respawns it with the new code.

## State and files (`$JARVIS_HOME`, default `~/.jarvis`)

- `sleep_state.json`: `{"state": "awake"|"asleep", "since": epoch}`
- `wake_request`: touched by a clap; the controller acts on it within 0.2 s
- `sleep_windows.json`: window geometry saved at sleep, removed after a clean wake
- `sleep_screensaver.pid`: the running screensaver
- `sleep_state.lock`: cross-process transition lock
- `away_log.jsonl`: every away action and deferred decision
- `away_worker.state.json`: the away worker's dedupe state
- `agents/watch.log`: the tiered-agents watch loop started after a wake

Idle sleep: 20 minutes (`IDLE_SECONDS`, fixed). A wake counts as activity, so
the 20 minutes restart from the wake.

## Email tasks

Tasks derived from email spawn with no confirmation step (approved). The
planner prompt in `src/wake_sequence.py` still marks answers and decisions as
untrusted data. Deferred decisions (money, legal, health and similar mail) are
still asked aloud by design, because the away worker sends nothing for them.
