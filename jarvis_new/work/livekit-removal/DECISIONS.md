# LiveKit removal: decisions

- Branch `refactor/remove-livekit` cut from `feat/characters` (dirty tree left untouched; stage only owned files).
- Tool schema snapshot: `scripts/dump_tool_schemas.py` -> `tool_schemas.json` (334 entries over Assistant/Research/System; 238 unique names; ScreenAgent not included, spec says 239 — reconcile in Phase 1).
- `.claude/skills/livekit-agents` and `.agents/skills/livekit-agents` are stale after removal; left in place.
- Tauri livekit-removal patch (cargo test 91/91) saved at deferred/tauri-drop-livekit.patch; apply with 'git apply' in Phase 5, once WS transport replaces livekit-server (applying earlier would break the running app).
