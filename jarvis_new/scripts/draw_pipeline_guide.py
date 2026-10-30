#!/usr/bin/env python3
"""Render the source-audited Jarvis functional pipeline and tool inventory."""

import ast
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/jarvis-pipeline-2026-10-28"
W = 3600
PAD = 76
GAP = 46
FONT = "/usr/share/fonts/google-noto/NotoSans-Regular.ttf"
BOLD = "/usr/share/fonts/google-noto/NotoSans-Bold.ttf"
F = ImageFont.truetype(FONT, 25)
S = ImageFont.truetype(FONT, 20)
H = ImageFont.truetype(BOLD, 30)
T = ImageFont.truetype(BOLD, 48)
COLORS = {
    "input": "#dceeff",
    "model": "#ece1ff",
    "guard": "#ffe2df",
    "tool": "#dff3ee",
    "state": "#e9edf3",
    "output": "#fff0cc",
}
commands, nodes, edges, markdown = [], [], [], []
y = 0
section_id = "00"


def wrap(text, font, width):
    lines = []
    for paragraph in str(text).split("\n"):
        line = ""
        for word in paragraph.split():
            candidate = (line + " " + word).strip()
            if line and font.getlength(candidate) > width:
                lines.append(line)
                line = word
            else:
                line = candidate
        lines.append(line)
    return lines


def text(x, yy, value, font, fill="#183248"):
    commands.append(("text", (x, yy), value, font, fill))


def section(number, title, subtitle):
    global y, section_id
    section_id = str(number).zfill(2)
    y += 35
    commands.append(("rect", (PAD, y, W - PAD, y + 74), "#12364c", None))
    text(PAD + 24, y + 10, f"{section_id}   {title}", T, "#ffffff")
    y += 91
    for line in wrap(subtitle, F, W - 2 * PAD - 24):
        text(PAD + 10, y, line, F)
        y += 36
    y += 20
    markdown.extend([f"## {section_id}. {title}", "", subtitle, ""])


def card(title, body, source, kind="tool"):
    return title, body, source, kind


def row(cards, connected=True, labels=None):
    global y
    width = (W - 2 * PAD - GAP * (len(cards) - 1)) / len(cards)
    prepared = []
    for title, body, source, kind in cards:
        titles = wrap(title, H, width - 40)
        bodies = wrap(body, F, width - 40)
        sources = wrap(source, S, width - 40)
        height = 36 * len(titles) + 35 * len(bodies) + 28 * len(sources) + 64
        prepared.append((title, body, source, kind, titles, bodies, sources, height))
    height = max(v[-1] for v in prepared)
    ids = []
    for i, (title, body, source, kind, titles, bodies, sources, _) in enumerate(
        prepared
    ):
        x = PAD + i * (width + GAP)
        commands.append(
            ("rect", (x, y, x + width, y + height), COLORS[kind], "#90a7b5")
        )
        node_id = (
            f"{section_id}.{len([n for n in nodes if n['section'] == section_id]) + 1}"
        )
        ids.append(node_id)
        yy = y + 18
        for line in titles:
            text(x + 20, yy, line, H)
            yy += 36
        yy += 12
        for line in bodies:
            text(x + 20, yy, line, F)
            yy += 35
        yy = y + height - 28 * len(sources) - 16
        for line in sources:
            text(x + 20, yy, line, S, "#496477")
            yy += 28
        nodes.append(
            {
                "id": node_id,
                "section": section_id,
                "title": title,
                "body": body,
                "source": source,
                "kind": kind,
            }
        )
        markdown.extend(
            [f"### {node_id} — {title}", "", body, "", f"Source: `{source}`", ""]
        )
    if connected:
        for i in range(len(cards) - 1):
            x = PAD + (i + 1) * width + i * GAP
            commands.append(
                (
                    "arrow",
                    (x + 3, y + height / 2, x + GAP - 3, y + height / 2),
                    "#25516b",
                )
            )
            label = labels[i] if labels and i < len(labels) else "then"
            edges.append({"from": ids[i], "to": ids[i + 1], "label": label})
    y += height + 40


def inventory():
    result = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except (SyntaxError, UnicodeError):
            continue
        hits = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                fn = deco.func if isinstance(deco, ast.Call) else deco
                if (isinstance(fn, ast.Name) and fn.id == "function_tool") or (
                    isinstance(fn, ast.Attribute) and fn.attr == "function_tool"
                ):
                    hits.append(node.name)
        if hits:
            result.append((str(path.relative_to(ROOT)), hits))
    return result


