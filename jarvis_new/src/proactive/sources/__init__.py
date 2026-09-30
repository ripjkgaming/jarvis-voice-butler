"""Event sources for the proactive engine (IRONMAN_SPEC §1.1).

Each source is ``poll(now: float) -> list[Signal]``. Sources never speak;
they only report. Failures are the engine's to swallow.
"""

from __future__ import annotations


def default_sources() -> list:
    from proactive.sources import (
        calendar_source,
        deadline_source,
        jobs_source,
        mail_source,
        system_source,
    )

    return [
        calendar_source.poll,
        mail_source.poll,
        jobs_source.poll,
        system_source.poll,
        deadline_source.poll,
    ]
