"""wake_sequence: away summary, voice interview, planner, spawn, watch.

Every test injects fake speak, listen, judge, planner, spawner, starter,
report and summarise, so nothing real is spoken, heard, spawned or sent. The
one exception is the default starter test, which replaces subprocess.Popen.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

import wake_sequence as ws

SUMMARY = "While you were away, I replied to one email, Sir."
REPORT = {
    "since": 0.0,
    "counts": {},
    "items": [],
    "deferred_decisions": [
        {
            "ts": 1.0,
            "kind": "deferred_decision",
            "summary": "Reply to the landlord?",
            "detail": None,
        }
    ],
}
QUIET_REPORT = {"since": 0.0, "counts": {}, "items": [], "deferred_decisions": []}
NOW = 1700000000.0


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("JARVIS_AGENTS_DIR", str(tmp_path / "agents"))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("JARVIS_WAKE_WORK_DIR", str(work))
    return tmp_path


class Speaker:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.said: list[str] = []

    def __call__(self, text: str) -> bool:
        self.said.append(text)
        return self.ok


class Listener:
    """Pops scripted answers. Exceptions raise, None means mic unusable, and an
    exhausted script means silence (""), so an interview always terminates."""

    def __init__(self, answers=()) -> None:
        self.answers = list(answers)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if not self.answers:
            return ""
        item = self.answers.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class Judge:
    def __init__(self, verdicts=()) -> None:
        self.verdicts = list(verdicts)
        self.seen: list[list] = []

    def __call__(self, qa):
        self.seen.append(list(qa))
        if not self.verdicts:
            return {"clear": True, "question": ""}
        item = self.verdicts.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class Planner:
    def __init__(self, results=()) -> None:
        self.results = list(results)
        self.calls: list[tuple[list, list]] = []

    def __call__(self, qa, decisions):
        self.calls.append((list(qa), list(decisions)))
        if not self.results:
            return []
        item = self.results.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class Spawner:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.calls: list[list] = []

    def __call__(self, tasks):
        self.calls.append(list(tasks))
        if self.error is not None:
            raise self.error
        return [{"task_id": t["task_id"], "status": "running"} for t in tasks]


class Starter:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return True


def _run(**overrides):
    """Build fakes, run the sequence, and return (result, fakes)."""
    fakes = {
        "speak": Speaker(),
        "listen": Listener(["Do the work please."] * 3),
        "judge": Judge(),
        "planner": Planner(),
        "spawner": Spawner(),
        "starter": Starter(),
    }
    fakes.update({k: v for k, v in overrides.items() if k in fakes})
    idle = overrides.get("idle", lambda: 0.0)
    report = overrides.get("report", lambda: REPORT)
    summarise = overrides.get("summarise", lambda rep: SUMMARY)
    result = ws.run_wake_sequence(
        speak=fakes["speak"],
        listen=fakes["listen"],
        judge=fakes["judge"],
        planner=fakes["planner"],
        spawner=fakes["spawner"],
        starter=fakes["starter"],
        report=report,
        summarise=summarise,
        idle=idle,
        **{k: v for k, v in overrides.items() if k in ("max_questions", "tries")},
    )
    return result, fakes


# ------------------------------------------------------------- full flow


def test_full_flow_speaks_interviews_plans_spawns_and_confirms(home: Path):
    listen = Listener(
        [
            "Yes, send the landlord a short reply.",  # deferred decision
            "Build the quarterly deck.",  # first question
            "For the board, due Friday.",  # follow-up
        ]
    )
    judge = Judge(
        [
            {"clear": False, "question": "Who is it for?"},
            {"clear": True, "question": ""},
        ]
    )
    planner = Planner(
        [
            [
                {
                    "task_id": "deck",
                    "brief": "Build the quarterly deck for the board by Friday.",
                    "cwd": str(home / "work"),
                },
                {
                    "task_id": "landlord",
                    "brief": "Send the landlord a short yes.",
                    "cwd": "/no/such/directory",
                },
            ]
        ]
    )
    result, f = _run(listen=listen, judge=judge, planner=planner)

    assert f["speak"].said[0] == SUMMARY
    assert "Decision for you, Sir: Reply to the landlord. What should I do?" in (
        f["speak"].said
    )
    assert f["speak"].said[-1] == "2 agents spawned, Sir."
    assert result["status"] == "done"
    assert result["spawned"] == 2
    assert result["questions"] == 2
    assert f["judge"].seen[-1] == [
        ("What would you like me to get done now, Sir?", "Build the quarterly deck."),
        ("Who is it for?", "For the board, due Friday."),
    ]

    qa, decisions = f["planner"].calls[0]
    assert len(qa) == 2
    assert decisions == [
        ("Reply to the landlord?", "Yes, send the landlord a short reply.")
    ]

    assert len(f["spawner"].calls) == 1
    tasks = f["spawner"].calls[0]
    assert [t["cwd"] for t in tasks] == [str(home / "work"), str(home / "work")]
    assert tasks[0]["task_id"].startswith("w1700") or tasks[0]["task_id"].startswith(
        "w"
    )
    assert tasks[0]["task_id"].startswith("w") and tasks[0]["task_id"].endswith("-deck")
    assert f["starter"].calls == 1


def test_judge_clear_on_first_answer_skips_follow_ups():
    result, f = _run(
        listen=Listener(["Tidy the downloads folder."]),
        judge=Judge([{"clear": True, "question": ""}]),
        planner=Planner([[{"task_id": "tidy", "brief": "Tidy downloads.", "cwd": ""}]]),
        report=lambda: QUIET_REPORT,
    )
    assert result["questions"] == 1
    assert len(f["judge"].seen) == 1
    assert result["status"] == "done"


# ------------------------------------------------------- interview controls


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("that's all", True),
        ("That's all, Jarvis.", True),
        ("Go.", True),
        ("go", True),
        ("go ahead", True),
        ("go ahead and book it", False),
        ("go to the bank", False),
        ("", False),
    ],
)
def test_is_stop_recognises_only_the_stop_phrases(text: str, expected: bool):
    assert ws.is_stop(text) is expected


def test_stop_phrase_ends_interview_without_judge_or_qa_entry():
    listen = Listener(["Build the deck.", "That's all, Jarvis."])
    judge = Judge([{"clear": False, "question": "Who is it for?"}])
    result, f = _run(listen=listen, judge=judge, report=lambda: QUIET_REPORT)
    assert result["questions"] == 2
    assert len(f["judge"].seen) == 1
    assert len(f["planner"].calls[0][0]) == 1  # only the real answer is kept


def test_go_as_first_answer_with_nothing_planned_is_nothing():
    result, f = _run(listen=Listener(["go"]), report=lambda: QUIET_REPORT)
    assert result["status"] == "nothing"
    assert f["spawner"].calls == []
    assert f["starter"].calls == 0
    assert "Nothing to start, Sir." in f["speak"].said


def test_question_cap_stops_at_max_questions():
    judge = Judge([{"clear": False, "question": "Anything else?"}] * 50)
    listen = Listener(["another detail"] * 50)
    result, f = _run(
        listen=listen,
        judge=judge,
        report=lambda: QUIET_REPORT,
    )
    assert result["questions"] == ws.MAX_QUESTIONS == 12
    assert len(f["judge"].seen) == ws.MAX_QUESTIONS - 1
    assert len(f["planner"].calls[0][0]) == ws.MAX_QUESTIONS


def test_silence_twice_ends_interview_cleanly():
    result, f = _run(listen=Listener([]), report=lambda: QUIET_REPORT)
    assert result["questions"] == 2
    assert ws.SORRY_LINE in f["speak"].said
    assert f["planner"].calls == []  # no meaningful answer: planner never runs
    assert result["status"] == "nothing"


# ------------------------------------------------------------- stuck paths


def test_mic_unusable_is_stuck_after_two_tries_and_spawns_nothing():
    listen = Listener([None, None, None])
    result, f = _run(listen=listen, report=lambda: QUIET_REPORT)
    assert result["status"] == "stuck"
    assert result["message"].startswith("STUCK:")
    assert f["listen"].calls == 2
    assert f["spawner"].calls == []
    assert f["speak"].said[-1].startswith("I am stuck, Sir.")


def test_refused_speech_is_stuck_and_never_listens():
    result, f = _run(speak=Speaker(ok=False))
    assert result["status"] == "stuck"
    assert result["message"].startswith("STUCK: speak the away summary")
    assert f["listen"].calls == 0


def test_planner_fails_once_then_succeeds_on_retry():
    planner = Planner(
        [
            RuntimeError("backend busy"),
            [{"task_id": "x", "brief": "Sort the inbox.", "cwd": ""}],
        ]
    )
    result, f = _run(planner=planner, report=lambda: QUIET_REPORT)
    assert result["status"] == "done"
    assert len(f["planner"].calls) == 2
    assert f["spawner"].calls[0][0]["brief"] == "Sort the inbox."


def test_planner_failing_twice_is_stuck_and_spawns_nothing():
    planner = Planner([RuntimeError("down"), RuntimeError("down")])
    result, f = _run(planner=planner, report=lambda: QUIET_REPORT)
    assert result["status"] == "stuck"
    assert "plan the tasks" in result["message"]
    assert f["spawner"].calls == []


def test_planner_returning_non_list_is_retried_then_stuck():
    planner = Planner([{"not": "a list"}, "still wrong"])
    result, f = _run(planner=planner, report=lambda: QUIET_REPORT)
    assert result["status"] == "stuck"
    assert len(f["planner"].calls) == 2
    assert f["spawner"].calls == []


def test_empty_plan_is_nothing_and_says_so():
    result, f = _run(planner=Planner([[]]), report=lambda: QUIET_REPORT)
    assert result["status"] == "nothing"
    assert f["spawner"].calls == []
    assert f["starter"].calls == 0
    assert "Nothing to start, Sir." in f["speak"].said


def test_spawner_failure_is_stuck_with_no_confirmation():
    planner = Planner([[{"task_id": "x", "brief": "Do it.", "cwd": ""}]])
    result, f = _run(
        planner=planner,
        spawner=Spawner(error=RuntimeError("tmux missing")),
        report=lambda: QUIET_REPORT,
    )
    assert result["status"] == "stuck"
    assert "tmux missing" in result["message"]
    assert not any("spawned, Sir." in text for text in f["speak"].said)


def test_starter_failure_is_stuck_after_the_spawn():
    planner = Planner([[{"task_id": "x", "brief": "Do it.", "cwd": ""}]])
    result, f = _run(
        planner=planner,
        starter=Starter(error=OSError("no watch")),
        report=lambda: QUIET_REPORT,
    )
    assert result["status"] == "stuck"
    assert "start the watch loop" in result["message"]
    assert len(f["spawner"].calls) == 1


def test_speech_that_raises_never_escapes_the_run():
    def boom(text: str) -> bool:
        raise RuntimeError("audio device gone")

    result, _ = _run(speak=boom)
    assert result["status"] == "stuck"
    assert result["message"].startswith("STUCK:")


def test_unexpected_error_becomes_stuck_not_a_raise():
    result, _ = _run(report=lambda: "not a dict")
    assert result["status"] == "stuck"
    assert "unexpected error" in result["message"]


def test_second_run_while_first_is_active_is_busy_and_silent():
    entered = threading.Event()
    release = threading.Event()
    calls = {"n": 0}

    def slow_listen():
        calls["n"] += 1
        if calls["n"] == 1:
            entered.set()
            release.wait(2)
        return ""

    results: dict = {}
    first = threading.Thread(
        target=lambda: results.update(first=_run(listen=slow_listen)[0]),
        daemon=True,
    )
    first.start()
    assert entered.wait(2)
    second, fakes = _run()
    release.set()
    first.join(5)

    assert second["status"] == "busy"
    assert fakes["speak"].said == []
    assert results["first"]["status"] in ("nothing", "done", "stuck")


# ------------------------------------------------------------- normalisation


def test_normalise_replaces_bad_ids_and_missing_cwds(home: Path):
    tasks = ws._normalise_tasks(
        [
            {
                "task_id": "bad id!!",
                "brief": "Tidy the inbox folder",
                "cwd": "/definitely/not/here",
            }
        ],
        now=NOW,
        default_cwd=home / "work",
    )
    assert len(tasks) == 1
    assert tasks[0]["task_id"].startswith("w1700000000-1-")
    assert "tidy-the-inbox-folder" in tasks[0]["task_id"]
    assert tasks[0]["cwd"] == str(home / "work")


def test_normalise_keeps_valid_ids_and_drops_junk(home: Path):
    tasks = ws._normalise_tasks(
        [
            "not a dict",
            {"task_id": "mail-sweep", "brief": "   "},
            {"task_id": "mail-sweep", "brief": "Sweep the mail."},
        ],
        now=NOW,
        default_cwd=home,
    )
    assert [t["task_id"] for t in tasks] == ["w1700000000-1-mail-sweep"]


def test_normalise_caps_the_batch_and_keeps_ids_unique(home: Path):
    raw = [{"task_id": "same", "brief": f"Job {i}"} for i in range(10)]
    tasks = ws._normalise_tasks(raw, now=NOW, default_cwd=home)
    assert len(tasks) == ws.MAX_TASKS == 8
    ids = [t["task_id"] for t in tasks]
    assert len(set(ids)) == len(ids)
    assert all(ws._TASK_ID_RE.match(i) for i in ids)


# ------------------------------------------------------------- default starter


def test_default_starter_starts_one_watch_loop_and_reuses_it(
    home: Path, monkeypatch: pytest.MonkeyPatch
):
    launched: list[tuple[list, dict]] = []

    class FakeProc:
        pid = 424242

    def fake_popen(argv, **kwargs):
        launched.append((argv, kwargs))
        return FakeProc()

    monkeypatch.setattr(ws.subprocess, "Popen", fake_popen)
    assert ws._default_starter() is True
    assert len(launched) == 1
    argv, kwargs = launched[0]
    assert argv == [sys.executable, "-m", "src.tiered_agents", "watch"]
    assert kwargs["start_new_session"] is True
    pidfile = home / "agents" / "watch.pid"
    assert pidfile.read_text(encoding="utf-8") == "424242"

    monkeypatch.setattr(ws, "_pid_is_watch", lambda pid: pid == 424242)
    assert ws._default_starter() is True
    assert len(launched) == 1


def test_sibling_import_falls_back_to_bare_name():
    assert ws._sib("away_log").__name__ in ("src.away_log", "away_log")
    with pytest.raises(ModuleNotFoundError):
        ws._sib("no_such_module_xyz")


def test_src_dir_is_on_sys_path():
    assert str(Path(ws.__file__).resolve().parent) in sys.path


def test_main_dry_run_prints_json_and_exits_zero(capsys):
    assert ws.main(["--dry-run"]) == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    import json

    assert json.loads(last)["status"] == "done"


# ------------------------------------------------------------- presence gate


def test_no_input_and_no_reply_skips_without_speaking_summary():
    result, f = _run(idle=lambda: 600.0, listen=Listener([]))
    assert result["status"] == "skipped"
    assert f["speak"].said == [ws.WELCOME_LINE]
    assert SUMMARY not in f["speak"].said
    assert f["spawner"].calls == [] if hasattr(f["spawner"], "calls") else True
    assert f["planner"].__dict__.get("calls", 0) in (0, [])


def test_unknown_idle_and_junk_reply_skips():
    result, f = _run(idle=lambda: None, listen=Listener(["you"]))
    assert result["status"] == "skipped"
    assert SUMMARY not in f["speak"].said


def test_spoken_reply_proves_presence():
    listen = Listener(["Yes, I am here.", "Build the deck.", "That is all."])
    judge = Judge([{"clear": True, "question": ""}])
    planner = Planner([[{"task_id": "deck", "brief": "Build the deck.", "cwd": ""}]])
    result, f = _run(
        idle=lambda: 600.0,
        listen=listen,
        judge=judge,
        planner=planner,
        report=lambda: QUIET_REPORT,
    )
    assert result["status"] == "done"
    assert f["speak"].said[0] == ws.WELCOME_LINE
    assert SUMMARY in f["speak"].said


def test_recent_input_proceeds_without_welcome_prompt():
    result, f = _run(
        idle=lambda: 3.0,
        listen=Listener(["Build the deck."]),
        judge=Judge([{"clear": True, "question": ""}]),
        planner=Planner([[{"task_id": "d", "brief": "Build the deck.", "cwd": ""}]]),
        report=lambda: QUIET_REPORT,
    )
    assert result["status"] == "done"
    assert ws.WELCOME_LINE not in f["speak"].said


# ------------------------------------------------------------- interview junk


def test_two_junk_answers_abort_and_spawn_nothing():
    listen = Listener(["thank you", "you", "ok", "ok", "ok", "ok"])
    judge = Judge()
    result, _f = _run(listen=listen, judge=judge, report=lambda: QUIET_REPORT)
    assert result["status"] == "nothing"
    assert result["questions"] == 2
    assert judge.seen == []
    assert result["spawned"] == 0


def test_repeated_noise_is_junk():
    for text in ["", "...", "no no no no", "turn it on turn it on", "Thank you."]:
        assert ws.is_junk(text), text
    assert not ws.is_junk("Check my email and summarise it.")


def test_real_answer_spawns_via_fake():
    result, _f = _run(
        listen=Listener(["Check my email and summarise it."]),
        judge=Judge([{"clear": True, "question": ""}]),
        planner=Planner([[{"task_id": "m", "brief": "Email.", "cwd": ""}]]),
        report=lambda: QUIET_REPORT,
    )
    assert result["status"] == "done"
    assert result["spawned"] == 1


def test_interview_wall_time_cap(monkeypatch):
    monkeypatch.setattr(ws, "INTERVIEW_MAX_S", -1.0)
    result, _f = _run(
        listen=Listener(["Build the deck."]),
        judge=Judge(),
        report=lambda: QUIET_REPORT,
    )
    assert result["status"] == "nothing"
    assert result["questions"] == 0
