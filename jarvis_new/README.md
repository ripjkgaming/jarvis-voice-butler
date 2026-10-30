# Jarvis

Jarvis is a voice butler for the desktop, with a phone companion. Its voice
path is the in-repo runtime `src/jarvis_rt`; no LiveKit packages are used.

## Voice runtime

`src/jarvis_rt` runs the agent sessions and tools, the Gemini Live client
(google-genai), the tool executor and the local Silero VAD (onnxruntime). The
`direct` pipeline (`JARVIS_PIPELINE=direct`) adds local faster-whisper ears and
a Piper voice.

The agent worker, `src/agent.py`, hosts a WebSocket voice hub. It binds to
loopback at `127.0.0.1:$JARVIS_VOICE_PORT` (default `7880`). Each call is one
authenticated WebSocket at `/voice?token=...`. The token is an HMAC-signed,
short-lived claim set keyed by `~/.jarvis/voice_secret`. Frame and topic formats
are in [docs/voice-transport.md](docs/voice-transport.md).

```console
uv run python src/agent.py dev       # hub with debug logging
uv run python src/agent.py console   # hub with info logging
uv run python src/agent.py start     # hub with info logging (Docker CMD)
```

`dev` differs from the other two only in its log level.

- `JARVIS_VOICE_PORT` changes the loopback port.
- `JARVIS_VOICE_URL` overrides the URL that clients use, when it is a `ws://` or
  `wss://` address. It does not change the bind address.
- Remote access (phone or another machine) must go through a tunnel to the
  loopback hub. Do not open a public port for the hub.

## Setup

```console
uv sync                                   # add --extra chess or --extra needle if needed
cp .env.example .env.local                # set GOOGLE_API_KEY and the other keys you use
uv run python scripts/prefetch_models.py  # optional: fetch whisper and Piper models
```

Coding assistants should follow [AGENTS.md](AGENTS.md) (uv, ruff, TDD).

## Live screen input

During a desktop voice call, select the application window and say **"share my
screen"** (also **"watch my screen"** or **"see my screen"**). Review the named
window disclosure, then say **"confirm visual watch"** or use the tray's Watch
Mode confirmation control. Jarvis receives selected-window frames in the live
Gemini voice session at up to one frame per second. Ask about the visible
content while talking normally. Say **"stop sharing my screen"** or **"stop
watching"**, or use the tray's Stop Watch Mode control, to end capture.

Sharing lasts up to two hours or 7200 video frames. Background Watch Mode analysis
has its separate sixty-call limit. The native capture helper pins the named
window; minimizing or closing it pauses capture. Without that helper, changing
the foreground window pauses capture. Guest/private phone sessions do not receive
this desktop feed. Images are passed in memory and are not saved locally.
Ordinary **"look at my screen"** remains a single screen-reading request; the
webcam security guard uses its existing commands.

## Jarvis headless ChatGPT

Jarvis's background research, planning, drafts, document extraction and short
summaries use the Codex CLI signed in with ChatGPT. Install the CLI and run
`codex login` as the same user that runs Jarvis. No OpenAI API key is needed
for these workers. The CLI must support `exec --json --ephemeral
--ignore-user-config --ignore-rules`.
Jarvis also finds the desktop-bundled CLI when a background service has a
minimal PATH. Use `JARVIS_CODEX_BIN` to select another executable.

The defaults are `gpt-6.1-sol` at **medium** reasoning for backend and research
work, and `gpt-6-luna` at **low** reasoning for summaries, memory and WhatsApp
extraction. Override them with `JARVIS_CHATGPT_BACKEND_MODEL`,
`JARVIS_CHATGPT_RESEARCH_MODEL`, `JARVIS_CHATGPT_FAST_MODEL`,
`JARVIS_CHATGPT_REASONING` and `JARVIS_CHATGPT_FAST_REASONING`.
Task-specific model overrides such as `JARVIS_NOTIFY_MODEL`, `JARVIS_MEMORY_MODEL`
and `JARVIS_WA_MODEL` now require OpenAI model IDs; replace any saved Claude IDs.
Set `JARVIS_CHATGPT=0` to disable all headless ChatGPT calls.

Workers run in `$JARVIS_HOME/chatgpt-cwd` with personal config, hooks, plugins
and project instructions disabled. Text-only workers have no shell or web
tools; research uses native web search, documents use a read-only shell,
and images attach directly. Calls time out cleanly and return warnings on
login, quota or CLI errors. Restart Jarvis after upgrading the CLI or changing
worker settings.

