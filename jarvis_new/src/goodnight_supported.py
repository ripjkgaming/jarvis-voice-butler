"""Explicit registered scope, never a claim about unregistered IDE/CLI work."""

from pathlib import Path

from goodnight_evidence import (
    _terminal,
    json_rows,
    named_build_exited,
    sql_rows,
)


class SupportedInventory:
    def __init__(self, root, fence, build_exited=named_build_exited):
        self.root, self.fence, self.build_exited = Path(root), fence, build_exited

    def clear(self):
        try:
            # An unused store may be absent: all supported creation/admission
            # paths acquire this fence. A present unsafe/broken store holds.
            for name, terminal in [
                ("tasks", {"done", "failed", "cancelled"}),
                ("jobs", {"done", "failed"}),
            ]:
                path = self.root / name
                if (path.exists() or path.is_symlink()) and not all(
                    _terminal(r, terminal, legacy=name == "jobs")
                    for r in json_rows(path)
                ):
                    return False
            path = self.root / "assigner/jobs.sqlite3"
            if (path.exists() or path.is_symlink()) and not all(
                _terminal(r, {"done", "failed", "blocked", "cancelled", "timed_out"})
                for r in sql_rows(path)
            ):
                return False
            path = self.root / "job_registry/jobs.sqlite3"
            if path.exists() or path.is_symlink():
                import json

                import jobs

                for raw in sql_rows(path, registry=True):
                    row = jobs._decode(json.dumps(raw))
                    if (
                        row["owner"] == "build-monitor"
                        and row["scope"] == "monitor_observation"
                    ):
                        if self.build_exited(row) is not True:
                            return False
                    elif (
                        row["owner"] == "agent-tasks" and row["scope"] == "adapter_work"
                    ):
                        if row["lifecycle"] != "settled" or row["outcome"] not in {
                            "succeeded",
                            "failed",
                            "cancelled",
                        }:
                            return False
                    else:
                        return None
            return self.fence.work_clear()
        except Exception:
            return None
