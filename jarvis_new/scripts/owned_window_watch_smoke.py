"""Manual owned-window capture/OCR smoke; never observe an arbitrary window.

Run only when computer access is authorized. Uses an ordinary closeable 640x300
Tk window and a local stub inference by default. --provider explicitly enables
up to three existing configured Gemini requests using only this synthetic window.
"""

import argparse
import asyncio
import os
import queue
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def owned_target(title):
    """Read-only exact-title candidate lookup; never KRunner Run/activation."""
    from jeepney import DBusAddress, new_method_call

    from system.computer_focus import open_dbus_connection
    from watch_native_capture import inspect_target

    connection = open_dbus_connection(
        bus=os.environ["DBUS_SESSION_BUS_ADDRESS"], auth_timeout=1
    )
    try:
        bus = DBusAddress(
            "/org/freedesktop/DBus", "org.freedesktop.DBus", "org.freedesktop.DBus"
        )
        owner = connection.send_and_get_reply(
            new_method_call(bus, "GetNameOwner", "s", ("org.kde.KWin",)), timeout=1
        ).body[0]
        result = connection.send_and_get_reply(
            new_method_call(
                DBusAddress("/WindowsRunner", owner, "org.kde.krunner1"),
                "Match",
                "s",
                (title,),
            ),
            timeout=1,
        )
        matches = [entry for entry in result.body[0] if entry[1] == title]
        if len(matches) != 1:
            raise RuntimeError("Owned synthetic target cannot be identified uniquely.")
        window = matches[0][0].split("_", 1)[1]
        metadata = connection.send_and_get_reply(
            new_method_call(
                DBusAddress("/KWin", owner, "org.kde.KWin"),
                "getWindowInfo",
                "s",
                (window,),
            ),
            timeout=1,
        ).body[0]
        # Native metadata map values are typed variants. Only this already
        # exact-title candidate is read, then fresh helper verifies process.
        pid = metadata["pid"][1]
        app = metadata["resourceClass"][1]
        if pid != os.getpid():
            raise RuntimeError("Candidate process is not the owned synthetic window.")
        fresh = inspect_target((window, app, pid))
        if fresh["pid"] != os.getpid() or fresh["title"] != title:
            raise RuntimeError("Owned synthetic target changed before enrollment.")
        return fresh["id"], fresh["app"], fresh["pid"]
    finally:
        connection.close()


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--run-owned-window", action="store_true", required=True)
    args.add_argument("--provider", action="store_true")
    args.add_argument("--native", action="store_true")
    args.add_argument("--provider-calls", type=int, choices=(1, 3), default=3)
    options = args.parse_args()
    # User's authorized computer window starts 17:40 Singapore, 13 Oct 2026.
    if time.time() < 1791884400:
        raise SystemExit("Authorized live-test time has not started.")
    import tkinter as tk

    if options.provider:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parents[1] / ".env.local")

    from watch_visual import VisualObserver, WatchConsent

    app = tk.Tk()
    title = "Jarvis owned synthetic watch test " + uuid.uuid4().hex[:12]
    app.title(title)
    app.geometry("640x300")
    label = tk.Label(
        app,
        text="JARVIS OWNED TEST\nBuild running\nThis is synthetic test content.",
        font=("Arial", 22),
    )
    label.pack(expand=True)
    updates = queue.Queue()
    outcome = []
    holder = []

    phases = [
        ("running", "Build running"),
        ("input_needed", "Input needed: confirm build"),
        ("completed", "Build finished successfully"),
    ]

    async def smoke():
        def stub(image, text, context, check):
            check()
            for state, quote in phases:
                if quote.casefold() in text.casefold():
                    return {
                        "task": "owned synthetic test",
                        "state": state,
                        "evidence": quote,
                        "summary": quote,
                        "confidence": 1,
                    }
            return {"state": "unknown"}

        observer = VisualObserver(
            WatchConsent(
                expires_at=time.time() + 120,
                max_calls=3,
                capture_policy="pinned_window"
                if options.native
                else "foreground_window",
            ),
            "owned-window-manual-test",
            authorized=lambda: True,
            infer=None if options.provider else stub,
            min_interval_s=0,
        )
        holder.append(observer)
        try:
            # Disclosed target is this process. Unknown or other foreground app
            # aborts before capture or upload. No activation/focus stealing.
            target = (
                await asyncio.to_thread(owned_target, title) if options.native else None
            )
            await observer.enroll(expected_target=target)
            identity = observer._identity(observer._selected)
            if (
                not isinstance(identity, tuple)
                or len(identity) != 3
                or identity[2] != os.getpid()
            ):
                raise RuntimeError(
                    "Owned test window is not foreground; no capture performed."
                )
            observed = []
            for index, (expected, quote) in enumerate(phases):
                if options.provider and index >= options.provider_calls:
                    observer.infer = stub
                updated = threading.Event()
                updates.put((quote, updated))
                if not await asyncio.to_thread(updated.wait, 3):
                    raise RuntimeError("Owned test UI timed out")
                await asyncio.sleep(0.4)  # allow the owned frame to paint
                result = await observer.observe(
                    context="Observe only the synthetic build task in this owned window."
                )
                observed.append(
                    {"expected": expected, "observed": (result or {}).get("state")}
                )
            outcome.append(
                {
                    "ok": all(p["expected"] == p["observed"] for p in observed),
                    "phases": observed,
                    "calls": observer.status()["calls"],
                    "provider": options.provider,
                    "native": options.native,
                    "provider_calls": options.provider_calls if options.provider else 0,
                }
            )
        finally:
            await observer.close()

    def worker():
        try:
            asyncio.run(asyncio.wait_for(smoke(), 95))
        except BaseException as error:
            from screen_understanding import ScreenError
            from system.computer_focus import ComputerFocusError

            safe_error = (
                str(error)
                if isinstance(error, (ScreenError, ComputerFocusError))
                else ""
            )
            outcome.append(
                {
                    "ok": False,
                    "error_type": type(error).__name__,
                    "safe_error": safe_error,
                    "cause_type": type(error.__cause__).__name__
                    if error.__cause__
                    else "",
                    "cause_frames": [
                        (Path(frame.filename).name, frame.lineno, frame.name)
                        for frame in __import__("traceback").extract_tb(
                            error.__cause__.__traceback__
                        )
                    ]
                    if error.__cause__
                    else [],
                }
            )

    def begin():
        threading.Thread(target=worker, daemon=True).start()
        app.after(100, poll)

    def poll():
        while not updates.empty():
            quote, updated = updates.get_nowait()
            label.configure(
                text=f"JARVIS OWNED TEST\n{quote}\nThis is synthetic test content."
            )
            updated.set()
        if outcome:
            print(outcome[-1], flush=True)
            app.destroy()
        else:
            app.after(100, poll)

    def close():
        for observer in holder:
            observer.revoke()
        app.destroy()

    app.protocol("WM_DELETE_WINDOW", close)
    app.after(1500, begin)
    app.after(100000, close)
    app.mainloop()
    return 0 if outcome and outcome[-1]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