See the official [non-interactive CLI documentation](https://learn.chatgpt.com/docs/non-interactive-mode)
and [OpenAI model catalog](https://developers.openai.com/api/docs/models).

## Jarvis Mail Link

Mail Link lets an allowlisted owner email commands to a dedicated Jarvis
mailbox and receive replies. The bridge hosts the listener. It stays idle
until explicitly enabled with mailbox credentials and at least one owner;
installing this code does not create an address or send mail.

Once you have a dedicated Gmail account and its App Password, run:

```console
uv run python scripts/mail_link_setup.py
```

The interactive setup asks for the Jarvis address, hidden App Password,
owner addresses, and an optional PIN. It tests IMAP and SMTP logins without
sending a message, then enables Mail Link in `$JARVIS_HOME/keys.env`
(default `~/.jarvis/keys.env`). The file is written atomically with mode
`0600`; other settings are preserved. Restart the Jarvis bridge afterward.
Use an owner address separate from the Jarvis mailbox.

Send a message from an owner address with `status` as the subject. Other
examples are `volume to 30`, `open youtube`, `screenshot`, and
`task: research X and summarise`. Put commands on separate body lines,
up to five commands per message. The subject is used only when the body
has no commands. If you configured a PIN, include `PIN <your PIN>` on its
own body line in every message, including confirmation replies.

The listener polls every 30 seconds. It rejects messages from other
senders, automatic/list mail, messages without aligned authentication
from the configured receiving provider, messages older than two hours,
and repeated Message-IDs. Commands containing `unlock`, `remote`, or
`type` require a one-time `CONFIRM <code>` reply within ten minutes.
The rolling limit is 30 accepted command lines per hour. Background task
completion is emailed to the requesting owner; tasks awaiting local
approval remain pending until approved locally.

Set `JARVIS_MAIL_LINK=0` in the bridge environment or `keys.env` and restart
the bridge to disable it. Environment values override `keys.env`.
The relevant settings are `JARVIS_MAIL_ADDRESS`, `JARVIS_MAIL_PASSWORD`,
`JARVIS_MAIL_OWNERS` (comma-separated), and `JARVIS_MAIL_PIN`. For another
provider, configure `JARVIS_MAIL_IMAP_HOST`/`JARVIS_MAIL_IMAP_PORT`,
`JARVIS_MAIL_SMTP_HOST`/`JARVIS_MAIL_SMTP_PORT`, and
`JARVIS_MAIL_AUTHSERV`. Connections use implicit TLS (defaults: Gmail
IMAP 993 and SMTP 465). The provider must prepend trustworthy
Authentication-Results headers and remove forged copies naming itself;
the receiver ID defaults to `mx.google.com`.

Replay history, rate limits, pending confirmations, and task follow-ups
are stored in `$JARVIS_HOME/mail_link.json`. Unreadable or corrupt state
blocks commands; fix access or restore that file before restarting.
Do not delete replay history just to retry a command. SMTP failures do
not rerun completed commands or reset their rate budget. Authentication
failures back off for ten minutes. Tests run entirely with fake mail and
bridge connections:

```console
uv run pytest tests/test_mail_link.py
```

## YouTube downloads

Say “download Tesla Roadster kickflip”, paste a video URL/ID, or ask to
save the current/recent YouTube video. Jarvis searches five results for spoken
text, ranks titles, and asks “this one?” when the choice is close. Say yes/no
before it starts that choice. Requests run in the background; “cancel the
YouTube download” stops the latest active job and removes partial files.

Video defaults to MP4 with H.264/AAC, capped at 1080p; “720p”, “4k”, and
“best” select another cap. “As mp3”, “audio”, “song”, “music only”, or
“just the sound” requests MP3 at 192 kbps; “as m4a” requests M4A instead.
Audio includes title/artist tags and optional cover art. ffmpeg and ffprobe
are required. Existing filenames are preserved with numbered suffixes.

Video goes to `~/Videos/Jarvis`, audio to `~/Music/Jarvis`. Configure
`JARVIS_YOUTUBE_VIDEO_DIR` / `JARVIS_YOUTUBE_AUDIO_DIR` to override them;
`JARVIS_DOWNLOADS_DIR` remains a legacy video override. Local Brave cookies
are used by default; set `JARVIS_YOUTUBE_COOKIES_BROWSER=off` to disable
cookies or select another yt-dlp-supported browser. No cookie export files
are created. Generic extraction/network failures get one retry and a
one-time standalone yt-dlp update check; package-managed installations
must be updated through their package manager.

A video URL's playlist context is ignored unless “playlist” is explicit.
For a complete playlist, say “download playlist” followed by its YouTube
playlist URL. Completed playlist entries are retained if a later entry is
cancelled or fails. At most two jobs run concurrently.

Mobile voice requests use the same desktop downloader. Files stay on the
desktop; the call receives the title, confirmation, status and completion.
Private phone jobs suppress shared desktop activity and notifications.
The authenticated phone tool API also supports `youtube_download_status`
with a `job_id`, plus download, confirm and cancel tools. Downloads are for
personal use; private, restricted and paywalled content is not bypassed.

## Tests and evals

### Visual computer use

Jarvis's system specialist can delegate bounded desktop navigation to a headless,
authenticated Codex worker using `gpt-6-luna` by default, with status/cancel
tools and no silent model fallback. See [computer-use setup and limits](docs/computer-use.md)
for configuration, supported surfaces, safeguards and isolated test commands.
Live model accuracy and latency have not been benchmarked.

### Agent tests

The test suite covers the runtime, the tools and the voice transport. Run it
with `uv run pytest`. Use `-m "not slow"` to skip the real whisper and Piper
loads. Tests marked `live_llm` call Gemini and are skipped unless
`JARVIS_LIVE_LLM=1` is set.

```console
uv run pytest
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
