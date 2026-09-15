"""Test isolation for the persistent Brave profiles.

Every test gets throwaway profile dirs so the suite never touches the
real ~/.jarvis/brave-profile and never trips Chromium's single-process
profile lock when several managers launch in one test.
"""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_browser_profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_BROWSER_PROFILE", str(tmp_path / "brave-profile"))
    monkeypatch.setenv(
        "JARVIS_BROWSER_RESEARCH_PROFILE", str(tmp_path / "brave-research-profile")
    )
