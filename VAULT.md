# Jarvis Vault (Obsidian, infinite retention)

- **Vault location:** `/home/ripjk/.jarvis/vault` (Obsidian app: `md.obsidian.Obsidian`)
- **Open it:** Obsidian → Open folder as vault → `/home/ripjk/.jarvis/vault`
- **Policy:** append-only. Nothing is ever deleted; `Archive/` is for cold storage, never pruning.
- **Live sources:** `background/` (JSONL activity/logs, markdown summaries), plus
  `~/.jarvis/voice-butler/{todos,shortcuts,wa_drafts}.json` mirrored under `Memory/`.
- **Code guarantee:** `manage_todo` / `whatsapp_draft` keep full history (no trimming);
  `clear` archives finished todos to `Memory/Todos Archive.md` before removing them
  from the JSON. See `jarvis_new/tests/test_system.py` and `tests/test_daily.py`
  (`..._for_infinite_retention` tests).
- **Disk:** `/home` is at ~89% (51G free). If free space drops below 10G, add
  storage — do not prune the vault.
