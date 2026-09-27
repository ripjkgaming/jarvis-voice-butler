# Jarvis - Voice AI Butler

A voice-controlled AI butler built on the **LiveKit Agents** framework. Speak to Jarvis, and it responds with a sarcastic British-butler persona while having full control over a real web browser — browsing, searching, clicking, typing, and completing tasks on your behalf.

## Features

- **Voice Conversations** — Real-time voice I/O powered by LiveKit with adaptive interruptions and preemptive generation
- **Gemini 3.1 Flash Live** — Uses Google's realtime model with a British English voice ("Enceladus")
- **Browser Control** — Full Playwright-powered Chromium automation with 11 tools:
  - `open_url` / `search_the_web` / `read_page` / `inspect_page`
  - `go_back` / `take_screenshot`
  - `click` / `type_text` / `scroll` / `press_key`
  - `confirm_browser_action` (safety gate for consequential actions)
- **Video Input** — Camera support for the agent to "see" you
- **Butler Persona** — Sarcastic British-butler personality with hardcoded easter eggs
- **Multi-Client Support** — React web frontend, Flutter mobile app, and console mode
- **Animated HUD Background** — Custom circuit-board/particle SVG animation in the web UI

## Project Structure

```
jarvis_updated_test/
├── jarvis_new/                 # Main project
│   ├── src/
│   │   ├── agent.py            # Entrypoint — AgentServer, Assistant class
│   │   ├── browser.py          # BrowserManager (Playwright Chromium)
│   │   ├── tools.py            # 11 browser function tools
│   │   ├── prompts.py          # System prompts and instructions
│   │   └── __init__.py
│   ├── tests/                  # Agent evals, browser tests, prompt tests
│   ├── frontend/               # Next.js/React web UI
│   │   ├── app/                # Next.js app routes
│   │   ├── components/         # Agents UI, audio visualizers, HUD background
│   │   ├── hooks/              # LiveKit client hooks
│   │   └── package.json
│   ├── pyproject.toml          # Python deps (managed with uv)
│   ├── uv.lock                 # Lockfile
│   ├── Dockerfile              # Multi-stage production build
│   ├── .env.example            # Environment variable template
│   ├── .env.local              # Local credentials (gitignored)
│   ├── AGENTS.md               # Coding agent guide
│   └── plan.md                 # Architecture planning doc
│
└── agent-starter-flutter/      # Flutter mobile client
    ├── lib/                    # Dart source
    ├── pubspec.yaml            # Flutter deps
    └── web/                    # Web build output
```

## Prerequisites

- **Python** >= 3.10
- **[uv](https://docs.astral.sh/uv/)** package manager
- **[pnpm](https://pnpm.io/)** (for the React frontend)
- **Flutter SDK** >= 3.5.1 (for the mobile app)
- **Node.js** >= 20

## Installation

### 1. Python Agent

```bash
cd jarvis_new
uv sync
uv run playwright install chromium
```

### 2. React Web Frontend

```bash
cd jarvis_new/frontend
pnpm install
```

### 3. Flutter Mobile App (Optional)

```bash
cd agent-starter-flutter
flutter pub get
```

## Configuration

Copy the example environment file and fill in your credentials:

```bash
cd jarvis_new
cp .env.example .env.local
```

Required environment variables:

| Variable | Description |
|---|---|
| `LIVEKIT_URL` | Your LiveKit Cloud WebSocket URL (e.g. `wss://your-project.livekit.cloud`) |
| `LIVEKIT_API_KEY` | LiveKit API key |
| `LIVEKIT_API_SECRET` | LiveKit API secret |
| `GOOGLE_API_KEY` | Google AI API key (for Gemini) |

The React frontend also expects `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, and `LIVEKIT_URL` in its own `.env.local`.

## Running

### Agent (Python)

```bash
cd jarvis_new

# Development mode (connects to LiveKit dev server)
uv run src/agent.py dev

# Console mode (terminal-based voice chat)
uv run src/agent.py console

#### Important run your agent before running the frontend!

### Web Frontend

```bash
cd jarvis_new/frontend
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

### Flutter App

```bash
cd agent-starter-flutter
flutter run
```

## Architecture

Jarvis uses **LiveKit's realtime agent framework** with function tools:

1. **Voice Pipeline**: User speaks → LiveKit streams audio → AI Coustics denoises → Gemini Realtime processes
2. **Tool Calling**: Gemini invokes browser tools via LiveKit's function tool system
3. **Browser**: Playwright Chromium instance (headless in production, visible locally) managed per-room with serialized async operations

The agent is designed to recognize named sites (YouTube, Google, Amazon) and open them directly rather than searching via DuckDuckGo.

## Tech Stack

| Component | Technology |
|---|---|
| Agent Framework | LiveKit Agents 1.8.2 |
| LLM | Google Gemini 3.8 Live (voice) / Gemini 3.8 Flash (text brains) |
| Browser Automation | Playwright (Chromium) |
| Noise Cancellation | AI Coustics |
| Web Frontend | Next.js 15, React 19, Tailwind CSS v4 |
| Mobile Client | Flutter, Dart 3.5 |
| Package Manager | uv (Python), pnpm (JS) |
| Language | Python 3.14, TypeScript 5, Dart 3.5 |
