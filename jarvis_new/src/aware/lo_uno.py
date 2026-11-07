"""LibreOffice bridge for worksheet completion. Runs under the SYSTEM python
(the one with ``uno``), never the venv: ``aware/worksheet.py`` starts it as a
subprocess with one JSON request on stdin and reads one JSON reply on stdout.

Requests::

    {"op": "docs"}                              -> {"docs": [{url, title}]}
    {"op": "open", "path": abs}                 -> {"url": str}
    {"op": "slots", "url": str}                 -> {"kind", "slots": [...]}
    {"op": "apply", "url": str, "edits": [...]} -> {"applied": n, "before": {...}}

A slot is one Writer paragraph, one Writer table cell or one Calc cell:
``{"id", "text", "where"}``. ``apply`` writes every edit inside one undo
context, so a single Ctrl+Z in LibreOffice takes all of Jarvis's answers back.
Nothing is ever saved to disk here; Sir reviews and saves.

The connection uses a named pipe (per-user socket, never TCP). When nothing
listens, ``soffice --accept=...`` asks the running LibreOffice to start the
acceptor (or starts LibreOffice when none runs).
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time

MAX_SLOTS = 400
MAX_TEXT = 300
CALC_MAX_ROWS = 80
CALC_MAX_COLS = 20


def pipe_name() -> str:
    return os.environ.get("JARVIS_LO_PIPE", "").strip() or f"jarvis_aware_{os.getuid()}"


def soffice_argv() -> list[str]:
    argv = ["soffice", f"--accept=pipe,name={pipe_name()};urp;", "--norestore"]
    profile = os.environ.get("JARVIS_LO_PROFILE", "").strip()
    if profile:
        argv.append(f"-env:UserInstallation=file://{profile}")
    if os.environ.get("JARVIS_LO_HEADLESS"):
        argv.append("--headless")
    return argv


def connect(start: bool = True, wait_s: float = 15.0):
    import uno

    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local
    )
    url = f"uno:pipe,name={pipe_name()};urp;StarOffice.ComponentContext"
    try:
        ctx = resolver.resolve(url)
    except Exception:
        if not start:
            raise
        subprocess.Popen(
            soffice_argv(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.time() + wait_s
        while True:
            time.sleep(0.5)
            try:
                ctx = resolver.resolve(url)
                break
            except Exception:
                if time.time() > deadline:
                    raise
    return ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.frame.Desktop", ctx
    )


def _docs(desktop) -> list:
    out = []
    comps = desktop.getComponents().createEnumeration()
    while comps.hasMoreElements():
        doc = comps.nextElement()
        if not hasattr(doc, "getURL"):
            continue
        title = ""
        with contextlib.suppress(Exception):
            title = doc.getCurrentController().getFrame().getTitle()
        out.append((doc, doc.getURL(), title))
    return out


def find_doc(desktop, url: str):
    for doc, u, _ in _docs(desktop):
        if u == url:
            return doc
    raise LookupError("document not open")


def _clip(text: str) -> str:
    text = text or ""
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "..."


def _col_name(i: int) -> str:
    name = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        name = chr(65 + r) + name
    return name


def writer_slots(doc) -> list[dict]:
    slots = []
    tables = doc.getTextTables()
    for t in range(tables.getCount()):
        table = tables.getByIndex(t)
        for cell in table.getCellNames():
            slots.append(
                {
                    "id": f"T{t}:{cell}",
                    "text": _clip(table.getCellByName(cell).getString()),
                    "where": f"table {t + 1}, cell {cell}",
                }
            )
    paras = doc.getText().createEnumeration()
    n = 0
    while paras.hasMoreElements():
        para = paras.nextElement()
        if para.supportsService("com.sun.star.text.TextTable"):
            continue
        slots.append(
            {"id": f"P{n}", "text": _clip(para.getString()), "where": "paragraph"}
        )
        n += 1
    return slots[:MAX_SLOTS]


def _paragraph(doc, idx: int):
    paras = doc.getText().createEnumeration()
    n = 0
    while paras.hasMoreElements():
        para = paras.nextElement()
        if para.supportsService("com.sun.star.text.TextTable"):
            continue
        if n == idx:
            return para
        n += 1
    raise LookupError(f"P{idx}")


def calc_slots(doc) -> list[dict]:
    slots = []
    sheets = doc.getSheets()
    for s in range(sheets.getCount()):
        sheet = sheets.getByIndex(s)
        cur = sheet.createCursor()
        cur.gotoEndOfUsedArea(False)
        addr = cur.getRangeAddress()
        rows = min(addr.EndRow + 1, CALC_MAX_ROWS)
        cols = min(addr.EndColumn + 2, CALC_MAX_COLS)  # +1: an empty answer column
        for r in range(rows):
            for c in range(cols):
                cell = sheet.getCellByPosition(c, r)
                slots.append(
                    {
                        "id": f"S{s}!{_col_name(c)}{r + 1}",
                        "text": _clip(cell.getString()),
                        "where": f"sheet {sheet.getName()}",
                    }
                )
    return slots[:MAX_SLOTS]


def _calc_cell(doc, sid: str):
    sheet_part, ref = sid[1:].split("!", 1)
    sheet = doc.getSheets().getByIndex(int(sheet_part))
    return sheet.getCellRangeByName(ref)


def is_calc(doc) -> bool:
    return doc.supportsService("com.sun.star.sheet.SpreadsheetDocument")


def slot_target(doc, sid: str):
    if sid.startswith("T"):
        t, cell = sid[1:].split(":", 1)
        return doc.getTextTables().getByIndex(int(t)).getCellByName(cell)
    if sid.startswith("P"):
        return _paragraph(doc, int(sid[1:]))
    if sid.startswith("S"):
        return _calc_cell(doc, sid)
    raise LookupError(sid)


def apply(doc, edits: list[dict]) -> dict:
    before, applied = {}, 0
    undo = doc.getUndoManager()
    undo.enterUndoContext("Jarvis worksheet answers")
    try:
        for e in edits:
            target = slot_target(doc, str(e["id"]))
            if target is None:
                continue
            before[e["id"]] = target.getString()
            target.setString(str(e["text"]))
            applied += 1
    finally:
        undo.leaveUndoContext()
    return {"applied": applied, "before": before}


def handle(req: dict) -> dict:
    op = req.get("op")
    desktop = connect(start=op != "docs" or bool(req.get("start")))
    if op == "docs":
        return {"docs": [{"url": u, "title": t} for _, u, t in _docs(desktop) if u]}
    if op == "open":
        import uno

        url = uno.systemPathToFileUrl(os.path.abspath(req["path"]))
        for _, u, _ in _docs(desktop):
            if u == url:
                return {"url": url}
        doc = desktop.loadComponentFromURL(url, "_blank", 0, ())
        if doc is None:  # e.g. locked by another LibreOffice; no dialog headless
            raise RuntimeError("load failed")
        return {"url": url}
    doc = find_doc(desktop, req["url"])
    if op == "slots":
        calc = is_calc(doc)
        return {
            "kind": "calc" if calc else "writer",
            "slots": calc_slots(doc) if calc else writer_slots(doc),
        }
    if op == "apply":
        return apply(doc, list(req.get("edits") or []))
    raise ValueError(f"unknown op {op!r}")


def main() -> int:
    try:
        req = json.loads(sys.stdin.read() or "{}")
        out = {"ok": True, **handle(req)}
    except Exception as exc:
        out = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
