# LiveKit removal: decisions

- Branch `refactor/remove-livekit` cut from `feat/characters` (dirty tree left untouched; stage only owned files).
- Tool schema snapshot: `scripts/dump_tool_schemas.py` -> `tool_schemas.json` (334 entries over Assistant/Research/System; 238 unique names; ScreenAgent not included, spec says 239 — reconcile in Phase 1).
- `.claude/skills/livekit-agents` and `.agents/skills/livekit-agents` are stale after removal; left in place.
- Tauri livekit-removal patch (cargo test 91/91) saved at deferred/tauri-drop-livekit.patch; apply with 'git apply' in Phase 5, once WS transport replaces livekit-server (applying earlier would break the running app).
- **agent.py drift (flag for owner):** before this migration the working-tree `src/agent.py` already lacked the character helpers present in committed HEAD (`_active_character`, `_char_runtime`, `_follow_character`, `_is_mirror`, `_prewarm_voice_bg`, `_start_idle_warm`) and the linked-turn wiring (`VoiceTurnBinding`, `ExternalSeedRouter`); `characters/*.yaml` are also deleted in the tree. Not caused by this migration (backup of the pre-edit file shows it). Linked-turn wiring was restored by the data-topics step; character helpers were NOT restored. Decide whether the character feature is being removed.
- Direct pipeline `local` (LiveKit Cloud inference) deleted; JARVIS_PIPELINE=local maps to direct.
- Worker is in-process (no prewarmed child processes): imports happen once, so first call is not slower.
- AGC: LiveKit's WebRTC AGC replaced by jarvis_rt.audio.AutoGain (software).
- Transcript pacing: TranscriptSynchronizer estimates words/second from received audio; not identical to LiveKit's.