def build():
    global y
    y = 65
    text(PAD, y, "JARVIS  /  INPUT TO EVERY OUTPUT", ImageFont.truetype(BOLD, 64))
    y += 100
    for line in [
        "Source-audited functional flowchart • 28 October 2026 • Asia/Singapore",
        "Read each arrowed row left -> right. Independent rows are alternative branches, not consecutive steps.",
        "Section references connect branches without crossing the entire poster. Optional features require their own flags/consent.",
        "Purple = models/decisions • Red = guards • Blue = inputs • Green = tools • Grey = state • Gold = outputs.",
    ]:
        text(PAD, y, line, F)
        y += 39
    markdown.extend(
        [
            "# Jarvis: input to every output",
            "",
            "Functional architecture and declared tool inventory, not a line-by-line Python call graph. Optional/experimental paths are explicitly labelled. No credentials or private conversations included.",
            "",
        ]
    )
    section(
        1,
        "THE COMPLETE SYSTEM AT A GLANCE",
        "Three parallel worlds share the same services: live conversation, asynchronous owner jobs, and automated incoming-message/background processing.",
    )
    row(
        [
            card(
                "INPUT CHANNELS",
                "Microphone / phone / HUD controls / Telegram / Mail Link / WhatsApp / email / timers / sensors / files.\nDetails: Section 02.",
                "wake_client.py; bridge.py",
                "input",
            ),
            card(
                "INGRESS & PERMISSIONS",
                "Authentication, owner/guest/private-call policy, echo filtering, activity and consent. Some inputs become commands; others become evidence.\nDetails: 03-04, 10.",
                "phone_session.py; action_guard.py",
                "guard",
            ),
            card(
                "ROUTE, THINK, EXECUTE",
                "Gemini conversation; existing tool grammar; Perplexity at specific decision gates; backend/research/code workers.\nDetails: 04-09.",
                "agent.py; request_router.py",
                "model",
            ),
            card(
                "OUTPUT FAN-OUT",
                "Voice, captions, UI, notifications, drafts, sent messages, calendar/doc changes, desktop/device effects, archives, status or deliberate silence.\nDetails: 11-12.",
                "notify.py; speak.py; tools",
                "output",
            ),
        ]
    )
    section(
        2,
        "EVERY INPUT FAMILY",
        "These are separate ingress paths. They do not all pass through speech recognition or through Perplexity.",
    )
    row(
        [
            card(
                "DESKTOP MICROPHONE",
                "Wake phrase / push-to-talk -> wake client -> room creation -> microphone uplink -> LiveKit audio.\nContinue at 03.",
                "wake_client.py; mic_uplink.py",
                "input",
            ),
            card(
                "PHONE / JARVISLINK",
                "Authenticated phone session or scheduled invitation -> private room and mobile audio. Phone telemetry/photos are separate data paths.\nContinue at 03 / 10 / 11.",
                "phone_api.py; phone_session.py",
                "input",
            ),
            card(
                "HUD / DESKTOP CONTROLS",
                "Tauri frontend buttons, tray, taskbar, typed commands and /talk audio -> authenticated bridge endpoints.\nDirect controls -> 04 / 05; audio -> 03.",
                "shell; frontend; bridge.py",
                "input",
            ),
            card(
                "TELEGRAM / MAIL LINK",
                "Approved chat text/voice notes or authenticated command email -> text command routing / chat / bounded task controls. Mail Link checks sender alignment and replay.\nContinue at 04.",
                "telegram_bot.py; mail_link.py",
                "input",
            ),
        ],
        False,
    )
    row(
        [
            card(
                "UNREAD EMAIL",
                "Gmail poll + parsed headers/body -> filters, triage, drafts and event suggestions. This is not the command-mail channel.\nContinue at 09.",
                "mail_watch_job.py; system/inbox.py",
                "input",
            ),
            card(
                "WHATSAPP CHAT INPUT",
                "WhatSie/headless adapter reads eligible unread chats; optional photos become descriptions. Per-chat permissions select available personas.\nContinue at 08.",
                "wa_autoreply.py; wa_headless.py",
                "input",
            ),
            card(
                "FILES / VISUAL EVIDENCE",
                "Explicitly supplied paths, selected active window, camera frames, phone photos and consented observations. Evidence is not permission.\nContinue at 05 / 10.",
                "librarian_sources.py; camera_hub.py",
                "input",
            ),
            card(
                "TIME / EVENTS / SENSORS",
                "Reminders, exams, calendar, jobs, system health, presence, input idle, public event feeds and paper-market quotes.\nContinue at 11.",
                "scheduled_tasks.py; proactive/*",
                "input",
            ),
        ],
        False,
    )
    section(
        3,
        "VOICE TRANSPORT AND THE THREE AUDIO PIPELINES",
        "JARVIS_PIPELINE selects one branch. Realtime is the code default. The new request decider is a tool-boundary decision, not a replacement for this audio pipeline.",
    )
    row(
        [
            card(
                "WAKE / PHONE / PTT",
                "Accepted audio session -> authenticated room/token. Owner/guest/private-session identity stays attached to the session.",
                "wake_client.py; mint_token.py",
                "input",
            ),
            card(
                "LIVEKIT TRANSPORT",
                "Microphone audio in; agent and phone session connect; captions/data-channel events travel beside audio.",
                "agent.py; linked_turn.py",
                "tool",
            ),
            card(
                "REALTIME: GEMINI LIVE",
                "Audio -> Gemini native activity/turn detection -> native conversation + function calls -> native speech audio. No separate Whisper/Silero gate on this branch.",
                "agent.py:_voice_model",
                "model",
            ),
            card(
                "SPOKEN / TEXT OUTPUT",
                "Audio -> laptop/phone speakers. Text/transcripts -> captions and HUD. Tool results re-enter the live model; interruptions use the realtime adapter.",
                "gemini_watchdog.py; hud_events.py",
                "output",
            ),
        ]
    )
    row(
        [
            card(
                "DIRECT BRANCH",
                "LiveKit audio + local Silero endpointing. Uses local-server transport; Gemini reasoning still uses a cloud API.",
                "agent.py:_session_for_pipeline",
                "input",
            ),
            card(
                "LOCAL WHISPER STT",
                "Faster-Whisper converts speech to text. Strict commands may run through the fast path before the text model.\nContinue at 04.",
                "local_stt.py; whisper_config.py",
                "tool",
            ),
            card(
                "GEMINI TEXT BRAIN",
                "Default gemini-3.8-flash via direct Google client. Existing text-model fallback chain; tool results feed back into conversation.",
                "agent.py; prompts.py",
                "model",
            ),
            card(
                "PIPER TTS",
                "Sentence streaming -> local ONNX voice -> playback gate -> speakers/captions. Optional configured cloud TTS override exists.",
                "local_voice.py; audio_playback.py",
                "output",
            ),
        ]
    )
    row(
        [
            card(
                "LOCAL / CLOUD-INFERENCE BRANCH",
                "Despite its name, this branch uses LiveKit Cloud Inference for STT and the text model.",
                "agent.py:JARVIS_PIPELINE=local",
                "input",
            ),
            card(
                "ASSEMBLYAI STT",
                "assemblyai/universal-streaming -> transcript; local turn/command handling remains a separate branch from realtime native audio.",
                "agent.py:CHEAP_STT_MODEL",
                "model",
            ),
            card(
                "GEMINI VIA INFERENCE",
                "Gemini text LLM and its tool collection -> model response. Specialist agents use the branch-appropriate shared LLM.",
                "agent.py:_brain_llm",
                "model",
            ),
            card(
                "LOCAL VOICE OR OVERRIDE",
                "Piper by default; configured JARVIS_TTS can choose an inference voice. Output converges with the common playback/HUD path.",
                "agent.py:_session_tts",
                "output",
            ),
        ]
    )
    section(
        4,
        "OWNER TURN -> DIRECT ACTION OR MODEL HANDOFF",
        "Obvious local controls and conversation do not require an extra cloud decision. Perplexity is consulted for uncertain routing and before ask_backend starts a reasoning job.",
    )
    row(
        [
            card(
                "FRESH OWNER TURN",
                "Transcript/text plus allowed context: recent dialogue, memory, telemetry and tool results. Guest/private-call rules restrict available capabilities.",
                "context/injector.py; phone_session.py",
                "input",
            ),
            card(
                "EARLY TRUSTED CONTROLS",
                "Echo/consent checks -> visual-control commands -> supported goodnight ingress -> feature sessions -> protocols. Realtime audio remains owned by Gemini.",
                "agent.py:on_user_turn_completed",
                "guard",
            ),
            card(
                "TEXT FAST PATH",
                "Only on text pipelines: strict grammar -> intent resolver / optional Needle -> read-goal routing -> existing verified tool. Unmatched text falls through to Gemini.",
                "intent/fast_path.py; intent/needle_router.py",
                "tool",
            ),
            card(
                "DIRECT JARVIS",
                "Conversation / quick explanations / arithmetic -> answer. Current quick facts -> search. Volume/media/apps/calendar etc. -> own tools.\nTools: 05; outputs: 12.",
                "agent.py; prompts.py",
                "output",
            ),
        ]
    )
    row(
        [
            card(
                "UNCLEAR / DEEP REQUEST",
                "route_request(request) is advisory only. ask_backend(task) independently checks the route before creating a background job.",
                "system/workspace_tools.py",
                "input",
            ),
            card(
                "PERPLEXITY DECIDER",
                "Larger pplx-decider-v1.2 -> typed destination + explicit provider preference. Below 0.8 / failure -> direct, except an anchored explicit backend request is retained.",
                "request_router.py; mercury_decide.py",
                "model",
            ),
            card(
                "FOUR DESTINATIONS",
                "direct -> Jarvis\nbackend -> ask_backend / Section 06\nresearch -> research_project / 07\ncode -> code_project / 07.\nA label itself executes nothing.",
                "request_router.py; workspace_tools.py",
                "guard",
            ),
            card(
                "RETURN TO CONVERSATION",
                "Direct advice returns without spawning work. Confirmed background start returns an acknowledgement; later completion feeds the result back to the owner.",
                "WorkspaceTools._in_background",
                "output",
            ),
        ]
    )
    section(
        5,
        "ALL TOOL / INTEGRATION DOMAINS",
        "Parallel capability branches off Jarvis's tool dispatch. Calls use each tool's own guards and return evidence to the model. Exact declared endpoint names are listed in Section 14.",
    )
    families = [
        (
            "DESKTOP & WINDOWS",
            "App launch/close, visible URLs, focus/list/arrange windows, keyboard/mouse automation, file operations and power actions.\nOutputs: desktop effects + verified/unverified receipts.",
            "system/core.py; window_tools.py; tools.py",
        ),
        (
            "MEDIA & DOWNLOADS",
            "YouTube resolution/playback in the system browser; media keys; direct video downloads and local output files. Backend browser is separate from visible playback.",
            "youtube_download.py; system/audio_ctl.py",
        ),
        (
            "DEVICES & SMART HOME",
            "Wi-Fi, Bluetooth, audio sinks, monitors, keyboard lighting, USB inspection, KDE Connect and optional Home Assistant.\nOutputs: device state, transfers or control effects.",
            "system/devices.py",
        ),
        (
            "WEB / SHOPPING / REDDIT",
            "Helper Google / search APIs / DuckDuckGo; read-only backend browsing; structured shopping results; Reddit tools. Purchases and posting retain approvals.",
            "tools.py; browser.py; shopping.py; system/reddit.py",
        ),
        (
            "MAIL / WHATSAPP",
            "Read threads, draft and review responses, inspect prior activity, approved mimic lists and exact-recipient send workflows. Automation paths also run independently.",
            "system/inbox.py; system/whatsapp.py; 08-09",
        ),
        (
            "DOCS / SHEETS / DRIVE / NOTION",
            "Read/search/export; new documents and invoices; guarded updates to existing content; Google sharing and returned URLs.\nOutputs: cloud document changes + previews.",
            "system/workspace_tools.py; google_apps.py; docgen.py",
        ),
        (
            "CALENDAR & EXAMS",
            "Read upcoming events; schedule-file/email extraction -> preview -> confirm import; manage stored exam timetable and reminders.",
            "event_extractor.py; exam_tools.py; workspace_tools.py",
        ),
        (
            "FILES & LIBRARIAN",
            "Second-brain index, document retrieval, local/Drive source search, PDFs, exact-location source hits and opening files. Evidence feeds answers or draft work.",
            "system/files_tools.py; librarian*.py; second_brain.py",
        ),
        (
            "MEMORY & ACTIVITY",
            "Explicit remember/forget, recall decisions and prior sessions; activity tracking; pick up where left off.\nOutputs: answer/context or vault/state updates.",
            "memory.py; activity.py; memory_tools.py",
        ),
        (
            "FOCUS / SCHOOL / PROTOCOLS",
            "Focus window/time sessions; school quiet/feature policy; protocol commands, suit/HUD controls and context-specific behaviour.",
            "focus.py; school.py; protocols.py; suit_tools.py",
        ),
        (
            "VISION / SCREEN / WATCH",
            "On-demand visual questions; foreground OCR; shared camera; scoped observation, posture/gestures and monitor summaries. Consent restrictions apply.\nDetails: 10.",
            "look.py; screen_understanding.py; watch_tools.py",
        ),
        (
            "CODE / TESTS / PROJECTS",
            "Dedicated coding proposals; background tests; failure explanation; reviewed PR opening; archive navigation.\nCoding proposal is not patch application.",
            "projects.py; system/code_tools.py; 07",
        ),
        (
            "TASKS / WORKSHOP / SCHEDULE",
            "Multi-step plans, read-only research/code/ops workshop, timers, reminders, scheduled phone calls, cancellation and status.\nDetails: 07 / 11.",
            "agent_tasks.py; workshop.py; scheduled_tasks.py",
        ),
        (
            "PUBLIC INTELLIGENCE / SECURITY",
            "Passive OSINT and bounded network/system information; active pentest tools behind the dedicated specialist/scope gates.",
            "system/osint.py; pentest.py; system/scope.py",
        ),
        (
            "GAMES / CHESS / ROBLOX",
            "Launch game windows, voice chess commands and game speech/UI state; desktop launcher for supported game clients.",
            "games/*; system/devices.py",
        ),
        (
            "HEALTH / HUD / INSIGHTS",
            "Measured latency and incidents, system stats, monitor lifecycle, panels, AI-usage tracker and paper-market dashboards.\nOutputs: visual status and diagnostic evidence.",
            "health_recorder*.py; hud_panels.py; insights_proxy.py",
        ),
    ]
    for i in range(0, len(families), 4):
        row([card(a, b, c) for a, b, c in families[i : i + 4]], False)
    section(
        6,
        "BACKGROUND REASONING: CHATGPT <-> CLAUDE",
        "This pool applies to ask_backend reasoning/drafting only. It does not replace the separate research, coding, WhatsApp or extraction adapters.",
    )
    row(
        [
            card(
                "ASK_BACKEND GUARD",
                "Perplexity must return backend; direct/research/code decisions return guidance and backend_started=false. Original full task and provider preference are preserved.",
                "workspace_tools.py; request_router.py",
                "guard",
            ),
            card(
                "PROVIDER POOL",
                "Auto defaults to ChatGPT first; explicit Claude/ChatGPT preference sets first attempt. Known limit-hit providers rest for 300 seconds. Actual remaining quota is not guessed.",
                "backend_pool.py",
                "model",
            ),
            card(
                "ISOLATED TEXT WORKER",
                "ChatGPT: existing headless Codex backend model.\nClaude: subscription-authenticated CLI, no tools/MCP/skills, no saved session, neutral temporary cwd. Both get only the supplied text task.",
                "chatgpt_cli.py; claude_backend.py",
                "model",
            ),
            card(
                "ANSWER / LIMIT / ERROR",
                "Success -> return answer to Jarvis.\nRecognised limit/unavailability -> other provider within time budget.\nAmbiguous timeout/error -> report failure, not blind replay.",
                "backend_pool.py; backend_model.py",
                "output",
            ),
        ]
    )
    section(
        7,
        "DURABLE PROJECTS, TASKS AND WORKSHOP",
        "Different job types retain different adapters and permissions; Claude limit fallback from Section 06 does not silently widen their capabilities.",
    )
    row(
        [
            card(
                "IN-DEPTH RESEARCH",
                "Explicit research request -> research_project -> authenticated bridge /projects -> durable research metadata/progress.",
                "system/projects_tools.py; projects.py",
                "input",
            ),
            card(
                "CHATGPT RESEARCH WORKER",
                "Existing research model with WebSearch/WebFetch and bounded execution. Reads public sources and writes a sourced report.",
                "projects.py:start_research",
                "model",
            ),
            card(
                "PROJECT ARCHIVE",
                "report.md + sources.md + summary/status/progress. Completion/cancel/error are persisted. Live caller can poll completion.",
                "~/.jarvis/projects/ (state)",
                "state",
            ),
            card(
                "OWNER DELIVERY",
                "Spoken summary when live; archive UI/report file and existing notification policy otherwise. No claim of a report until completion.",
                "projects_tools.py; notify.py",
                "output",
            ),
        ]
    )
    row(
        [
            card(
                "CODING REQUEST",
                "code_project -> private/guest checks -> authenticated bridge -> explicitly activated dedicated Assigner queue.",
                "projects_tools.py; projects.py",
                "guard",
            ),
            card(
                "ASSIGNER + WORKERS",
                "Trusted bounded source inventory and dependency context -> validated coordinator plan -> isolated no-tools generate/review workers. Existing fixed worker model allowlist.",
                "assigner_context.py; assigner_headless.py",
                "model",
            ),
            card(
                "VALIDATED PROPOSAL",
                "Strict patch-hunk validation, hashes, manifests, raw results and proposal.patch in archive. Block if context/settings/proposal are invalid.",
                "assigner_headless.py; projects.py",
                "state",
            ),
            card(
                "REVIEW, NOT AUTO-APPLY",
                "Owner reviews proposal. Applying patches, running tests, merging or deployment remain separate actions. Existing run_tests/open_pr tools have their own workflows.",
                "docs/assigner-routing.md; code_tools.py",
                "output",
            ),
        ]
    )
    row(
        [
            card(
                "MULTI-STEP TASK / WORKSHOP",
                "start_task plans think/research/write/consequential steps. Workshop chooses scoped research/code/ops specialists; these are read-only roles.",
                "agent_tasks.py; workshop.py",
                "input",
            ),
            card(
                "BOUNDED EXECUTION",
                "Task state/revision/lease locks; background model steps and prior-result context. Consequential actions are separated and paused for owner approval.",
                "agent_tasks.py; cancellation_control.py",
                "guard",
            ),
            card(
                "MERGE / PREPARE",
                "Task outputs or workshop reports -> stored status and write-up. Cancellation has settlement receipts; a speech interruption is not an external undo.",
                "workshop.py; cancellation_adapters.py",
                "state",
            ),
            card(
                "READ BACK / HAND BACK",
                "Jarvis reports progress/results. Approved consequential steps return to the normal guarded tools; workshop never modifies the world by itself.",
                "system/task_tools.py; workshop.py",
                "output",
            ),
        ]
    )
    section(
        8,
        "WHATSAPP: INCOMING -> PERSONA -> DRAFT -> SEND",
        "WhatsApp's persona names are reply modes, not a global character switch. Rude mode remains off in the current deployment unless the owner enables it.",
    )
    row(
        [
            card(
                "UNREAD CHAT / PHOTOS",
                "Wait gate -> read recent chat -> optional photo description. Self/blocklist, group mention, owner-active, muted, one-note and fingerprint checks run first.",
                "wa_autoreply.py; wa_vision.py",
                "input",
            ),
            card(
                "PERPLEXITY PERSONA",
                "Allowed choices built by code:\nJarvis always; owner_style only approved mimic chats; rude_jarvis only enabled + not trusted. Latest messages distinguished from history.",
                "wa_reply_router.py",
                "model",
            ),
            card(
                "PERSONA-SPECIFIC WRITER",
                "Jarvis / rude Jarvis: existing ChatGPT fast writer.\nOwner style: optional free style analyst + real-owner examples + existing mimic writer. Writer failure uses existing OpenRouter fallback.",
                "wa_autoreply.py; wa_mimic.py; wa_analyst.py",
                "model",
            ),
            card(
                "VALIDATE & DELIVER",
                "Uncertainty/failure -> polite Jarvis. Bot-identity / sensitive mimic requests -> owner handoff, no reply. Disallowed rude reply suppressed. Live auto-reply -> WhatsApp text + digest; dry -> saved evidence only.",
                "wa_autoreply.py:_send_reply",
                "output",
            ),
        ]
    )
    row(
        [
            card(
                "EXPLICIT OUTGOING MESSAGE",
                "Owner asks to send/reply to a recipient -> canonical draft record. This separate workflow is not permission granted by the persona model.",
                "system/whatsapp.py; wa_approvals.py",
                "input",
            ),
            card(
                "EXACT RECIPIENT REVIEW",
                "Open/read back the selected recipient and message. The private owner phone workflow requires a fresh exact spoken challenge.",
                "wa_approvals.py; system/whatsapp.py",
                "guard",
            ),
            card(
                "SINGLE-USE AUTHORIZATION",
                "Recheck identity, exact draft, recipient and current channel; reject stale/generic approvals and model-invented confirmation.",
                "wa_approvals.py; action_guard.py",
                "guard",
            ),
            card(
                "MESSAGE / RECEIPT / REFUSAL",
                "Confirmed transport send returns its receipt. Refusal or uncertainty is reported honestly. No simulated click is allowed to replace the approval gate.",
                "system/whatsapp.py; wa_headless.py",
                "output",
            ),
        ]
    )
    section(
        9,
        "EMAIL: TRIAGE, DRAFTS AND CALENDAR SUGGESTIONS",
        "Incoming email is untrusted content. Polling with no new qualifying mail makes no Perplexity inference call.",
    )
    row(
        [
            card(
                "GMAIL POLL + FILTERS",
                "Unread IDs -> bounded fetch/parse -> seen/retry state. Skip own/undeliverable noise; distinguish bulk/human; keyword/classroom triage.",
                "mail_watch_job.py; system/inbox.py",
                "input",
            ),
            card(
                "PERPLEXITY URGENCY / SCAM",
                "Two yes/no questions in one request. Urgent >=0.5; scam >=0.8 overrides urgency. Failure/incomplete response -> existing ChatGPT judge -> keyword fallback if unavailable.",
                "urgency_judge.py; mercury_decide.py",
                "model",
            ),
            card(
                "MAIL-SPECIFIC POLICY",
                "High/normal/skip outcome; dedupe and sender/thread acknowledgement limits. Draft engine creates bounded replies; configured auto-ack is a distinct preauthorised path.",
                "draft_engine.py; mail_watch_job.py",
                "guard",
            ),
            card(
                "MAIL OUTPUTS",
                "Draft queue / relaxed or urgent acknowledgement when enabled / urgent notification or owner message / logged normal mail. Explicit reviewed mail sends retain confirmation.",
                "draft_notify.py; inbox.py; notify.py",
                "output",
            ),
        ]
    )
    row(
        [
            card(
                "EMAIL / SCHEDULE FILE",
                "Dated content in mail, a supplied PDF/image/text schedule, or an explicit conversation import.",
                "event_extractor.py; backend_model.py",
                "input",
            ),
            card(
                "EVENT GATE / EXTRACTION",
                "Incoming email gate: p(event)<0.05 skips ChatGPT. Otherwise existing ChatGPT extracts structured dates/times; validators reject bad/past/uncertain entries.",
                "event_extractor.py; backend_model.py",
                "model",
            ),
            card(
                "PENDING SUGGESTION",
                "Future event candidates and previews are saved. Read back event details; owner must confirm the exact import or suggestion.",
                "event_suggest.py; workspace_tools.py",
                "guard",
            ),
            card(
                "CALENDAR OR NO CHANGE",
                "Confirmed candidate -> Google Calendar event + receipt. Decline -> dismissed state. No event / invalid extraction -> no calendar write.",
                "google_api.py; confirm_email_event",
                "output",
            ),
        ]
    )
    section(
        10,
        "VISUAL INPUT, WATCH MODE AND PRIVATE CONTEXT",
        "A transcript, model call or on-screen instruction cannot create visual consent. Screen OCR and image-question paths have different scopes.",
    )
    row(
        [
            card(
                "EXPLICIT SCREEN REQUEST",
                "Fresh trusted owner turn + supported host/session lifecycle. Bind capture permission to the selected foreground window and current generation.",
                "feature_sessions.py; screen_session_holder.py",
                "guard",
            ),
            card(
                "FOREGROUND OCR",
                "Foreground-only capture/OCR with byte/pixel/text/age bounds. No ambient capture; raw screen images are not uploaded by this OCR path.",
                "screen_understanding.py; screen_host.py",
                "tool",
            ),
            card(
                "TEXT EXPLANATION",
                "Tool-less explanation adapter receives bounded untrusted OCR text + the owner's question, not execution instructions.",
                "screen_understanding.py:explanation_input",
                "model",
            ),
            card(
                "PRIVATE ANSWER / CLEANUP",
                "Scoped speech/context handling; fresh provider/session generation where required; close/cancel clears evidence. No retained screenshot unless explicitly authorised.",
                "screen_session_holder.py; feature_sessions.py",
                "output",
            ),
        ]
    )
    row(
        [
            card(
                "CAMERA / VISION REQUEST",
                "On-demand object/window question; shared camera lease or permitted active-window image path. Phone photos are a separate explicit ingest source.",
                "look.py; camera_hub.py; phone_api.py",
                "input",
            ),
            card(
                "VISION ADAPTER",
                "Configured vision client/fallbacks answer the specific question. WhatsApp photo descriptions feed the text conversation. Camera consumers share the hub.",
                "look.py; eyes.py; wa_vision.py",
                "model",
            ),
            card(
                "OPTIONAL WATCHERS",
                "Watch mode, appearance/posture/phone/gesture checks, window metadata and registered activity/build/event monitoring. Each has its own consent/flag/scope.",
                "computer_watch*; focus.py; eyes.py",
                "guard",
            ),
            card(
                "OBSERVATIONS, NOT AUTHORITY",
                "Answer, watch briefing, draft suggestion or notification -> Section 11/12. Observation does not authorize clicking, sending, coding or arbitrary remediation.",
                "watch_control.py; build_monitor.py",
                "output",
            ),
        ]
    )
    section(
        11,
        "BACKGROUND SERVICES, TIMERS AND RELIABILITY",
        "The bridge hosts long-lived sidecar threads so many jobs survive the end of a voice call. Features can be disabled or require explicit setup.",
    )
    row(
        [
            card(
                "SIGNAL SOURCES",
                "Calendar/deadlines/mail/jobs/system telemetry, exam schedule, due reminders/calls, focus drift and allowlisted public event feeds.",
                "proactive/sources/*; scheduled_tasks.py",
                "input",
            ),
            card(
                "TRIGGER / MONITOR ENGINE",
                "Thresholds, time windows, dedupe, job state and public evidence. Optional event triage uses a separate free-router adapter, not the Perplexity persona router.",
                "proactive/engine.py; event_monitor/*",
                "tool",
            ),
            card(
                "DELIVERY POLICY",
                "Quiet hours, daily caps, batching, active/idle/unknown presence, mute/school/call conditions and queue TTL. Scheduled calls require supported phone readiness and valid invitations.",
                "proactive/policy.py; notify.py; phone_calls.py",
                "guard",
            ),
            card(
                "NOTIFY / QUEUE / CALL",
                "Immediate permitted speech + caption/toast, silent desktop notification, queued announcement after activity, scheduled reminder or phone invitation. Queue/invitation is not proof of delivery.",
                "notify.py; speak.py; scheduled_tasks.py",
                "output",
            ),
        ]
    )
    row(
        [
            card(
                "REGISTERED JOB / SERVICE",
                "Bridge supervisors and Tauri sidecars, agent worker, wake/microphone, local LiveKit, task/assigner jobs and supported goodnight inventory.",
                "bridge.py; shell/src-tauri/src/manager.rs",
                "state",
            ),
            card(
                "HEALTH & CANCELLATION",
                "Liveness/readiness; Gemini reconnect/failover; tool latency; opted-in health recorder; explicit job cancellation and settled outcomes. Failures return bounded errors.",
                "gemini_watchdog.py; cancellation_control.py",
                "guard",
            ),
            card(
                "SUPPORTED GOODNIGHT V2",
                "Fresh owner audio ingress -> registered-scope activity / busy checks and fencing -> ten-minute idle policy -> session lock with readback, when eligible. Old watcher is retired.",
                "goodnight_host.py; goodnight_runtime.py",
                "guard",
            ),
            card(
                "STATE / DIAGNOSTIC OUTPUT",
                "Recovered job state, truthful failure/cancel receipt, health/latency report, archive status or verified lock. No global claim over unrelated external coding apps.",
                "action_outcome.py; health_recorder*.py",
                "output",
            ),
        ]
    )
    row(
        [
            card(
                "PAPER-MARKET INPUT",
                "Quote feeds, calendar and persisted paper portfolio / replay data. Dedicated paper-market services run separately from voice.",
                "paper_market/feed.py; service.py",
                "input",
            ),
            card(
                "SIMULATION / OPTIONAL TA",
                "Paper engine/strategy/replay and separately configured TradingAgents analysis. These are not live broker trade execution paths.",
                "paper_market/engine.py; ta_runner.py",
                "tool",
            ),
            card(
                "PAPER STATE / INSIGHTS",
                "Local positions, fills, charts, replay metrics, reports and AI-usage tracker data are exposed through read-only dashboard proxies.",
                "paper_market/*; insights_proxy.py",
                "state",
            ),
            card(
                "DASHBOARD OUTPUT",
                "HUD/Insights paper-market panels and usage view. Optional notifications retain delivery policy. Paper fills are simulations, not real trades.",
                "frontend/markets; bridge.py",
                "output",
            ),
        ]
    )
    section(
        12,
        "EVERY OUTPUT FAMILY AND WHAT COUNTS AS SUCCESS",
        "Several outputs can happen for one request. Evidence and delivery receipts must distinguish prepared, launched, sent, verified, failed and deliberately skipped states.",
    )
    row(
        [
            card(
                "AUDIO / CONVERSATION",
                "Native Gemini audio; Piper/inference TTS; laptop or phone speakers; spoken handoff/completion/error; interruption-aware playback.",
                "agent.py; local_voice.py; speak.py",
                "output",
            ),
            card(
                "TEXT / VISUAL UI",
                "Live captions, HUD state/cards/panels, archive viewer, watch briefing, game windows, desktop toasts, usage/paper dashboards and visible browser pages.",
                "hud_events.py; frontend; shell",
                "output",
            ),
            card(
                "MESSAGES / CALLS",
                "Gmail replies/acknowledgements, WhatsApp auto-replies or confirmed sends, configured Telegram responses, owner handoff alerts and bounded phone invitations.",
                "inbox.py; whatsapp.py; telegram_bot.py",
                "output",
            ),
            card(
                "FILES / CLOUD OBJECTS",
                "Drafts, reports, source lists, code proposals, downloaded video, generated documents/invoices, Drive/Docs/Sheets/Notion changes and confirmed calendar entries.",
                "projects.py; docgen.py; google_api.py",
                "output",
            ),
        ],
        False,
    )
    row(
        [
            card(
                "DESKTOP / DEVICE EFFECTS",
                "Open/focus/arrange apps and windows; volume/media/network/display/lighting changes; optional smart-home/KDE Connect effects; approved input or power operations.",
                "system/*; tools.py; power_actions.py",
                "output",
            ),
            card(
                "PERSISTENCE / EVIDENCE",
                "JSON/SQLite state, memory vault, indexes, drafts, consent records, job revisions, action logs, captions, monitor events and cancellation/verification receipts.",
                "~/.jarvis/ (private runtime state)",
                "state",
            ),
            card(
                "GUARDED CONSEQUENCES",
                "Send/post/pay/book/delete/deploy/update actions require their existing exact-context approvals where applicable. Prepared content or a model's yes is not approval.",
                "action_guard.py; confirmation tools",
                "guard",
            ),
            card(
                "NO OUTPUT IS A VALID BRANCH",
                "Filtered mail/chat; no new input; duplicate; blocked/guest/private restriction; mute/quiet queue; uncertain evidence; declined preview; handoff; unsupported feature.",
                "per-feature gates; action_outcome.py",
                "output",
            ),
        ],
        False,
    )
    section(
        13,
        "SHARED STATE, SECURITY AND THE THREE PERPLEXITY CONNECTIONS",
        "Only these three decision boundaries use the newly integrated larger Perplexity model. The rest of the system retains its existing adapters.",
    )
    row(
        [
            card(
                "PERPLEXITY / OPENROUTER",
                "1. Email urgency/phishing + event gate (09).\n2. WhatsApp persona selection (08).\n3. Direct/backend/research/code advice + backend guard (04/06).\nTyped probabilities; no generated response text.",
                "mercury_decide.py (legacy module name)",
                "model",
            ),
            card(
                "CREDENTIAL / CONFIG BOUNDARY",
                "Dedicated decision key in private env/keys.env; model/feature switches; existing OAuth/subscription logins. Never include key values in prompts, logs or this guide.",
                "openrouter_chat.py; keys.env",
                "guard",
            ),
            card(
                "CONTEXT / ACTION BOUNDARY",
                "Memory, files, web pages, messages, OCR and tool replies are evidence. Application-owned session identity and fresh owner approvals authorize effects.",
                "action_guard.py; feature_sessions.py",
                "guard",
            ),
            card(
                "PERSIST / RETURN / RETRY",
                "Archive/state feeds status and future context. Specific provider quota errors may trigger bounded failover. Timers/queues can defer delivery; error/unknown stays explicit.",
                "backend_pool.py; notify.py; jobs.py",
                "state",
            ),
        ]
    )
    inv = inventory()
    section(
        14,
        "COMPLETE DECLARED TOOL-ENDPOINT INVENTORY",
        f"{sum(len(names) for _, names in inv)} @function_tool endpoints across {len(inv)} source files, extracted from this checkout. This appendix lists declarations, including optional/specialist tools; it does not assert that every endpoint is enabled in every session.",
    )
    for i in range(0, len(inv), 2):
        row(
            [
                card(path.replace("src/", ""), " • ".join(names), path, "state")
                for path, names in inv[i : i + 2]
            ],
            False,
        )
    y += 15
    for line in wrap(
        "HOW TO VERIFY: source paths are printed on every card; GUIDE.md contains searchable text and node IDs; connections.json records drawn edges and section ports; tool-inventory.json records the AST inventory. This is a functional architecture guide, not an exhaustive internal function-call graph or a claim that optional features are live.",
        F,
        W - 2 * PAD,
    ):
        text(PAD, y, line, F)
        y += 38
    y += 65
    OUT.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (W, int(y)), "#f5f8fb")
    draw = ImageDraw.Draw(image)
    for command in commands:
        if command[0] == "text":
            _, xy, value, font, fill = command
            draw.text(xy, value, font=font, fill=fill)
        elif command[0] == "rect":
            _, box, fill, outline = command
            draw.rounded_rectangle(box, radius=12, fill=fill, outline=outline, width=2)
        else:
            _, (x1, y1, x2, y2), fill = command
            draw.line((x1, y1, x2 - 10, y2), fill=fill, width=5)
            draw.polygon([(x2, y2), (x2 - 14, y2 - 10), (x2 - 14, y2 + 10)], fill=fill)
    image.save(OUT / "JARVIS-FULL-PIPELINE.png", optimize=True)
    # A separate top-level overview remains readable without opening the huge poster.
    section_headers = [
        command[1][1]
        for command in commands
        if command[0] == "rect" and command[2] == "#12364c"
    ]
    overview_height = int(section_headers[1] - 12)
    image.crop((0, 0, W, min(overview_height, image.height))).save(
        OUT / "JARVIS-OVERVIEW.png", optimize=True
    )
    preview = image.copy()
    preview.thumbnail((700, 5000))
    preview.save(OUT / "layout-preview.png")
    (OUT / "GUIDE.md").write_text("\n".join(markdown))
    (OUT / "tool-inventory.json").write_text(json.dumps(dict(inv), indent=2))
    ports = [
        {"from_section": "02", "to_section": s}
        for s in ("03", "04", "08", "09", "10", "11")
    ]
    ports += [{"from_section": "04", "to_section": s} for s in ("05", "06", "07", "12")]
    ports += [
        {"from_section": s, "to_section": "12"}
        for s in ("03", "05", "06", "07", "08", "09", "10", "11")
    ]
    (OUT / "connections.json").write_text(
        json.dumps(
            {"nodes": nodes, "drawn_edges": edges, "cross_section_ports": ports},
            indent=2,
        )
    )
    print(
        json.dumps(
            {
                "png": str(OUT / "JARVIS-FULL-PIPELINE.png"),
                "width": W,
                "height": image.height,
                "cards": len(nodes),
                "drawn_edges": len(edges),
                "tool_endpoints": sum(len(names) for _, names in inv),
            }
        )
    )


if __name__ == "__main__":
    build()
