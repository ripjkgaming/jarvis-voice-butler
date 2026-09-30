"""Exam timetable parsing, gaps, and the exam_times tool."""

import datetime as dt

import exam_timetable as ex

MD = """| Date | Time | Subject / Paper |
| --- | --- | --- |
| Wednesday, 30 September | 11:30 am | Physics P6 (1h) |
|  | 1:30 pm | Computer Science P2 (1h 45m) |
| Friday, 2 October | 8:30 am | Economics P2 (1h 30m) |
|  | 11:30 am | Economics P1 (1h) |
|  | 1:30 pm | Biology P2 + P4 (30m + 1h 15m) |
| Monday, 5 October | 11:30 am | Hindi P2 (Listening) (55m) |
|  | 1:30 pm | Additional Maths P1 (1h) |
"""
TODAY = dt.date(2026, 9, 30)


def test_parse_durations_dates_and_continuation_rows() -> None:
    rows = ex.parse_timetable(MD, TODAY)
    assert len(rows) == 7
    physics = rows[0]
    assert physics["date"] == dt.date(2026, 9, 30)
    assert (physics["start"], physics["end"], physics["minutes"]) == (690, 750, 60)
    cs = rows[1]
    assert cs["date"] == dt.date(2026, 9, 30) and cs["minutes"] == 105
    bio = rows[4]
    assert bio["minutes"] == 105 and bio["title"] == "Biology P2 + P4"
    assert rows[5]["minutes"] == 55


def test_today_answer_gives_times_lengths_and_gap() -> None:
    rows = ex.parse_timetable(MD, TODAY)
    say = ex.answer(rows, "today", TODAY)
    assert "Physics P6 11:30am to 12:30pm (1h)" in say
    assert "Then 1h free" in say
    assert "Computer Science P2 1:30pm to 3:15pm (1h 45m)" in say


def test_gaps_for_a_day_with_three_papers() -> None:
    rows = ex.parse_timetable(MD, TODAY)
    say = ex.answer(rows, "friday", TODAY)
    assert "Then 1h 30m free" in say and "Then 1h free" in say
    assert "Then 1h 5m free" in ex.answer(rows, "2026-10-05", TODAY)


def test_no_exam_day_points_to_next() -> None:
    rows = ex.parse_timetable(MD, TODAY)
    say = ex.answer(rows, "tomorrow", TODAY)
    assert say.startswith("Nothing then") and "Fri 02 Oct" in say


def test_all_and_finished() -> None:
    rows = ex.parse_timetable(MD, TODAY)
    assert "Mon 05 Oct" in ex.answer(rows, "all", TODAY)
    assert "No exams left" in ex.answer(rows, "all", dt.date(2026, 12, 1))


def test_load_adopts_download_once(tmp_path) -> None:
    dl = tmp_path / "Downloads"
    dl.mkdir()
    (dl / ex.DOWNLOAD_NAME).write_text(MD)
    data = tmp_path / "data"
    assert len(ex.load(data, dl, TODAY)) == 7
    assert (data / ex.DATA_NAME).exists()
    (dl / ex.DOWNLOAD_NAME).unlink()
    assert len(ex.load(data, dl, TODAY)) == 7  # saved copy survives
    assert ex.load(tmp_path / "empty", None, TODAY) == []


async def test_exam_times_tool(monkeypatch, tmp_path) -> None:
    import system.daily as daily

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(daily, "DATA_DIR", tmp_path)
    (tmp_path / ex.DATA_NAME).write_text(MD)
    monkeypatch.setattr(ex.dt, "date", type("D", (dt.date,), {"today": staticmethod(lambda: TODAY)}))
    out = await daily.DailyTools.exam_times(daily.DailyTools(), None, when="today")
    assert "Physics P6" in out["say"] and "free" in out["say"]
