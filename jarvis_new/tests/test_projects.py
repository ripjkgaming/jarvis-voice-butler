"""Background projects (research on Claude, coding on opencode) and the
voice-only Project Archive grammar + command bus."""

import time

import pytest

import projects
from projects import UiBus, parse_voice


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jh"))
    monkeypatch.setenv("JARVIS_CLAUDE", "1")  # runners are always stubbed here
    monkeypatch.setattr(projects, "BUS", UiBus())
    monkeypatch.setattr(projects, "_announce", lambda meta: None)
    # ResearchProgress pops a persistent (-t 0) toast; a real one from a
    # test is never replaced and sat on Sir's desktop at 3% for good.
    real_run = projects.subprocess.run

    def no_notify(argv, *a, **kw):
        if argv and argv[0] == "notify-send":
            return _Proc()
        return real_run(argv, *a, **kw)

    monkeypatch.setattr(projects.subprocess, "run", no_notify)


class _Proc:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


REPORT = """## Summary
Solid-state cells are close. Pilot lines run in 2027.

## Findings
- Energy density up.

## Sources
- https://example.com/a - A paper
- https://example.org/b.
"""


def _research(topic="solid state batteries", out=REPORT, rc=0):
    runner = lambda argv, **kw: _Proc(rc, out)  # noqa: E731
    return projects.start_research(topic, runner=runner, background=False)


def test_research_project_writes_documents_and_parses(monkeypatch):
    monkeypatch.setattr("claude_cli.shutil.which", lambda n: f"/usr/bin/{n}")
    seen = {}

    def runner(argv, **kw):
        seen["argv"] = argv
        return _Proc(0, REPORT)

    meta = projects.start_research(
        "solid state batteries", runner=runner, background=False
    )
    assert meta["status"] == "done"
    assert meta["summary"].startswith("Solid-state cells are close.")
    assert meta["sources"] == ["https://example.com/a", "https://example.org/b"]
    argv = seen["argv"]
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5-5"
    assert argv[argv.index("--tools") + 1] == "WebSearch,WebFetch,Bash"
    # Headless runs can't ask: Bash is pre-approved only for the pdf reader.
    assert argv[argv.index("--allowedTools") + 1] == (
        f"WebSearch,WebFetch,Bash({projects.pdf_command()}:*)"
    )
    full = projects.get_project(meta["id"])
    assert [d["name"] for d in full["documents"]] == [
        "report.md",
        "sources.md",
        "brief.md",
    ]


def test_research_pdf_tooling():
    import sys
    from pathlib import Path

    # The exact command the model is told to run is the one pre-approved.
    cmd = projects.pdf_command()
    assert cmd == f"{sys.executable} {Path(projects.__file__).resolve().parent / 'pdf_fetch.py'}"
    assert cmd.endswith("src/pdf_fetch.py")
    assert projects.pdf_command() in projects.RESEARCH_SYSTEM
    assert "Never use Bash for anything else" in projects.RESEARCH_SYSTEM
    assert ".pdf" in projects.RESEARCH_SYSTEM


def test_research_stream_path_scopes_bash(monkeypatch):
    monkeypatch.setattr("claude_cli.shutil.which", lambda n: f"/usr/bin/{n}")
    seen = {}

    def fake_stream(prompt, **kw):
        seen.update(kw)
        return REPORT, None

    monkeypatch.setattr("claude_cli.claude_stream", fake_stream)
    meta = projects.start_research("solid state batteries", background=False)
    assert meta["status"] == "done"
    assert seen["tools"] == "WebSearch,WebFetch,Bash"
    assert seen["allowed"] == f"WebSearch,WebFetch,Bash({projects.pdf_command()}:*)"


def test_research_pdf_progress(monkeypatch):
    from projects import research_stage

    assert research_stage(
        {"kind": "pdf", "detail": "https://www.arxiv.org/pdf/1706.03762"}
    ) == "Reading PDF: arxiv.org"
    monkeypatch.setattr(projects.subprocess, "run", lambda argv, **kw: None)
    meta = projects._new_meta("research", "sober", "sober", "m")
    prog = projects.ResearchProgress(meta, notify=False, min_gap_s=0.0)
    prog.on_event({"kind": "pdf", "detail": "https://arxiv.org/pdf/1706.03762"})
    assert prog.steps == 1 and prog.stage == "Reading PDF: arxiv.org"
    got = projects._read_meta(meta["id"])
    assert got["steps"] == 1 and got["stage"] == "Reading PDF: arxiv.org"


def test_research_failure_is_recorded(monkeypatch):
    monkeypatch.setattr("claude_cli.shutil.which", lambda n: f"/usr/bin/{n}")
    meta = _research(out="", rc=1)
    assert meta["status"] == "failed" and meta["error"]


def test_choose_variant():
    assert projects.choose_variant("make a script that prints the date") == "high"
    assert projects.choose_variant("refactor the auth module") == "max"
    assert projects.choose_variant("x" * 300) == "max"


