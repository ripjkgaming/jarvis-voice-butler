# Skill: Focus Mode

Mute + park distracting windows + dim brightness for a work block, then
RESTORE everything on exit. The restore half is mandatory, not optional.

## Trigger phrases

Enter:

- "focus mode"
- "lock in"
- "heads down time"
- "deep work mode"
- "do not disturb me"
- "time to focus"

Exit / restore (must all restore):

- "restore focus" / "end focus mode" / "unfocus"
- "i'm back" / "break's over"

## Step sequence (real tool names)

Enter:

1. `set_volume` (SystemTools, `action="status"`) — record current level.
   Then `set_volume` (`action="mute"`).
2. `set_brightness` (SystemTools, `action="status"`) — record current
   level. Then `set_brightness` (`action="set"`, `level=30`).
3. `window_action` (SystemTools, `action="list"`) — announce candidates
   (e.g. youtube, discord, games). `window_action` (`action="minimize"`,
   `query="<title fragment>"`) per distracting window the user names.
   Never minimize anything the user did not name.
4. Say: "Focus mode on, Sir. Say `restore focus` when you surface and I
   will put everything back."

Exit (on ANY of the restore phrases — this phrasing is the contract):

1. `set_volume` (`action="unmute"`, or `action="set"` with the recorded
   level if one was recorded).
2. `set_brightness` (`action="set"` with the recorded level).
3. `window_action` (`action="focus"`, `query="<same fragment>"`) for each
   window minimized on entry.
4. Say: "Welcome back, Sir — volume, brightness, and windows restored."

## Abort / confirm rules

- `close` is NEVER used in this skill. If the user says "close X" mid
  session, treat it as a separate explicit command: repeat the title back
  and proceed only on a yes. Minimizing needs no confirm once the user
  named the window; closing always does.
- If `wmctrl` is missing (`window_action` list fails): keep mute + dim,
  say windows are out of reach, still offer restore.
- If entry state (volume/brightness) cannot be read, restore to sane
  defaults (unmute, brightness 70) and say so.
- Never leave the machine muted+dimmed: if the session ends without an
  explicit restore phrase, restore on the next user turn before answering.

## Success criteria

- After entry: muted, dimmed to ~30, named windows parked.
- After "restore focus": volume, brightness, and every parked window back
  where they were. Verify by re-reading status, not by assuming.
- No window ever closed by this skill. No state left unrestored.
