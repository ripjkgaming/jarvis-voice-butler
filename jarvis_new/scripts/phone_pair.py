"""LOCKE//LINK pairing CLI (laptop side).

    uv run python scripts/phone_pair.py              # print a pairing QR + code
    uv run python scripts/phone_pair.py --list       # paired devices
    uv run python scripts/phone_pair.py --revoke ID  # revoke one device

The code is single use, valid for 10 minutes, and only its sha256 is stored
(~/.jarvis/phone/pair.json, 0600). The phone opens
``https://<JARVIS_PHONE_ORIGIN>/#pair=<code>`` (the fragment never reaches a
server log) or the code is typed by hand on the GATE screen.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import phone_device

DEFAULT_ORIGIN = "https://ripjk.tailea4e7f.ts.net"


def pair_url(origin: str, code: str) -> str:
    return f"{origin.rstrip('/')}/#pair={code}"


def _ago(ts) -> str:
    if not isinstance(ts, (int, float)):
        return "--"
    s = max(0, int(time.time() - ts))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size:
            return f"{s // size}{unit} ago"
    return f"{s}s ago"


def main(argv=None, out=sys.stdout) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--list", action="store_true", help="list paired devices")
    ap.add_argument("--revoke", metavar="DEVICE_ID", help="revoke a paired device")
    ap.add_argument("--no-qr", action="store_true", help="print the code and URL only")
    args = ap.parse_args(argv)
    store = phone_device.DeviceStore()
    if args.list:
        devices = store.list()
        if not devices:
            print("no paired devices", file=out)
        for d in devices:
            print(
                f"{d['id']}  {d.get('name', ''):<20} passkeys={len(d.get('credentials', []))}"
                f"  paired {_ago(d.get('created'))}  seen {_ago(d.get('last_seen'))}",
                file=out,
            )
        return 0
    if args.revoke:
        if store.revoke(args.revoke):
            print(f"revoked {args.revoke}", file=out)
            return 0
        print(f"no such device {args.revoke}", file=sys.stderr)
        return 1
    origin = phone_device.phone_origin() or DEFAULT_ORIGIN
    try:
        code = store.issue_code()
    except phone_device.PairError as exc:
        print(f"refused: {exc}; wait for one to expire (10 min)", file=sys.stderr)
        return 1
    url = pair_url(origin, code)
    if not args.no_qr:
        import segno

        segno.make(url, error="m").terminal(out=out, compact=True)
    print(f"\n  code  {code[:5]}-{code[5:]}   (valid 10 min, single use)", file=out)
    print(f"  url   {url}\n", file=out)
    if not phone_device.phone_origin():
        print(
            "  note  JARVIS_PHONE_ORIGIN is not set in this shell; the bridge"
            " refuses pairing until it is set in its environment.",
            file=out,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