def test_code_argv_and_directory_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(projects.Path, "home", classmethod(lambda cls: tmp_path))
    argv = projects.code_argv("do it", tmp_path / "p", "high")
    assert argv[:6] == [
        "opencode",
        "run",
        "--auto",
        "-m",
        projects.OPENCODE_MODEL,
        "--variant",
    ]
    assert argv[-1] == "do it"
    path, err = projects.resolve_directory("", "My Tool")
    assert err is None and path.parent == tmp_path / "jarvis-projects"
    assert projects.resolve_directory(str(tmp_path), "x")[1]  # $HOME itself refused
    assert projects.resolve_directory("/etc", "x")[1]  # outside home refused
    ok_dir = tmp_path / "code" / "app"
    ok_dir.mkdir(parents=True)
    assert projects.resolve_directory(str(ok_dir), "x") == (ok_dir.resolve(), None)


def test_code_project_runs_and_summarizes(tmp_path, monkeypatch):
    monkeypatch.setattr(projects.Path, "home", classmethod(lambda cls: tmp_path))
    launched = {}

    class FakePopen:
        pid = 4242

        def __init__(self, argv, stdout=None, **kw):
            launched["argv"] = argv
            stdout.write(b"\x1b[32mwrote hello.py\x1b[0m\nall done\n")
            stdout.flush()

        def wait(self, timeout=None):
            return 0

    meta = projects.start_code("make hello world", popen=FakePopen, background=False)
    assert meta["status"] == "done" and meta["variant"] == "high"
    assert "all done" in meta["summary"] and "\x1b" not in meta["summary"]
    assert launched["argv"][launched["argv"].index("--dir") + 1] == meta["directory"]


def test_code_project_bad_directory_fails_fast():
    meta = projects.start_code(
        "x task", directory="/definitely/not/here", background=False
    )
    assert meta["status"] == "failed" and "directory" in meta["error"]


def test_list_newest_first_and_ids_are_safe(monkeypatch):
    monkeypatch.setattr("claude_cli.shutil.which", lambda n: f"/usr/bin/{n}")
    a = _research("alpha topic")
    time.sleep(1.1)
    b = _research("beta topic")
    assert [p["id"] for p in projects.list_projects()] == [b["id"], a["id"]]
    assert projects.get_project("../etc") is None
    assert projects.cancel_project("../../x") is False


# --- voice grammar ---------------------------------------------------------

PLIST = [
    {
        "id": "20260927-200000-solid-state-batteries",
        "title": "Solid-state batteries timeline",
        "kind": "research",
    },
    {
        "id": "20260927-190000-weather-widget",
        "title": "Build a weather widget",
        "kind": "code",
    },
]


def test_owners_example_chain():
    cmds = parse_voice(
        "hey jarvis open research projects, navigate to the solid state batteries "
        "project and open the first document and start scrolling through them slowly",
        PLIST,
    )
    # Wakeword is stripped upstream by the bridge; the parser sees the rest.
    assert cmds is None or cmds[0]["action"] == "show"
    cmds = parse_voice(
        "open research projects, navigate to the solid state batteries project "
        "and open the first document and start scrolling through them slowly",
        PLIST,
    )
    assert [c["action"] for c in cmds] == ["show", "filter", "select", "open_document", "scroll"]
    assert cmds[1] == {"action": "filter", "filter": "research"}
    assert cmds[2]["project_id"] == PLIST[0]["id"]
    assert cmds[3]["index"] == 1
    assert cmds[4] == {"action": "scroll", "mode": "start", "speed": "slow"}


@pytest.mark.parametrize(
    ("text", "action", "extra"),
    [
        ("open my projects", "show", {}),
        ("close research projects", "hide", {}),
        ("show code projects", "filter", {"filter": "code"}),
        ("show everything", "filter", {"filter": "all"}),
        ("open project two", "select", {"index": 2}),
        ("open the third project", "select", {"index": 3}),
        ("open the second document", "open_document", {"index": 2}),
        ("open the 3rd document", "open_document", {"index": 3}),
        ("open the last document", "open_document", {"index": -1}),
        ("abort this project", "abort", {}),
    ],
)
def test_single_commands(text, action, extra):
    cmds = parse_voice(text, PLIST)
    assert cmds and cmds[0]["action"] == action
    for k, v in extra.items():
        assert cmds[0][k] == v


def test_search_and_site_opens_never_become_projects():
    # Research must never swallow a web search or a website open.
    for text in (
        "search for solid state batteries",
        "google the weather",
        "open youtube",
        "open brave",
        "research solid state batteries",  # agent tool, not a UI command
        "go to the kitchen",
        "what's the weather",
    ):
        assert parse_voice(text, PLIST) is None, text


def test_bare_navigation_only_while_archive_active():
    assert parse_voice("stop scrolling", PLIST) is None
    assert parse_voice("go back", PLIST) is None
    projects.BUS.push("select", project_id=PLIST[0]["id"])
    assert parse_voice("stop scrolling", PLIST) == [
        {"action": "scroll", "mode": "stop"}
    ]
    assert parse_voice("faster", PLIST) == [{"action": "scroll", "mode": "faster"}]
    assert parse_voice("go to the top", PLIST) == [{"action": "scroll", "mode": "top"}]


