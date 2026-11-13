import numpy as np

from audio_playback import FastAudioPlayback


def _cb(p, frames):
    out = np.zeros((frames, 1), dtype=np.int16)
    p._callback(out, frames, None, None)
    return out[:, 0]


def test_fade_ramps_down_then_mutes():
    p = FastAudioPlayback()
    p._rate = 1000
    p._queue.append(np.full(1000, 10000, dtype=np.int16))
    p._queued_samples = 1000
    p.fade_out(100)  # 100 samples
    out = _cb(p, 200)
    assert out[0] == 10000 and 0 < out[50] < 10000
    assert np.all(out[100:] == 0)
    assert p._muted is True
    p.resume()
    assert p._muted is False
    p._executor.shutdown(wait=False)
