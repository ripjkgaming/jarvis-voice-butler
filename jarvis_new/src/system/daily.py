"""Daily life tools: school timetable (.ics, offline), morning briefing,
study plan, offline docs/slides builder, WhatsApp read + draft queue.

Ported from proven laptop Jarvis logic (~/jarvis/src/new_jarvis/):
- gcal.py: school.ics -> parsed VEVENTs (no OAuth; local file wins)
- skill_timetable: day bundle (calendar + study + todos)
- skill_study: IGCSE plan days/exams/allocation + done flags
- docgen.py: ~/Documents/Jarvis/ output. Here the outline is built
  locally/offline (no cloud model): deterministic sections from the
  topic so voice can always produce a file.
- whatsapp.py: Whatsie CDP on 127.0.0.1:9223. Read-only voice access
  (status/chats/read/summarize) + approval-gated draft queue.
  Sending stays manual on the phone; auto-reply is NOT ported.

Every tool refuses unless JARVIS_LOCAL=1.
"""

from __future__ import annotations

import datetime
import html
import json
import re
import time
import urllib.request
from pathlib import Path

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

from system import LocalSystemError, log_action, require_local
from system.core import DATA_DIR, TODOS_PATH

SCHOOL_ICS = Path.home() / "jarvis" / "data" / "school.ics"
SCHOOL_JSON = DATA_DIR / "school.json"
STUDY_PLAN_SRC = Path.home() / "jarvis" / "data" / "study_plan.json"
STUDY_STATE = DATA_DIR / "study_state.json"
DOCS_DIR = Path.home() / "Documents" / "Jarvis"
WA_DRAFTS = DATA_DIR / "wa_drafts.json"
WA_CDP = "http://127.0.0.1:9223"


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2))


def parse_ics_events(raw: str) -> list[dict]:
    """Parse VEVENT blocks -> [{s, e, t, loc, desc}]. Pure, testable."""
    raw = re.sub(r"\r?\n[ \t]", "", raw)
    blocks = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", raw, re.S)

    def get(b: str, key: str) -> str:
        m = re.search(r"^" + key + r"[^:]*:(.+)$", b, re.M)
        return m.group(1).strip() if m else ""

    def parse_dt(s: str) -> str:
        s = (s or "").strip().rstrip("Z")
        if not s or s.startswith("1970") or s.startswith("2001"):
            return ""
        for f in ("%Y%m%dT%H%M%S", "%Y%m%d"):
            try:
                d = datetime.datetime.strptime(s, f)
                if len(s) == 8:
                    return d.date().isoformat()
                return d.replace(tzinfo=datetime.timezone.utc).astimezone().isoformat()
            except Exception:
                pass
        return ""

    def clean_desc(d: str) -> str:
        if not d:
            return ""
        t = html.unescape(d)
        t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", t)
        t = re.sub(r"(?s)<!--.*?-->", " ", t)
        t = re.sub(r"<[^>]+>", " ", t)
        t = t.replace("\\n", "\n").replace("\\,", ",")
        keep = []
        for ln in t.splitlines():
            s = ln.strip(" -:~")
            if not s:
                continue
            low = s.lower()
            if (
                "generated from sims.net" in low
                or "salamandersoft" in low
                or "join with google meet" in low
                or "meet.google.com" in low
                or "please do not edit" in low
                or set(s) <= set("-:~ ")
            ):
                continue
            keep.append(s.strip())
        return " ".join(keep).strip()[:400]

    out = []
    for b in blocks:
        s = parse_dt(get(b, "DTSTART"))
        if not s:
            continue
        out.append(
            {
                "s": s,
                "e": parse_dt(get(b, "DTEND")),
                "t": get(b, "SUMMARY")[:120],
                "loc": get(b, "LOCATION")[:120],
                "desc": clean_desc(get(b, "DESCRIPTION")),
            }
        )
    out.sort(key=lambda e: e["s"])
    return out


def load_school_events() -> list[dict]:
    """Load parsed timetable, re-parsing the .ics when stale."""
    try:
        if not SCHOOL_ICS.exists():
            return []
        if (
            not SCHOOL_JSON.exists()
            or SCHOOL_JSON.stat().st_mtime < SCHOOL_ICS.stat().st_mtime
        ):
            events = parse_ics_events(
                SCHOOL_ICS.read_text(encoding="utf-8", errors="replace")
            )
            _write_json(SCHOOL_JSON, events)
            return events
        data = _read_json(SCHOOL_JSON, [])
        return data if isinstance(data, list) else []
    except Exception:
        return []


