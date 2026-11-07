"""Conservative Gemini Live discovery, quota gate, and session-time selection.

Missing quotas never mean unlimited. A newer numeric generation is our explicit
upgrade policy, not a claim of independently benchmarked subjective quality.
"""

import argparse
import asyncio
import fcntl
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_CHAIN = [
    "gemini-3.8-live",
    "gemini-3.1-flash-live-preview",
    "gemini-2.5-flash-native-audio-latest",
]
RATE_URL = "https://ai.google.dev/gemini-api/docs/rate-limits"
LOG = logging.getLogger(__name__)


def selection_path():
    return Path(
        os.environ.get(
            "JARVIS_VOICE_SELECTION_FILE",
            str(Path.home() / ".jarvis/voice-upgrade/selection.json"),
        )
    ).expanduser()


def normalize(value):
    return re.sub(r"[^a-z0-9]", "", value.lower())


def generation(model):
    match = re.match(r"^gemini-(\d+)\.(\d+)-", model)
    return tuple(map(int, match.groups())) if match else (0, 0)


def selected_chain(defaults):
    """Read on each new session; an explicit env chain always wins."""
    manual = [
        m.strip()
        for m in os.environ.get("JARVIS_VOICE_MODELS", "").split(",")
        if m.strip()
    ]
    if manual:
        return manual
    try:
        data = json.loads(selection_path().read_text())
        model = data.get("model", "")
        if (
            isinstance(model, str)
            and re.fullmatch(r"gemini-[a-z0-9.-]+", model)
            and generation(model) > generation(defaults[0])
        ):
            previous = data.get("previous_chain", [])
            if not isinstance(previous, list):
                previous = []
            previous = [
                m
                for m in previous
                if isinstance(m, str) and re.fullmatch(r"gemini-[a-z0-9.-]+", m)
            ]
            return list(dict.fromkeys([model, *previous, *defaults]))
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return list(defaults)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".voice-upgrade-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class QuotaTables(HTMLParser):
    """Keep tier headings and table cells together; never match global text."""

    def __init__(self):
        super().__init__()
        self.tier = None
        self.heading = None
        self.cell = None
        self.rows = None
        self.row = []
        self.tables = []

    def handle_starttag(self, tag, attrs):
        if tag in ("h1", "h2", "h3", "h4"):
            self.heading = ""
        elif tag == "table":
            self.rows = []
        elif tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.cell = ""

    def handle_data(self, data):
        if self.heading is not None:
            self.heading += data
        if self.cell is not None:
            self.cell += data

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "h4") and self.heading is not None:
            heading = normalize(self.heading)
            if "freetier" in heading:
                self.tier = "free"
            elif match := re.fullmatch(r"tier([123])", heading):
                self.tier = "tier" + match[1]
            elif tag in ("h1", "h2"):
                self.tier = None
            self.heading = None
        elif tag in ("td", "th") and self.cell is not None:
            self.row.append(self.cell.strip())
            self.cell = None
        elif tag == "tr" and self.rows is not None:
            self.rows.append(self.row)
        elif tag == "table" and self.rows is not None:
            self.tables.append((self.tier, self.rows))
            self.rows = None


def public_unlimited(html, tier):
    parser = QuotaTables()
    parser.feed(html)
    eligible = set()
    for table_tier, rows in parser.tables:
        if table_tier != tier or not rows:
            continue
        headers = [normalize(h) for h in rows[0]]
        try:
            rpm = next(
                i for i, h in enumerate(headers) if h in ("rpm", "requestsperminute")
            )
            rpd = next(
                i for i, h in enumerate(headers) if h in ("rpd", "requestsperday")
            )
            model = next(i for i, h in enumerate(headers) if h in ("model", "models"))
        except StopIteration:
            continue
        for row in rows[1:]:
            if len(row) > max(rpm, rpd, model) and all(
                row[i].strip().lower() in ("unlimited", "∞") for i in (rpm, rpd)
            ):
                eligible.add(normalize(row[model]))
    return eligible


def fetch(url, key=None):
    headers = {"User-Agent": "JarvisVoiceUpgrade/1.0"}
    if key:
        headers["x-goog-api-key"] = key
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        return response.read(5_000_000).decode("utf-8")


def list_models(key):
    models = []
    token = None
    for _ in range(100):
        query = {"pageSize": 1000}
        if token:
            query["pageToken"] = token
        data = json.loads(
            fetch(
                "https://generativelanguage.googleapis.com/v1beta/models?"
                + urlencode(query),
                key,
            )
        )
        models.extend(data.get("models", []))
        token = data.get("nextPageToken")
        if not token:
            return models
    raise RuntimeError("Model pagination did not finish")


