# Automatic voice upgrades

Run from `jarvis_new`:

```bash
uv run python scripts/voice_model_updater.py --install-timer
```

This installs a user systemd timer, runs the first check immediately, and checks
every 48 hours thereafter while the user service manager is running. After a
reboot or login, it checks again after five minutes. There is no continuously
running checker process. The existing Jarvis worker needs to load the updated
code once; subsequent selected models are read for each new voice session.

The checker paginates Google's authenticated models API and downloads Google's
official rate-limit page on every check. A candidate must advertise
`bidiGenerateContent`, be a Live/native-audio model, have a strictly newer
major/minor version than the current primary, and have **both RPM and RPD explicitly
listed as Unlimited** in the configured tier's table. Unlimited TPM, a missing
limit, a dash, and prose about other models do not qualify. A newer version is the
upgrade policy; it does not establish subjective quality or lower latency.

Before promotion it requests a short audio response using Enceladus, transcription,
and a tool schema, with a 45-second timeout. This can incur a small provider charge.
Failed probes keep the existing selection. Successful selection writes are atomic,
retain prior fallback models, and apply to new sessions without restarting or
interrupting an active conversation. No packages or model code are downloaded.

## Quota visibility

Google currently points to authenticated AI Studio for project-specific limits;
the model-list API does not include them. If the public documentation does not
publish a matching table for your tier, the checker records `quota_unverified`
and leaves Jarvis alone. It cannot infer unlimited quotas from a successful call
or guarantee an upgrade will become eligible. It does not scrape private AI Studio
sessions. Public limits may also differ from the active limits of a project;
verify your project's limits in AI Studio before relying on public tier limits.
Other limits (tokens, concurrency, spending) may still apply.

Optional `.env.local` settings:

```dotenv
# Defaults to free. Choose the tier that actually applies to this project.
JARVIS_VOICE_QUOTA_TIER=free
# Optional shared path; worker and checker must use the same setting/user.
# JARVIS_VOICE_SELECTION_FILE=/home/you/.jarvis/voice-upgrade/selection.json
```

`GOOGLE_API_KEY` (or `GEMINI_API_KEY`) comes from the environment or `.env.local`.
An explicit `JARVIS_VOICE_MODELS` disables automatic promotion and takes precedence
over the saved selection. Clear it to resume automatic selection.

Inspect or disable:

```bash
uv run python scripts/voice_model_updater.py --dry-run
systemctl --user list-timers jarvis-voice-upgrade.timer
journalctl --user -u jarvis-voice-upgrade.service -n 30
cat ~/.jarvis/voice-upgrade/last-check.json
systemctl --user disable --now jarvis-voice-upgrade.timer
```

`last-check.json` records status, candidates, and the quota source without API keys.
`selection.json` records the selected model and previous chain. To revert to the
built-in chain, stop the timer and remove `selection.json`; new sessions use the
built-in chain again. Use `--dry-run` to discover without audio probes or changes.