def test_unknown_project_in_a_chain_is_reported():
    cmds = parse_voice(
        "open research projects and navigate to the moon base project", PLIST
    )
    assert cmds[-1]["action"] == "missing"
    assert "can't find" in projects.reply_for(cmds)


def test_bus_since_and_execute(monkeypatch, tmp_path):
    verbs = []
    monkeypatch.setattr(
        projects, "shell_verb", lambda v, run=None: verbs.append(v) or True
    )
    monkeypatch.setattr(projects, "list_projects", lambda: PLIST)
    cursor = projects.BUS.since(None)["seq"]
    out = projects.execute_voice(
        [
            {"action": "show"},
            {"action": "select", "index": 2},
            {"action": "open_document", "index": 1},
        ],
        heard="open projects, open project two, open the first document",
    )
    assert out["ok"] and verbs == ["projectsshow"]
    cmds = projects.BUS.since(cursor)["commands"]
    assert [c["action"] for c in cmds] == ["show", "select", "open_document"]
    assert cmds[0]["heard"].startswith("open projects")
    assert "heard" not in cmds[1]
    assert cmds[1]["project_id"] == PLIST[1]["id"]
    assert projects.BUS.selected_id == PLIST[1]["id"]


def test_polite_and_misheard_project_commands():
    plist = [{"id": "a", "title": "Roblox Sober", "kind": "research"}]
    two = [{"action": "select", "index": 2}]
    assert parse_voice("Can you do project two?", plist) == two
    assert parse_voice("jarvis open project 2 please", plist) == two
    assert parse_voice("can you open the second project", plist) == two
    assert parse_voice("JAFIS, can you open research projects?", plist) == [
        {"action": "show"},
        {"action": "filter", "filter": "research"},
    ]
    assert parse_voice("Can you", plist) is None
    assert parse_voice("what is the capital of australia", plist) is None


def test_research_progress_curve():
    from projects import research_progress, research_stage

    assert research_progress(0, False) == 3
    seq = [research_progress(n, False) for n in range(0, 30)]
    assert seq == sorted(seq) and max(seq) <= 80
    assert research_progress(3, True) == 88
    assert research_progress(50, True) <= 97
    assert research_progress(2, False, done=True) == 100
    assert research_stage({"kind": "fetch", "detail": "https://www.github.com/a"}) == "Reading: github.com"
    assert research_stage({"kind": "search", "detail": "sober"}) == "Searching: sober"


def test_research_progress_writes_meta_and_notifies(monkeypatch):
    calls = []

    class _Out:
        stdout = "4242\n"

    monkeypatch.setattr(projects.subprocess, "run", lambda argv, **kw: calls.append(argv) or _Out())
    meta = projects._new_meta("research", "sober", "sober", "m")
    prog = projects.ResearchProgress(meta, min_gap_s=0.0)
    prog.on_event({"kind": "search", "detail": "sober linux"})
    prog.on_event({"kind": "fetch", "detail": "https://sober.vinegarhq.org/x"})
    got = projects._read_meta(meta["id"])
    assert got["steps"] == 2 and got["progress"] > 3 and got["stage"].startswith("Reading")
    assert got["notify_id"] == "4242"
    assert "-r" in calls[-1] and any(a.startswith("int:value:") for a in calls[-1])


def test_voice_delete_needs_confirmation(monkeypatch):
    monkeypatch.setattr(projects, "shell_verb", lambda verb, run=None: True)
    meta = projects._new_meta("research", "roblox sober", "x", "m")
    other = projects._new_meta("research", "batteries", "y", "m")
    plist = projects.list_projects()
    # "confirm" alone means nothing until a delete is waiting.
    assert parse_voice("confirm delete", plist) is None
    cmds = parse_voice("delete the roblox sober project", plist)
    assert cmds == [{"action": "delete", "project_id": meta["id"], "title": "roblox sober"}]
    assert "confirm delete" in projects.reply_for(cmds)
    assert projects.execute_voice(cmds)["ok"]
    assert projects._read_meta(meta["id"]) is not None  # not yet
    confirm = parse_voice("confirm delete", plist)
    assert projects.execute_voice(confirm)["ok"]
    assert projects._read_meta(meta["id"]) is None
    assert projects._read_meta(other["id"]) is not None
    actions = [c["action"] for c in projects.BUS.since(0)["commands"]]
    assert "delete_pending" in actions and actions[-1] == "deleted"
    # Expired confirmation does nothing.
    projects.execute_voice(parse_voice("delete the batteries project", projects.list_projects()))
    projects.BUS.pending_delete["at"] -= projects.DELETE_CONFIRM_S + 1
    assert parse_voice("confirm delete", plist) is None
    assert projects._read_meta(other["id"]) is not None


def test_text_bar():
    assert projects.text_bar(0) == "▱" * 16
    assert projects.text_bar(100) == "▰" * 16
    assert projects.text_bar(50).count("▰") == 8
