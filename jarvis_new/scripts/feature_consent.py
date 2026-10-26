"""Owner-run consent writer. Does not capture/read source content or call services.

Example: python scripts/feature_consent.py /absolute/private/consent.json
 --screen-local-ocr --local-folder /absolute/approved/folder
 --read-content --disclose-to-model
Then configure JARVIS_FEATURE_CONSENT_FILE with that exact file for the next session.
The command is a host action, never a model-callable tool.
"""

import argparse
import json
import os
import time
import uuid
from pathlib import Path


def write_consent(
    target,
    *,
    screen=False,
    folder=None,
    drive_folder=None,
    account="personal",
    content=False,
    disclosure=False,
    seconds=3600,
):
    if not 60 <= seconds <= 10 * 365 * 86400:
        raise ValueError("Consent duration must be 60 seconds to ten years")
    target = Path(target)
    if not target.is_absolute() or not target.parent.is_dir() or target.is_symlink():
        raise ValueError(
            "Choose an absolute file in an existing private owner directory"
        )
    parent = target.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise ValueError(
            "Consent directory must already be private and owner-controlled"
        )
    grants = []
    if account not in {"personal", "school"} or (folder and drive_folder):
        raise ValueError("Choose one source and an existing personal or school account")
    if drive_folder:
        import re

        if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", drive_folder):
            raise ValueError("Approve one existing Drive folder ID")
        grants.append(
            {
                "grant_id": "drive-" + uuid.uuid4().hex,
                "source": "drive",
                "scope": drive_folder,
                "account": account,
                "content": bool(content),
                "model_disclosure": bool(disclosure),
            }
        )
    if folder:
        scope = Path(folder)
        if (
            not scope.is_absolute()
            or scope.is_symlink()
            or str(scope.resolve()) != str(scope)
            or not scope.is_dir()
        ):
            raise ValueError("Approve one canonical local directory")
        grants.append(
            {
                "grant_id": "local-" + uuid.uuid4().hex,
                "source": "local",
                "scope": str(scope),
                "account": account,
                "content": bool(content),
                "model_disclosure": bool(disclosure),
            }
        )
    data = {
        "version": 1,
        "screen_local_ocr": bool(screen),
        "expires_at": time.time() + seconds,
        "librarian_grants": grants,
    }
    # Exclusive creation avoids silently replacing a previously reviewed scope.
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        raw = (json.dumps(data, indent=2) + "\n").encode()
        offset = 0
        while offset < len(raw):
            offset += os.write(fd, raw[offset:])
    finally:
        os.close(fd)
    return data


def main():
    parser = argparse.ArgumentParser(
        description="Explicit expiring owner consent; no reads or captures"
    )
    parser.add_argument("output")
    parser.add_argument("--screen-local-ocr", action="store_true")
    parser.add_argument("--local-folder")
    parser.add_argument("--drive-folder")
    parser.add_argument("--account", choices=["personal", "school"], default="personal")
    parser.add_argument("--read-content", action="store_true")
    parser.add_argument("--disclose-to-model", action="store_true")
    parser.add_argument("--seconds", type=int, default=3600)
    args = parser.parse_args()
    write_consent(
        args.output,
        screen=args.screen_local_ocr,
        folder=args.local_folder,
        drive_folder=args.drive_folder,
        account=args.account,
        content=args.read_content,
        disclosure=args.disclose_to_model,
        seconds=args.seconds,
    )
    print(
        "Consent saved. Configure its exact path for the next owner session. No source was read or captured."
    )


if __name__ == "__main__":
    main()
