"""Voice tool for Sir's exam schedule (see src/exams.py)."""

from __future__ import annotations

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

from system import LocalSystemError, log_action, require_local


class ExamTools:
    """Exam schedule: next, list, find, add, remove. Register via .tools."""

    @property
    def tools(self) -> list:
        return [self.exam_schedule]

    @function_tool()
    async def exam_schedule(
        self,
        context: RunContext,
        action: str = "next",
        subject: str = "",
        date: str = "",
        time: str = "",
        end: str = "",
        location: str = "",
        days: int = 30,
    ) -> dict[str, str]:
        """Sir's exam schedule. "When's my next exam" = next; "what exams do I
        have this month" = upcoming; "when's my physics exam" / "how long until
        chemistry" = find with subject; "add maths paper 2 on 14 Oct at 9am" =
        add; "remove the biology mock" = remove. Imported exam timetables
        (import_schedule_to_calendar) land here automatically.

        Args:
            action: next, upcoming, find, add, or remove.
            subject: Exam name or subject words (find/add/remove).
            date: For add: YYYY-MM-DD, "tomorrow", "monday" or "14 Oct".
            time: For add: start time like "9am" or "13:30".
            end: For add: optional end time.
            location: For add: optional room or venue.
            days: For upcoming: how many days ahead (1-365).
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        import exams

        action = (action or "next").strip().lower()
        subject = " ".join((subject or "").split())[:120]
        if action == "next":
            nxt = exams.next_exam()
            if not nxt:
                return {
                    "say": "No exams on the schedule, Sir. Tell me one to add, or import your timetable."
                }
            return {"say": f"Your next exam: {exams.describe(nxt)}."}
        if action in ("upcoming", "list", "all"):
            return {"say": exams.summary(days=max(1, min(365, int(days or 30))))[:1500]}
        if action in ("find", "when", "countdown"):
            if not subject:
                raise ToolError("Which exam? Give me the subject.")
            rows = exams.find(subject)
            if not rows:
                past = exams.find(subject, include_past=True)
                if past:
                    return {
                        "say": f"Your last {subject} exam was {exams.describe(past[-1])}. Nothing more is scheduled."
                    }
                return {"say": f"I have no {subject} exam on the schedule, Sir."}
            return {"say": "; ".join(exams.describe(e) for e in rows[:5])[:1200] + "."}
        if action == "add":
            iso = exams.parse_date(date)
            if not subject or not iso:
                raise ToolError("I need the exam name and a date, Sir.")
            start = exams.parse_time(time) if time else ""
            if time and not start:
                raise ToolError(f"I could not read the time {time!r}.")
            outcome = exams.add(
                {
                    "title": subject,
                    "date": iso,
                    "start": start,
                    "end": exams.parse_time(end) if end else "",
                    "location": location,
                }
            )
            if outcome not in ("added", "updated"):
                raise ToolError("I could not save that exam.")
            log_action("exams", f"{outcome} {subject} {iso}")
            entry = exams.find(subject, include_past=True)
            said = exams.describe(entry[-1]) if entry else f"{subject} on {iso}"
            return {"say": f"{outcome.title()}: {said}."}
        if action in ("remove", "delete"):
            gone = exams.remove(subject, exams.parse_date(date) if date else "")
            if not gone:
                return {"say": f"No saved exam matches {subject or 'that'}, Sir."}
            log_action("exams", f"removed {len(gone)} matching {subject}")
            return {
                "say": f"Removed {len(gone)}: "
                + "; ".join(e["title"] for e in gone[:4])
                + "."
            }
        raise ToolError("action must be next, upcoming, find, add or remove.")