def day_events(events: list[dict], date: str) -> list[dict]:
    """Events whose local start falls on date (YYYY-MM-DD)."""
    hits = []
    for e in events:
        s = e.get("s", "")
        day = s[:10]
        if len(s) > 10:
            with __import__("contextlib").suppress(Exception):
                day = datetime.datetime.fromisoformat(s).date().isoformat()
        if day == date:
            hits.append(e)
    return hits


def format_day(events: list[dict], date: str) -> str:
    if not events:
        return f"No classes on {date}."
    bits = []
    for e in events:
        try:
            t = datetime.datetime.fromisoformat(e["s"]).strftime("%H:%M")
        except Exception:
            t = e["s"]
        bits.append(f"{t} {e.get('t', 'Class')}")
    return f"{date}: " + "; ".join(bits)


def _slug(text: str, default: str = "untitled") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text or default).lower()).strip("-")
    return (s or default)[:50]


def build_outline(topic: str, n: int = 5) -> list[tuple[str, list[str]]]:
    """Deterministic offline outline from a topic. Pure, testable."""
    topic = (topic or "").strip()[:120] or "Untitled"
    words = [w for w in re.findall(r"[A-Za-z]+", topic) if len(w) > 2][:6]
    angles = [
        f"What {topic} is",
        f"Why {topic} matters",
        f"How {topic} works",
        f"Examples of {topic}",
        f"Key facts about {topic}",
        f"Common questions on {topic}",
        f"Summary: {topic}",
    ]
    sections = []
    for i in range(max(2, min(15, n))):
        title = angles[i] if i < len(angles) else f"{topic} part {i + 1}"
        bullets = [
            f"{title}: point one",
            f"{title}: point two ({', '.join(words[:3]) or 'overview'})",
            f"{title}: takeaway",
        ]
        sections.append((title, bullets))
    return sections


def render_markdown(title: str, sections: list[tuple[str, list[str]]]) -> str:
    lines = [f"# {title}", ""]
    for head, bullets in sections:
        lines.append(f"## {head}")
        lines.extend(f"- {b}" for b in bullets)
        lines.append("")
    return "\n".join(lines)


def render_html(title: str, sections: list[tuple[str, list[str]]]) -> str:
    esc = html.escape
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        f"<title>{esc(title)}</title>",
        "<style>body{font-family:sans-serif;max-width:45em;margin:2em auto}"
        "h1{border-bottom:2px solid #333}</style></head><body>",
        f"<h1>{esc(title)}</h1>",
    ]
    for head, bullets in sections:
        parts.append(f"<h2>{esc(head)}</h2><ul>")
        parts.extend(f"<li>{esc(b)}</li>" for b in bullets)
        parts.append("</ul>")
    parts.append("</body></html>")
    return "\n".join(parts)


