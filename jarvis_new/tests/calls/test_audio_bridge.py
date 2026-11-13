import asyncio

import numpy as np

from calls.call_bridge import CallBridge, level, resample_s16


class FakeStdout:
    def __init__(self):
        self.q: asyncio.Queue = asyncio.Queue()

    async def readexactly(self, n):
        d = await self.q.get()
        if d is None:
            raise asyncio.IncompleteReadError(b"", n)
        return d


class FakeStdin:
    def __init__(self):
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, b):
        self.writes.append(b)

    async def drain(self):
        pass

    def close(self):
        self.closed = True


class FakeProc:
    def __init__(self):
        self.stdout, self.stdin = FakeStdout(), FakeStdin()
        self.terminated = False

    def terminate(self):
        self.terminated = True

    async def wait(self):
        return 0


def make():
    procs = {}

    async def spawn(*argv, stdin=False):
        p = FakeProc()
        procs["pacat" if stdin else "parec"] = (p, argv)
        return p

    sleeps = []

    async def sleep(s):
        sleeps.append(s)
        await asyncio.sleep(0)

    return CallBridge(spawn=spawn, sleep=sleep), procs, sleeps


def pcm(n_samples, val=1000):
    return (np.full(n_samples, val, dtype="<i2")).tobytes()


def test_resample_length_and_dc():
    out, _ = resample_s16(pcm(2400), 24000, 48000)
    assert len(out) == 4800 * 2
    assert set(np.frombuffer(out, "<i2")) == {1000}


def test_resample_chunked_matches_whole():
    t = (np.sin(np.arange(4800) / 20) * 8000).astype("<i2").tobytes()
    whole, _ = resample_s16(t, 24000, 48000)
    a, tail = resample_s16(t[:4000], 24000, 48000)
    b, _ = resample_s16(t[4000:], 24000, 48000, tail)
    assert len(a) + len(b) == len(whole)
    d = np.abs(np.frombuffer(a + b, "<i2").astype(int) - np.frombuffer(whole, "<i2"))
    assert d[2:].max() < 400  # continuous across the chunk seam


def test_level():
    assert level(b"") == 0.0
    assert level(pcm(100, 0)) == 0.0
    assert 0 < level(pcm(100, 3000)) <= 1.0


async def test_ears_frames_to_callback_and_argv():
    br, procs, _ = make()
    got = []
    await br.start(got.append)
    rec, argv = procs["parec"]
    assert argv[:3] == ("parec", "-d", "jarvis_call_in.monitor")
    assert "16000" in argv
    rec.stdout.q.put_nowait(pcm(512, 2000))
    rec.stdout.q.put_nowait(None)
    await asyncio.sleep(0.05)
    assert len(got) == 1 and len(got[0]) == 1024
    assert br.in_level > 0
    await br.stop()
    assert rec.terminated


async def test_play_chunks_le_100ms_and_resampled():
    br, procs, _ = make()
    await br.start(lambda b: None)
    await br.play(pcm(24000))  # 1 s @24k
    await asyncio.sleep(0.2)
    pac, argv = procs["pacat"]
    assert "48000" in argv
    ws = pac.stdin.writes
    assert all(len(w) <= 48000 * 100 // 1000 * 2 for w in ws)
    await br.stop()


async def test_flush_drops_queue_instantly():
    br, _procs, _ = make()
    # don't start writer: inspect queue directly
    await br.play(pcm(24000))
    assert br.pending_ms >= 900
    await br.flush()
    assert br.pending_ms == 0


async def test_flush_midstream_stops_writes():
    br, _procs, _sleeps = make()

    async def slow(s):
        await asyncio.sleep(0.01)

    br._sleep = slow
    await br.start(lambda b: None)
    await br.play(pcm(24000 * 3))
    await asyncio.sleep(0.05)
    pac = _procs["pacat"][0]
    await br.flush()
    n = len(pac.stdin.writes)
    await asyncio.sleep(0.1)
    assert len(pac.stdin.writes) <= n + 1
    await br.stop()


async def test_stop_idempotent():
    br, _, _ = make()
    await br.stop()
    await br.start(lambda b: None)
    await br.stop()
    await br.stop()
    assert not br.running
