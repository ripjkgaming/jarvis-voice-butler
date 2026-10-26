"""Explicit owner-requested local report. No capture, inference or remediation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from health_recorder_client import BridgeClient


def main(argv=None, *, client=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-s", type=int, default=300)
    parser.add_argument("--clear", action="store_true")
    args = parser.parse_args(argv)
    if not 30 <= args.window_s <= 1800:
        parser.error("window must be 30-1800 seconds")
    if (
        client is None
        and args.clear
        and os.environ.get("JARVIS_HEALTH_RECORDER") != "1"
    ):
        print(json.dumps({"ok": False, "error": "health recording disabled"}))
        return 1
    try:
        client = client or BridgeClient()
        result = (
            client.request("/health-recorder/clear", {})
            if args.clear
            else client.request(f"/health-recorder/incident?window_s={args.window_s}")
        )
        if result.get("ok") is not True:
            raise RuntimeError("unavailable")
        print(json.dumps(result, allow_nan=False))
        return 0
    except Exception:
        print(json.dumps({"ok": False, "error": "health recorder unavailable"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
