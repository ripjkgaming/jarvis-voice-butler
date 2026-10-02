<a href="https://livekit.io/">
  <img src="./.github/assets/livekit-mark.png" alt="LiveKit logo" width="100" height="100">
</a>

# LiveKit Agents Starter - Python

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

A complete starter project for building voice AI apps with [LiveKit Agents for Python](https://github.com/livekit/agents) and [LiveKit Cloud](https://cloud.livekit.io/).

The starter project includes:

- A simple voice AI assistant, ready for extension and customization
- A voice AI pipeline built on [LiveKit Inference](https://docs.livekit.io/agents/models/inference), providing zero-configuration access to [models](https://docs.livekit.io/agents/models) from top labs
  - Uses the fast, open-weight Gemma 4 31B model, [hosted by LiveKit](https://docs.livekit.io/agents/models/llm/livekit/) and tuned for optimal performance in voice AI, as the default LLM
  - Uses Fish Audio S2.1 Pro for TTS, which renders the inline delivery markup that expressive mode relies on
  - Supports more than 50 models from OpenAI, Cartesia, Deepgram, and other providers
  - Access to a wide range of other models, including [Realtime models](https://docs.livekit.io/agents/models/realtime), through extensive plugin ecosystem
- Expressive mode, enabled by default: the framework injects the TTS provider's markup guide into the LLM prompt, so the model emits inline delivery tags (emotion, pacing, non-verbal sounds) that the TTS renders and the transcript never shows
- Eval suite based on the LiveKit Agents [testing & evaluation framework](https://docs.livekit.io/agents/start/testing/)
- [LiveKit Turn Detector](https://docs.livekit.io/agents/logic/turns/turn-detector/), an end-of-turn model that listens to the user's audio directly, combining semantic understanding with acoustic cues for state-of-the-art accuracy across 14 languages
- [Background voice cancellation](https://docs.livekit.io/transport/media/noise-cancellation/)
- Deep session insights from LiveKit [Agent Observability](https://docs.livekit.io/deploy/observability/)
- A Dockerfile ready for [production deployment to LiveKit Cloud](https://docs.livekit.io/deploy/agents/)

This starter app is compatible with any [custom web/mobile frontend](https://docs.livekit.io/frontends/) or [telephony](https://docs.livekit.io/telephony/).

## Using coding agents

This project is designed to work with coding agents like [Claude Code](https://claude.com/product/claude-code), [Cursor](https://www.cursor.com/), and [Codex](https://openai.com/codex/).

For your convenience, LiveKit offers both a CLI and an [MCP server](https://docs.livekit.io/reference/developer-tools/docs-mcp/) that can be used to browse and search its documentation. The [LiveKit CLI](https://docs.livekit.io/intro/basics/cli/) (`lk docs`) works with any coding agent that can run shell commands. Install it for your platform:

**macOS:**

```console
brew install livekit-cli
```

**Linux:**

```console
curl -sSL https://get.livekit.io/cli | bash
```

**Windows:**

```console
winget install LiveKit.LiveKitCLI
```

The `lk docs` subcommand requires version 2.15.0 or higher. Check your version with `lk --version` and update if needed. Once installed, your coding agent can search and browse LiveKit documentation directly from the terminal:

```console
lk docs search "voice agents"
lk docs get-page /agents/start/voice-ai-quickstart
```

See the [Using coding agents](https://docs.livekit.io/intro/coding-agents/) guide for more details, including MCP server setup.

The project includes a complete [AGENTS.md](AGENTS.md) file for these assistants. You can modify this file to suit your needs. To learn more about this file, see [https://agents.md](https://agents.md).

## Dev Setup

Create a project from this template with the LiveKit CLI (recommended):

```bash
lk cloud auth
lk agent init my-agent --template agent-starter-python
```

The CLI clones the template and configures your environment. Then follow the rest of this guide from [Run the agent](#run-the-agent).

<details>
<summary>Alternative: Manual setup without the CLI</summary>

Clone the repository and install dependencies to a virtual environment:

```console
cd agent-starter-python
uv sync
```

Sign up for [LiveKit Cloud](https://cloud.livekit.io/) then set up the environment by copying `.env.example` to `.env.local` and filling in the required keys:

- `LIVEKIT_URL`
- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`

You can load the LiveKit environment automatically using the [LiveKit CLI](https://docs.livekit.io/intro/basics/cli/):

```bash
lk cloud auth
lk app env --write --destination .env.local
```

</details>

## Run the agent

Run this command to speak to your agent directly in your terminal:

```console
uv run python src/agent.py console
```

To run the agent for use with a frontend or telephony, use the `dev` command:

```console
uv run python src/agent.py dev
```

In production, use the `start` command:

```console
uv run python src/agent.py start
```

## Frontend & Telephony

Get started quickly with our pre-built frontend starter apps, or add telephony support:

| Platform | Link | Description |
|----------|----------|-------------|
| **Web** | [`livekit-examples/agent-starter-react`](https://github.com/livekit-examples/agent-starter-react) | Web voice AI assistant with React & Next.js |
| **iOS/macOS** | [`livekit-examples/agent-starter-swift`](https://github.com/livekit-examples/agent-starter-swift) | Native iOS, macOS, and visionOS voice AI assistant |
| **Flutter** | [`livekit-examples/agent-starter-flutter`](https://github.com/livekit-examples/agent-starter-flutter) | Cross-platform voice AI assistant app |
| **React Native** | [`livekit-examples/voice-assistant-react-native`](https://github.com/livekit-examples/voice-assistant-react-native) | Native mobile app with React Native & Expo |
| **Android** | [`livekit-examples/agent-starter-android`](https://github.com/livekit-examples/agent-starter-android) | Native Android app with Kotlin & Jetpack Compose |
| **Web Embed** | [`livekit-examples/agent-starter-embed`](https://github.com/livekit-examples/agent-starter-embed) | Voice AI widget for any website |
| **Telephony** | [Documentation](https://docs.livekit.io/telephony/) | Add inbound or outbound calling to your agent |

For advanced customization, see the [complete frontend guide](https://docs.livekit.io/frontends/).

## Tests and evals

### Visual computer use

Jarvis's system specialist can delegate bounded desktop navigation to a headless,
authenticated Codex worker using `gpt-6-luna` by default, with status/cancel
tools and no silent model fallback. See [computer-use setup and limits](docs/computer-use.md)
for configuration, supported surfaces, safeguards and isolated test commands.
Live model accuracy and latency have not been benchmarked.

### Agent tests

This project includes a complete suite of evals, based on the LiveKit Agents [testing & evaluation framework](https://docs.livekit.io/agents/start/testing/). To run them, use `pytest`.

```console
uv run pytest
```

## Using this template repo for your own project

Once you've started your own project based on this repo, you should:

1. **Check in your `uv.lock`**: This file is currently untracked for the template, but you should commit it to your repository for reproducible builds and proper configuration management. (The same applies to `livekit.toml`, if you run your agents in LiveKit Cloud)

2. **Remove the git tracking test**: Delete the "Check files not tracked in git" step from `.github/workflows/tests.yml` since you'll now want this file to be tracked. These are just there for development purposes in the template repo itself.

3. **Add your own repository secrets**: You must [add secrets](https://docs.github.com/en/actions/how-tos/writing-workflows/choosing-what-your-workflow-does/using-secrets-in-github-actions) for `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` so that the tests can run in CI.

## Deploying to production

This project is production-ready and includes a working `Dockerfile`. To deploy it to LiveKit Cloud or another environment, see the [deploying to production](https://docs.livekit.io/deploy/agents/) guide.

## Self-hosted LiveKit

You can also self-host LiveKit instead of using LiveKit Cloud. See the [self-hosting](https://docs.livekit.io/transport/self-hosting/local/) guide for more information. If you choose to self-host, you'll need to also use [model plugins](https://docs.livekit.io/agents/models/#plugins) instead of LiveKit Inference and will need to remove the [LiveKit Cloud noise cancellation](https://docs.livekit.io/transport/media/noise-cancellation/) plugin.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
