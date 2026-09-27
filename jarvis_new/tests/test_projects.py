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
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5"
    assert argv[argv.index("--tools") + 1] == "WebSearch,WebFetch"
    # Headless runs can't ask: web tools must be pre-approved too.
    assert argv[argv.index("--allowedTools") + 1] == "WebSearch,WebFetch"
    full = projects.get_project(meta["id"])
    assert [d["name"] for d in full["documents"]] == [
        "report.md",
        "sources.md",
        "brief.md",
    ]


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
    assert [c["action"] for c in cmds] == ["show", "select", "open_document", "scroll"]
    assert cmds[1]["project_id"] == PLIST[0]["id"]
    assert cmds[2]["index"] == 1
    assert cmds[3] == {"action": "scroll", "mode": "start", "speed": "slow"}


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
