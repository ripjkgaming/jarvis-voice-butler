from types import SimpleNamespace

import pytest
from PIL import Image
from screen_understanding import ForegroundBackend, ScreenError


@pytest.mark.asyncio
@pytest.mark.parametrize('overlay,blocked', [
    ((30, 20, 5, 5), False),
    ((20, 10, 5, 5), False),
    ((0, 0, 5, 5), False),
    ((0, 10, 10, 10), False),
    ((10, 0, 10, 10), False),
    ((19, 10, 5, 5), True),
    ((10, 19, 5, 5), True),
    ((0, 0, 30, 30), True),
    ((11, 11, 1, 1), True),
])
async def test_real_backend_keeps_only_actual_occlusion(overlay, blocked):
    snapshot = SimpleNamespace(signature=('fixture', overlay),
                               frame=(10, 10, 10, 10),
                               overlays=(('window-above', *overlay),))
    closed = []
    calls = []

    class Probe:
        def __init__(self, *args):
            pass
        async def snapshot(self):
            return snapshot
        async def close(self):
            closed.append(True)

    async def run(argv, timeout):
        calls.append(argv[0])
        # Synthetic PNG only; no screen capture device or subprocess.
        Image.new('RGB', (10, 10)).save(argv[-1])
        return b''

    backend = ForegroundBackend(probe_factory=Probe, run=run)
    if blocked:
        with pytest.raises(ScreenError):
            await backend.select()
        with pytest.raises(ScreenError):
            await backend.capture(snapshot.signature, lambda: None)
        assert calls == []
    else:
        assert await backend.select() == snapshot.signature
        assert await backend.capture(snapshot.signature, lambda: None)
        assert calls == ['spectacle']
    assert len(closed) == 2