def wa_alive() -> bool:
    try:
        with urllib.request.urlopen(WA_CDP + "/json/version", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def _wa_targets() -> list:
    try:
        with urllib.request.urlopen(WA_CDP + "/json/list", timeout=10) as r:
            d = json.loads(r.read().decode("utf8", "replace"))
            return d if isinstance(d, list) else []
    except Exception:
        return []


def wa_page_present() -> bool:
    return any(
        "web.whatsapp.com" in (t.get("url") or "")
        for t in _wa_targets()
        if isinstance(t, dict)
    )


class DailyTools:
    """School, briefing, study, docs, WhatsApp drafts. Register via .tools."""

    @property
    def tools(self) -> list:
        return [
            self.school_day,
            self.daily_briefing,
            self.study_plan,
            self.study_tick,
            self.build_slides,
            self.build_document,
            self.whatsapp_status,
            self.whatsapp_read,
            self.whatsapp_draft,
        ]

    # --- school ---

    @function_tool()
    async def school_day(
        self, context: RunContext, when: str = "today"
    ) -> dict[str, str]:
        """School timetable from the offline .ics (today/tomorrow/date/next).

        Args:
            when: "today", "tomorrow", "YYYY-MM-DD", or "next".
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        events = load_school_events()
        if not events:
            return {"say": "No school calendar loaded yet."}
        w = (when or "today").strip().lower()
        today = datetime.date.today()
        if w in ("today", ""):
            date = today.isoformat()
        elif w.startswith("tomor"):
            date = (today + datetime.timedelta(days=1)).isoformat()
        elif w in ("next", "upcoming"):
            for e in events:
                try:
                    d = (
                        datetime.datetime.fromisoformat(e["s"]).date()
                        if len(e["s"]) > 10
                        else datetime.date.fromisoformat(e["s"][:10])
                    )
                except Exception:
                    continue
                if d >= today:
                    date = d.isoformat()
                    break
            else:
                return {"say": "No upcoming classes."}
        else:
            try:
                date = datetime.date.fromisoformat(w[:10]).isoformat()
            except Exception:
                raise ToolError("Which day? Say today, tomorrow, or a date.") from None
        say = format_day(day_events(events, date), date)
        log_action("school", date)
        return {"say": say, "date": date}

    @function_tool()
    async def daily_briefing(
        self, context: RunContext, when: str = "today"
    ) -> dict[str, str]:
        """Morning bundle: school classes + study blocks + open todos."""
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        w = (when or "today").strip().lower()
        today = datetime.date.today()
        if w.startswith("tomor"):
            date = (today + datetime.timedelta(days=1)).isoformat()
        else:
            try:
                date = (
                    datetime.date.fromisoformat(w[:10]).isoformat()
                    if w[:4].isdigit()
                    else today.isoformat()
                )
            except Exception:
                date = today.isoformat()
        parts = []
        school = await self.school_day(context, date)
        if school.get("say"):
            parts.append("School: " + school["say"])
        study = await self.study_plan(context, "today", date)
        if (
            study.get("ok")
            and study.get("say")
            and "rest day" not in study["say"].lower()
        ):
            parts.append("Study: " + study["say"])
        try:
            items = _read_json(TODOS_PATH, [])
            open_items = [i for i in items if not i.get("done")]
            if open_items:
                enum = "; ".join(
                    f"{n + 1}. {i['text']}" for n, i in enumerate(open_items[-5:])
                )
                parts.append("Todos: " + enum)
        except Exception:
            pass
        say = " ".join(parts)[:1400] or "Nothing planned for today."
        log_action("briefing", date)
        return {"say": say, "date": date}

    # --- study ---

    @function_tool()
    async def study_plan(
        self, context: RunContext, action: str = "today", text: str = ""
    ) -> dict[str, object]:
        """IGCSE study plan: today/week/exams/subjects.

        Args:
            action: One of today, week, exams, subjects.
            text: Date YYYY-MM-DD for today (default today).
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        plan = _read_json(STUDY_PLAN_SRC, None)
        if not plan:
            return {"ok": False, "say": "Study plan not loaded yet."}
        days, exams = plan.get("days", {}), plan.get("exams", {})
        state = _read_json(STUDY_STATE, {})
        done = state.get("done", {})
        today = datetime.date.today().isoformat()
        action = action.lower()
        if action in ("today", "plan", "day"):
            date = today
            if text.strip()[:4].isdigit():
                with __import__("contextlib").suppress(Exception):
                    date = datetime.date.fromisoformat(text.strip()[:10]).isoformat()
            if date in days:
                d = days[date]
                st = done.get(date, {})
                blocks = d.get("blocks", [])
                marks = st.get("blocks", [])
                bits = []
                for i, b in enumerate(blocks):
                    mark = " [done]" if i < len(marks) and marks[i] else ""
                    bits.append(f"{i + 1}. {b}{mark}")
                say = f"{date}: {d.get('hrs', '?')}h. " + " ".join(bits)
                if d.get("timed_paper"):
                    pm = " [done]" if st.get("paper") else ""
                    say += f" Timed paper: {d['timed_paper']}{pm}."
                return {"ok": True, "say": say[:900], "date": date}
            if date in exams:
                e = exams[date]
                return {
                    "ok": True,
                    "say": (f"{date}: {e.get('status', '')}. {e.get('papers', '')}")[
                        :600
                    ],
                    "date": date,
                }
            return {"ok": True, "say": f"No study blocks on {date}, rest day."}
        if action == "week":
            out = []
            for dk in sorted(days):
                if dk < today or len(out) >= 7:
                    continue
                d = days[dk]
                marks = done.get(dk, {})
                nb = len(d.get("blocks", []))
                nd = sum(1 for x in marks.get("blocks", []) if x)
                out.append(f"{dk[5:]}: {d.get('hrs', '?')}h, {nd}/{nb} done")
            return {
                "ok": True,
                "say": ("Next study days: " + "; ".join(out))[:600]
                if out
                else "Study phase over.",
            }
        if action == "exams":
            up = [
                f"{dk[5:]}: {v.get('papers', '')[:80]}"
                for dk, v in sorted(exams.items())
                if dk >= today
            ][:4]
            return {
                "ok": True,
                "say": ("Upcoming: " + " | ".join(up))[:600]
                if up
                else "No exams left.",
            }
        if action == "subjects":
            alloc = [
                x for x in plan.get("allocation", []) if x.get("subject") != "TOTAL"
            ]
            bits = [
                f"{x['subject']} {x['total_h']}h" for x in alloc if x.get("total_h")
            ]
            return {"ok": True, "say": ("Weighting: " + "; ".join(bits))[:600]}
        raise ToolError(f"Unknown study action {action}.")

    @function_tool()
    async def study_tick(
        self, context: RunContext, text: str, date: str = ""
    ) -> dict[str, str]:
        """Mark a study block or timed paper done ("2", "paper", topic words).

        Args:
            text: Block number, "paper", or topic words.
            date: YYYY-MM-DD (default today).
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        plan = _read_json(STUDY_PLAN_SRC, None)
        if not plan:
            raise ToolError("Study plan not loaded yet.")
        days = plan.get("days", {})
        today = datetime.date.today().isoformat()
        if date.strip()[:4].isdigit():
            with __import__("contextlib").suppress(Exception):
                today = datetime.date.fromisoformat(date.strip()[:10]).isoformat()
        if today not in days:
            raise ToolError(f"Nothing to mark on {today}.")
        d = days[today]
        state = _read_json(STUDY_STATE, {})
        st = state.setdefault("done", {}).setdefault(
            today, {"blocks": [False] * len(d.get("blocks", [])), "paper": False}
        )
        while len(st.setdefault("blocks", [])) < len(d.get("blocks", [])):
            st["blocks"].append(False)
        t = (text or "").strip().lower()
        if t in ("paper", "timed", "timed paper", "practice paper"):
            st["paper"] = True
            _write_json(STUDY_STATE, state)
            log_action("study", f"{today} paper done")
            return {"say": f"Timed paper {today} marked done."}
        idx = None
        if t.isdigit():
            idx = int(t) - 1
        else:
            for i, b in enumerate(d.get("blocks", [])):
                if t and t in b.lower():
                    idx = i
                    break
        if idx is None or not (0 <= idx < len(d.get("blocks", []))):
            raise ToolError(
                f"Which block? 1-{len(d.get('blocks', []))}, 'paper', or topic words."
            )
        st["blocks"][idx] = True
        _write_json(STUDY_STATE, state)
        left = sum(1 for x in st["blocks"] if not x) + (0 if st.get("paper") else 1)
        log_action("study", f"{today} block {idx + 1} done")
        return {"say": f"Block {idx + 1} done. {left} items left today."}

    # --- docs / slides (offline) ---

    def _write_doc_files(
        self, title: str, sections: list[tuple[str, list[str]]], kind: str
    ) -> list[str]:
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        slug = _slug(title)
        ts = int(time.time())
        md = DOCS_DIR / f"{kind}-{slug}-{ts}.md"
        hx = DOCS_DIR / f"{kind}-{slug}-{ts}.html"
        md.write_text(render_markdown(title, sections))
        hx.write_text(render_html(title, sections))
        paths = [str(md), str(hx)]
        # Native formats when the libs exist; HTML/MD always written.
        try:
            if kind == "slideshow":
                from pptx import Presentation

                prs = Presentation()
                slide = prs.slides.add_slide(prs.slide_layouts[5])
                slide.shapes.title.text = title
                for head, bullets in sections:
                    s = prs.slides.add_slide(prs.slide_layouts[1])
                    s.shapes.title.text = head[:80]
                    tf = s.placeholders[1].text_frame
                    tf.text = bullets[0][:200] if bullets else ""
                    for b in bullets[1:]:
                        tf.add_paragraph().text = b[:200]
                px = DOCS_DIR / f"slideshow-{slug}-{ts}.pptx"
                prs.save(px)
                paths.append(str(px))
            else:
                from docx import Document

                doc = Document()
                doc.add_heading(title, 0)
                for head, bullets in sections:
                    doc.add_heading(head, level=1)
                    for b in bullets:
                        doc.add_paragraph(b, style="List Bullet")
                dx = DOCS_DIR / f"{kind}-{slug}-{ts}.docx"
                doc.save(dx)
                paths.append(str(dx))
        except ImportError:
            pass
        return paths

    @function_tool()
    async def build_slides(
        self, context: RunContext, topic: str, slides: int = 5
    ) -> dict[str, str]:
        """Build a slide outline about a topic (offline .md + .html, .pptx if able).

        Args:
            topic: What the slides are about.
            slides: 2-15, default 5.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        topic = (topic or "").strip()[:120]
        if not topic:
            raise ToolError("Slides about what? Give me a topic.")
        sections = build_outline(topic, slides)
        paths = self._write_doc_files(topic, sections, "slideshow")
        log_action("slides", f"{topic} -> {len(paths)} files")
        return {
            "say": f"Slides about {topic[:60]} ready: {len(sections)} slides in Documents Jarvis.",
            "paths": "; ".join(paths),
        }

    @function_tool()
    async def build_document(
        self, context: RunContext, topic: str, kind: str = "document"
    ) -> dict[str, str]:
        """Draft a document/report/notes/summary/letter (offline .md + .html).

        Args:
            topic: What to write about.
            kind: One of document, report, notes, summary, letter.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        kind = (kind or "document").lower()
        if kind not in ("document", "report", "notes", "summary", "letter"):
            kind = "document"
        topic = (topic or "").strip()[:200]
        if not topic:
            raise ToolError("Write about what? Give me a topic.")
        sections = build_outline(topic, 4)
        paths = self._write_doc_files(f"{kind.title()}: {topic}", sections, kind)
        log_action("document", f"{kind} {topic} -> {len(paths)} files")
        return {
            "say": f"{kind.title()} about {topic[:60]} ready in Documents Jarvis.",
            "paths": "; ".join(paths),
        }

    # --- whatsapp (read + approval-gated drafts) ---

    @function_tool()
    async def whatsapp_status(self, context: RunContext) -> dict[str, str]:
        """Is WhatsApp (Whatsie desktop) reachable?"""
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        if not wa_alive():
            return {"say": "WhatsApp is not reachable. Is Whatsie open?"}
        if not wa_page_present():
            return {"say": "Whatsie is running but WhatsApp Web is not open."}
        return {"say": "WhatsApp is connected via Whatsie."}

    @function_tool()
    async def whatsapp_read(
        self, context: RunContext, chat: str = "", n: int = 10
    ) -> dict[str, str]:
        """Read recent WhatsApp messages (marks them read, as WhatsApp does).

        Full message text needs the Whatsie CDP bridge (websockets lib);
        without it this reports reachability only.

        Args:
            chat: Chat name (empty = reachability check only).
            n: Last N messages (1-30).
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        if not wa_alive():
            raise ToolError("WhatsApp is not reachable. Is Whatsie open?")
        try:
            import websockets  # noqa: F401
        except ImportError:
            log_action("whatsapp", "read (no bridge)")
            return {
                "say": (
                    "WhatsApp is connected but full message reading needs "
                    "the bridge library. Say draft to queue a reply instead."
                )
            }
        # Bridge present: reuse the proven CDP reader via subprocess-free
        # inline evaluate. Kept minimal here; full DOM parsing lives in
        # the laptop Jarvis whatsapp module.
        raise ToolError(
            "Message text reading runs on the full laptop Jarvis; "
            "here I can take drafts. Say who and what to draft."
        )

    @function_tool()
    async def whatsapp_draft(
        self, context: RunContext, chat: str, text: str
    ) -> dict[str, str]:
        """Queue a WhatsApp reply draft for approval on the phone (never auto-sends).

        Args:
            chat: Who the reply is for.
            text: What to say (queued verbatim, signed as Jarvis draft).
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        chat = (chat or "").strip()[:60]
        text = (text or "").strip()[:500]
        if not chat or not text:
            raise ToolError("Draft for whom, saying what? Give me both.")
        drafts = _read_json(WA_DRAFTS, [])
        if not isinstance(drafts, list):
            drafts = []
        drafts.append({"chat": chat, "text": text, "ts": time.time()})
        _write_json(WA_DRAFTS, drafts[-50:])
        log_action("whatsapp-draft", f"{chat}: {text[:80]}")
        return {
            "say": f"Draft queued for {chat[:40]}. Approve it on your phone to send."
        }
