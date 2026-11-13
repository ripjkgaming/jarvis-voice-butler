"""Real pactl create/teardown with uniquely named test sinks only."""

import shutil
import uuid

import pytest

from calls.audio_route import AudioRoute

pytestmark = pytest.mark.skipif(not shutil.which("pactl"), reason="no pactl")


def test_real_create_teardown():
    r = AudioRoute.unique(uuid.uuid4().hex[:6])
    try:
        if not r.setup():
            pytest.skip("pactl server unavailable")
        assert r.health()["ok"]
        assert r.setup()  # idempotent
    finally:
        assert r.teardown()
    assert not r.health()["ok"]
    assert r.health()["missing"]
