"""Sir's own contact details, shared by every auto-reply.

Jarvis may put this number in replies to IMPORTANT messages (urgent email
acknowledgements, urgent WhatsApp messages) so people can reach him directly.
Override with JARVIS_OWNER_PHONE; set it to an empty string to stop sharing it.
"""

from __future__ import annotations

import os
import re

DEFAULT_PHONE = "+65 8753 4735"


def owner_phone() -> str:
    """The number to share ("" when sharing is switched off)."""
    if "JARVIS_OWNER_PHONE" in os.environ:
        return os.environ["JARVIS_OWNER_PHONE"].strip()
    return DEFAULT_PHONE


def digits(text: str) -> str:
    return re.sub(r"\D", "", str(text or ""))
