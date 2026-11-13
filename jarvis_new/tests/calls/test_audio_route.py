import json

from calls.audio_route import AudioRoute


class FakePactl:
    """Tiny stateful pactl double."""

    def __init__(self):
        self.mods: dict[str, str] = {}  # id -> args
        self.next = 100
        self.log: list[list[str]] = []
        self.sink_inputs: list[dict] = []
        self.source_outputs: list[dict] = []
        self.fail_load = False

    def _sinks(self):
        return [
            (str(i), a.split("sink_name=")[1].split()[0])
            for i, a in self.mods.items()
            if "sink_name=" in a
        ]

    def __call__(self, argv):
        a = list(argv)
        self.log.append(a)
        assert a[0] == "pactl"
        a = a[1:]
        if a[0] == "load-module":
            if self.fail_load:
                return 1, "fail"
            self.next += 1
            self.mods[str(self.next)] = a[1] + "\t" + " ".join(a[2:])
            return 0, str(self.next)
        if a[0] == "unload-module":
            self.mods.pop(a[1], None)
            return 0, ""
        if a[:3] == ["list", "short", "modules"]:
            return 0, "\n".join(
                f"{i}\t{v.split(chr(9))[0]}\t{v.split(chr(9))[1]}"
                for i, v in self.mods.items()
            )
        if a[:3] == ["list", "short", "sinks"]:
            return 0, "\n".join(f"{i}\t{n}\tPW\ts16\tIDLE" for i, n in self._sinks())
        if a[:3] == ["list", "short", "sources"]:
            rows = []
            for i, n in self._sinks():
                rows.append(f"{i}\t{n}.monitor\tPW\ts16\tIDLE")
            for i, v in self.mods.items():
                if "source_name=" in v:
                    rows.append(
                        f"{i}\t{v.split('source_name=')[1].split()[0]}\tPW\t\tIDLE"
                    )
            return 0, "\n".join(rows)
        if a[:3] == ["--format=json", "list", "sink-inputs"]:
            return 0, json.dumps(self.sink_inputs)
        if a[:3] == ["--format=json", "list", "source-outputs"]:
            return 0, json.dumps(self.source_outputs)
        if a[0].startswith("move-"):
            return 0, ""
        return 1, "unknown"


def mk(fake):
    return AudioRoute.unique("t", runner=fake)


def test_setup_creates_and_is_idempotent():
    f = FakePactl()
    r = mk(f)
    assert r.setup() and len(f.mods) == 3
    assert r.setup() and len(f.mods) == 3
    assert r.health()["ok"]


def test_teardown_unloads_only_ours_and_is_idempotent():
    f = FakePactl()
    f.mods["1"] = "module-null-sink\tsink_name=other"
    r = mk(f)
    r.setup()
    assert r.teardown()
    assert list(f.mods) == ["1"]
    assert r.teardown()
    assert not r.health()["ok"]


def test_never_sets_defaults():
    f = FakePactl()
    r = mk(f)
    r.setup()
    r.route_streams()
    r.teardown()
    assert not any(c[1].startswith("set-default") for c in f.log)


def test_setup_failure_reports_false():
    f = FakePactl()
    f.fail_load = True
    assert not mk(f).setup()


def test_route_moves_only_whatsie():
    f = FakePactl()
    r = mk(f)
    r.setup()
    f.sink_inputs = [
        {"index": 7, "sink": 1, "properties": {"application.name": "WhatSie"}},
        {
            "index": 8,
            "sink": 1,
            "properties": {
                "application.name": "firefox",
                "application.process.binary": "firefox",
            },
        },
        {
            "index": 9,
            "sink": 1,
            "properties": {"application.process.binary": "com.ktechpit.whatsie"},
        },
    ]
    f.source_outputs = [
        {"index": 3, "source": 1, "properties": {"application.name": "WhatSie"}},
        {"index": 4, "source": 1, "properties": {"application.name": "obs"}},
    ]
    moved = r.route_streams()
    assert moved == {"sink_inputs": [7, 9], "source_outputs": [3]}
    cmds = [c[1:] for c in f.log if c[1].startswith("move-")]
    assert ["move-sink-input", "7", r.sink_in] in cmds
    assert ["move-source-output", "3", r.mic] in cmds
    assert all(c[1] not in ("8", "4") for c in cmds)


def test_route_skips_already_routed():
    f = FakePactl()
    r = mk(f)
    r.setup()
    idx = int(next(i for i, n in f._sinks() if n == r.sink_in))
    f.sink_inputs = [
        {"index": 7, "sink": idx, "properties": {"application.name": "WhatSie"}}
    ]
    assert r.route_streams()["sink_inputs"] == []


def test_health_reports_missing():
    h = mk(FakePactl()).health()
    assert not h["ok"] and len(h["missing"]) == 4
