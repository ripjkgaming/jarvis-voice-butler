from pathlib import Path
p=Path('src/system/__init__.py');s=p.read_text();pos=s.index('\n\nasync def run_cmd(');s=s[:pos]+'''

_GUI_REAPERS: set[asyncio.Task] = set()


async def launch_gui(*argv: str) -> tuple[int, str, str]:
    """Request a detached desktop launch. Never kill a GUI for staying open."""
    import phone_session
    from system.launcher import require_launch_allowed

    require_local()
    if phone_session.guest_required():
        raise LocalSystemError("Guest mode refuses owner actions.")
    phone_session.check_approval_effect()
    require_launch_allowed(argv)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
        )
    except OSError as exc:
        return 127, "", str(exc)[:200]
    # Reap when the app eventually exits, without tying its lifetime to this call.
    reaper = asyncio.create_task(proc.wait())
    _GUI_REAPERS.add(reaper)
    reaper.add_done_callback(_GUI_REAPERS.discard)
    return 0, "", ""
'''+s[pos:];p.write_text(s)
p=Path('src/system/core.py');s=p.read_text().replace('full_filesystem_access, full_command_access)','full_filesystem_access, full_command_access, launch_gui)');needle='''        rc, out, err = await run_cmd(*argv, timeout=float(timeout))
        log_action("cmd",''';replacement='''        # GUI processes intentionally remain alive. The ordinary command timeout
        # used to kill VLC after a successful launch, then invite another launch.
        try:
            gui_argv = shlex.split(cmd)
        except ValueError:
            gui_argv = []
        if (gui_argv and Path(gui_argv[0]).name in {
            "vlc", "mpv", "haruna", "celluloid", "xdg-open", "dolphin",
            "okular", "gwenview", "kate", "konsole",
        } and not any(token in cmd for token in _SHELL_TOKENS)):
            rc, _, err = await launch_gui(*gui_argv)
            log_action("cmd-launch", f"{gui_argv[0]} rc={rc}")
            return {"say": "Launch requested, Sir." if rc == 0 else f"Launch failed: {err[:200]}",
                    "rc": str(rc), "launch_requested": rc == 0}
        rc, out, err = await run_cmd(*argv, timeout=float(timeout))
        log_action("cmd",''';assert needle in s;s=s.replace(needle,replacement);p.write_text(s)
p=Path('src/system/files_tools.py');s=p.read_text().replace('run_cmd, full_filesystem_access','run_cmd, full_filesystem_access, launch_gui');s=s.replace('await run_cmd(*argv, timeout=15.0)','await launch_gui(*argv)').replace('await run_cmd("xdg-open", str(rp), timeout=15.0)','await launch_gui("xdg-open", str(rp))');p.write_text(s)
p=Path('tests/test_files_tools.py');s=p.read_text();start=s.index('async def test_open_path_result_index');end=s.index('async def test_describe_folder',start);chunk=s[start:end].replace('monkeypatch.setattr(files_tools, "run_cmd", fake)','monkeypatch.setattr(files_tools, "launch_gui", fake)');s=s[:start]+chunk+s[end:];p.write_text(s)
p=Path('src/hud_events.py');s=p.read_text();pos=s.index('\n\ndef intent_echo_payload');s=s[:pos]+'''

def tool_execution_results(event) -> list[tuple[str, bool]]:
    """Read errors from paired SDK outputs, including failed command exit codes."""
    from claim_guard import sdk_tool_result
    from action_outcome import parse_outcome

    results = []
    for call, output in event.zipped():
        name = str(getattr(call, "name", "unknown"))
        ok = output is not None and not bool(getattr(output, "is_error", False))
        data = sdk_tool_result(output)
        if isinstance(data, dict):
            ok = ok and data.get("ok") is not False and not data.get("error")
            if "rc" in data:
                ok = ok and str(data["rc"]) == "0"
            receipt = parse_outcome(data)
            if receipt is not None:
                ok = ok and receipt.accepted
        results.append((name, bool(ok)))
    return results
'''+s[pos:];p.write_text(s)
p=Path('src/agent.py');s=p.read_text();start=s.index('                calls = (',s.index('        def _on_hud_tools'));end=s.index('                    mirror_to_log(str(name)',start);s=s[:start]+'''                from hud_events import tool_execution_results

                for name, ok in tool_execution_results(executed):
'''+s[end:];# A finished continuation still counts as having answered; don't nudge it twice.
s=s.replace('''            def _nudge(sess=self) -> None:
                gen = sess._current_generation
                if gen is None or gen._done:
                    orig_send(sess, _gt.LiveClientContent(turns=[], turn_complete=True))''','''            previous = self._current_generation

            def _nudge(sess=self, before=previous) -> None:
                gen = sess._current_generation
                if gen is before and (gen is None or gen._done):
                    orig_send(sess, _gt.LiveClientContent(turns=[], turn_complete=True))''');p.write_text(s)
p=Path('tests/conftest.py');s=p.read_text();s+='''

@pytest.fixture(autouse=True)
def _isolated_local_access_preferences(monkeypatch):
    """Keep legacy restricted-mode tests independent of the owner's preferences."""
    monkeypatch.setenv("JARVIS_FULL_FILESYSTEM", "0")
    monkeypatch.setenv("JARVIS_COMMAND_ACCESS", "0")
''';p.write_text(s)