def candidates(models, chain, unlimited):
    result = []
    for model in models:
        name = model.get("name", "").removeprefix("models/")
        if not re.fullmatch(r"gemini-[a-z0-9.-]+", name):
            continue
        if "bidiGenerateContent" not in model.get("supportedGenerationMethods", []):
            continue
        if not ("live" in name or "native-audio" in name):
            continue
        if "thinking" in name or generation(name) <= generation(chain[0]):
            continue
        # Exact ID or exact published display name only; never fuzzy-match quotas.
        if (
            normalize(name) in unlimited
            or normalize(model.get("displayName", "")) in unlimited
        ):
            result.append(name)
    return sorted(set(result), key=lambda name: (generation(name), name), reverse=True)


async def probe_audio(model, key):
    """Verify native audio and tool-schema support using Jarvis's voice."""
    from google import genai

    client = genai.Client(api_key=key)
    try:
        async with client.aio.live.connect(
            model=model,
            config={
                "response_modalities": ["AUDIO"],
                "speech_config": {
                    "voice_config": {
                        "prebuilt_voice_config": {"voice_name": "Enceladus"}
                    }
                },
                "input_audio_transcription": {},
                "output_audio_transcription": {},
                "tools": [
                    {
                        "function_declarations": [
                            {
                                "name": "jarvis_upgrade_probe",
                                "description": "Connectivity test; do not call.",
                            }
                        ]
                    }
                ],
            },
        ) as session:
            await session.send_client_content(
                turns={
                    "role": "user",
                    "parts": [{"text": "Say: Jarvis voice ready. Do not call tools."}],
                },
                turn_complete=True,
            )
            async for message in session.receive():
                if message.data:
                    return True
            return False
    finally:
        await client.aio.aclose()
        client.close()


def promote(options, chain, path, probe):
    for model in options:
        try:
            passed = probe(model)
        except Exception as exc:
            LOG.warning("Probe failed for %s (%s)", model, type(exc).__name__)
            continue
        if passed:
            write_json(
                path,
                {
                    "model": model,
                    "previous_chain": chain,
                    "selected_at": datetime.now(timezone.utc).isoformat(),
                    "policy": "newer-generation+unlimited-rpm-rpd+audio-probe",
                },
            )
            return model
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover only; no audio requests or selection changes",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")
    path = selection_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / "check.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            LOG.info("Another check is running")
            return 0
        report = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "dry_run": args.dry_run,
        }
        try:
            if os.environ.get("JARVIS_VOICE_MODELS", "").strip():
                report.update(
                    status="manual_override",
                    reason="JARVIS_VOICE_MODELS takes precedence",
                )
            else:
                key = os.environ.get("GOOGLE_API_KEY") or os.environ.get(
                    "GEMINI_API_KEY"
                )
                if not key:
                    raise RuntimeError("Missing Gemini API key")
                tier = normalize(os.environ.get("JARVIS_VOICE_QUOTA_TIER", "free"))
                if tier not in ("free", "tier1", "tier2", "tier3"):
                    raise ValueError("Invalid JARVIS_VOICE_QUOTA_TIER")
                chain = selected_chain(DEFAULT_CHAIN)
                unlimited = public_unlimited(fetch(RATE_URL), tier)
                options = candidates(list_models(key), chain, unlimited)
                report.update(
                    tier=tier,
                    current_chain=chain,
                    candidates=options,
                    quota_source=RATE_URL,
                )
                if not unlimited:
                    report.update(
                        status="quota_unverified",
                        reason="No explicit unlimited RPM and RPD table for this tier; keeping current model",
                    )
                elif args.dry_run:
                    report.update(status="dry_run")
                else:
                    chosen = promote(
                        options,
                        chain,
                        path,
                        lambda model: asyncio.run(
                            asyncio.wait_for(probe_audio(model, key), timeout=45)
                        ),
                    )
                    report.update(
                        status="upgraded" if chosen else "unchanged", selected=chosen
                    )
            write_json(path.parent / "last-check.json", report)
            LOG.info("%s", json.dumps(report))
            return 0
        except Exception as exc:
            # Do not log exception strings: HTTP/SDK messages can contain credentials.
            report.update(status="error", error_type=type(exc).__name__)
            write_json(path.parent / "last-check.json", report)
            LOG.error(
                "Voice check failed (%s); current selection preserved",
                type(exc).__name__,
            )
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
