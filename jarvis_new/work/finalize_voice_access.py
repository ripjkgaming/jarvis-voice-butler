from pathlib import Path
p=Path('src/agent.py');s=p.read_text();s=s.replace('''        if session is not None:
            session.say(line, add_to_chat_ctx=False, allow_interruptions=True)''','''        if session is not None:
            activity = getattr(session, "_activity", None)
            if (isinstance(getattr(activity, "llm", None), llm.RealtimeModel)
                    and getattr(session, "tts", None) is None):
                # say() becomes generate_reply on this pipeline. Starting an
                # acknowledgement here races the tool's normal spoken reply.
                return
            session.say(line, add_to_chat_ctx=False, allow_interruptions=True)''');s=s.replace('''        caption("jarvis", line)
        return True''','''        activity = getattr(session, "_activity", None)
        if not isinstance(getattr(activity, "llm", None), llm.RealtimeModel):
            caption("jarvis", line)
        # Realtime say() uses generate_reply, whose conversation event already
        # captions the actual words. Do not append the same line a second time.
        return True''');p.write_text(s)
p=Path('src/system/core.py');s=p.read_text();s=s.replace('''        """List a local directory, including mounted drives with full access.

        Args:
            path: Directory path, absolute or ~/... .''','''        """List a local directory with exact paths and modification times.

        Args:
            path: Directory path, absolute, relative to home, or ~/... .
            sort_by: "name" or "newest" for latest-file requests.''');s=s.replace('''async def list_dir(self, context: RunContext, path: str = ".") -> dict[str, str]:''','''async def list_dir(self, context: RunContext, path: str = ".", sort_by: str = "name") -> dict:''');s=s.replace('''        rows = sorted(rp.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))[:40]
        bits =''','''        try:
            entries = [
                {"name": item.name, "path": str(item), "is_dir": item.is_dir(),
                 "modified_at": item.stat().st_mtime}
                for item in rp.iterdir()
            ]
        except OSError as exc:
            raise ToolError(f"Cannot list {rp}: {exc.strerror or str(exc)}") from exc
        if sort_by == "newest":
            entries.sort(key=lambda item: item["modified_at"], reverse=True)
        else:
            entries.sort(key=lambda item: (not item["is_dir"], item["name"].lower()))
        entries = entries[:40]
        rows = [Path(item["path"]) for item in entries]
        bits =''');s=s.replace('''return {"say": f"{rp.name or '/'} ({len(rows)} shown): {bits or 'empty'}."}''','''return {"path": str(rp), "entries": entries,
                "say": f"{rp.name or '/'} ({len(rows)} shown): {bits or 'empty'}."}''');s=s.replace('''        cmd = _normalize_command(command)
        if not cmd:
            raise ToolError("Confirm what, Sir? Give me the command first.")''','''        if full_command_access():
            return "Local commands already have standing owner authorization; run_command directly."
        cmd = _normalize_command(command)
        if not cmd:
            raise ToolError("Confirm what, Sir? Give me the command first.")''');s=s.replace('confined to /home/ripjk and /tmp','full filesystem with local access enabled; otherwise home/tmp');p.write_text(s)
p=Path('src/system/files_tools.py');s=p.read_text().replace('full_filesystem_access, launch_gui','full_filesystem_access, full_command_access, launch_gui');s=s.replace('''    def _consume_confirm(self, plan: dict) -> None:
''','''    def _consume_confirm(self, plan: dict) -> None:
        if full_command_access():
            self._confirmed_plan = None
            return
''');p.write_text(s)
p=Path('src/system/school_tools.py');s=p.read_text().replace('''    if school.is_school() and school.is_loud''','''    from system import full_command_access

    if full_command_access():
        return
    if school.is_school() and school.is_loud''');p.write_text(s)
