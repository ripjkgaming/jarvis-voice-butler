import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import audio_duck  # noqa: E402
from audio_duck import Ducker  # noqa: E402


class FakePactl:
    def __init__(self, streams):
        self.s = {x["index"]: x for x in streams}
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        if args[1:4] == ["-f", "json", "list"]:
            return json.dumps([
                {"index": i, "mute": x.get("mute", False),
                 "volume": {f"c{k}": {"value": v} for k, v in enumerate(x["vols"])},
                 "properties": {"application.name": x["app"],
                                "application.process.binary": x.get("bin", x["app"].lower()),
                                "application.process.id": str(x.get("pid", i))}}
                for i, x in self.s.items()])
        if args[1] == "set-sink-input-volume":
            i = int(args[2])
            if i in self.s:
                self.s[i]["vols"] = [int(v) for v in args[3:]]
        return ""

    def vols(self, i):
        return self.s[i]["vols"]


def mk(tmp_path, streams, tail=0.05):
    f = FakePactl(streams)
    d = Ducker(runner=f, tail=tail, rescan=0.03, ramp=0.0, ledger=tmp_path / "led.json")
    return f, d


def streams():
    return [
        {"index": 1, "app": "Brave", "vols": [65536, 65536]},
        {"index": 2, "app": "Spotify", "vols": [30000, 32000]},
        {"index": 3, "app": "paplay", "vols": [65536, 65536]},
        {"index": 4, "app": "ALSA plug-in [python3.11]", "bin": "python3.11", "vols": [65536, 65536]},
        {"index": 5, "app": "Muted", "vols": [65536, 65536], "mute": True},
    ]


def settle(d):
    assert d.wait_idle(3)


def test_duck_only_non_jarvis_and_exact_restore(tmp_path):
    f, d = mk(tmp_path, streams())
    d.duck()
    time.sleep(0.2)
    assert f.vols(1) == [int(65536 * 0.18)] * 2
    assert f.vols(2) == [int(30000 * 0.18), int(32000 * 0.18)]
    assert f.vols(3) == [65536, 65536] and f.vols(4) == [65536, 65536]
    assert f.vols(5) == [65536, 65536]
    assert (tmp_path / "led.json").exists()
    d.unduck()
    settle(d)
    assert f.vols(1) == [65536, 65536] and f.vols(2) == [30000, 32000]
    assert not (tmp_path / "led.json").exists()


def test_refcount_overlap_and_tail(tmp_path):
    f, d = mk(tmp_path, streams(), tail=0.3)
    d.duck(); d.duck()
    time.sleep(0.15)
    d.unduck()
    time.sleep(0.5)
    assert f.vols(1)[0] < 20000  # still held by the second speaker
    d.unduck()
    time.sleep(0.1)
    assert f.vols(1)[0] < 20000  # tail not elapsed yet
    # new duck inside tail cancels the release (no pumping)
    d.duck()
    time.sleep(0.5)
    assert f.vols(1)[0] < 20000
    d.unduck()
    settle(d)
    assert f.vols(1) == [65536, 65536]


def test_new_stream_while_ducked(tmp_path):
    f, d = mk(tmp_path, streams())
    d.duck()
    time.sleep(0.1)
    f.s[9] = {"index": 9, "app": "mpv", "vols": [50000, 50000]}
    time.sleep(0.2)
    assert f.vols(9)[0] == int(50000 * 0.18)
    d.unduck()
    settle(d)
    assert f.vols(9) == [50000, 50000]


def test_sir_changed_volume_while_ducked(tmp_path):
    f, d = mk(tmp_path, streams())
    d.duck()
    time.sleep(0.1)
    f.s[1]["vols"] = [40000, 40000]  # Sir sets volume by voice
    time.sleep(0.2)
    d.unduck()
    settle(d)
    assert f.vols(1) == [40000, 40000]  # new value is the new original


def test_sir_changes_after_ducked_pass_not_clobbered(tmp_path):
    f, d = mk(tmp_path, streams())
    d.rescan = 5  # no rescan before release
    d.duck()
    time.sleep(0.15)
    f.s[2]["vols"] = [12345, 12345]
    d.unduck()
    settle(d)
    assert f.vols(2) == [12345, 12345]


def test_crash_repair_from_ledger(tmp_path):
    f, d = mk(tmp_path, streams())
    d.duck()
    time.sleep(0.15)
    assert f.vols(1)[0] < 20000
    # "crash": new process, same ledger, same (still ducked) streams
    d2 = Ducker(runner=f, ledger=tmp_path / "led.json")
    d2.repair()
    assert f.vols(1) == [65536, 65536] and f.vols(2) == [30000, 32000]
    assert not (tmp_path / "led.json").exists()
    d.release_all()


def test_disabled_env(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DUCK", "0")
    f, d = mk(tmp_path, streams())
    d.duck()
    time.sleep(0.15)
    assert f.vols(1) == [65536, 65536]
    assert not any(c[1] == "set-sink-input-volume" for c in f.calls)


def test_level_env_and_text_parser(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DUCK_LEVEL", "0.5")
    f, d = mk(tmp_path, [{"index": 1, "app": "Brave", "vols": [60000, 60000]}])
    d.duck(); time.sleep(0.15)
    assert f.vols(1) == [30000, 30000]
    d.release_all()
    txt = ('Sink Input #7\n\tMute: no\n\tVolume: front-left: 65536 / 100% / 0.00 dB,   front-right: 32768 / 50% / -18 dB\n'
           '\tProperties:\n\t\tapplication.name = "Brave"\n\t\tapplication.process.id = "88"\n')
    s = audio_duck.parse_streams(txt)
    assert s[0]["index"] == 7 and s[0]["vols"] == [65536, 32768] and s[0]["app"] == "Brave"


def test_never_raises(tmp_path):
    d = Ducker(runner=lambda a: 1 / 0, tail=0.01, ledger=tmp_path / "x.json")
    d.duck(); d.unduck(); time.sleep(0.1); d.release_all()
