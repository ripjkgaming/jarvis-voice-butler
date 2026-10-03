import gemini_watchdog as g


class _Run:
    def __init__(self) -> None:
        self.clock = g.StallClock()
        self.t = 0.0
        self.fired: list[float] = []

    def frames(self, seconds: float, rms: float) -> None:
        for _ in range(round(seconds / 0.01)):
            self.t += 0.01
            if self.clock.mic(0.01, rms, self.t):
                self.fired.append(self.t)

    def talk(self, seconds: float) -> None:
        self.frames(seconds, 2000.0)

    def quiet(self, seconds: float) -> None:
        self.frames(seconds, 10.0)


def test_answered_turns_never_restart():
    r = _Run()
    for _ in range(10):
        r.talk(3)
        r.quiet(1)
        r.clock.server(r.t)
        r.quiet(30)
    assert r.fired == []


def test_short_noise_bursts_never_restart():
    r = _Run()
    for _ in range(600):
        r.talk(0.2)
        r.quiet(0.8)
    assert r.fired == []


def test_repeated_unanswered_speech_restarts_once_per_cooldown():
    r = _Run()
    for _ in range(4):
        r.talk(3)
        r.quiet(5)
    assert len(r.fired) == 1
    assert r.fired[0] >= g.SILENT_S
    first = r.fired[0]
    for _ in range(4):
        r.talk(3)
        r.quiet(5)
    assert all(t - first >= g.COOLDOWN_S for t in r.fired[1:])


def test_single_long_utterance_does_not_restart():
    r = _Run()
    r.talk(20)
    r.quiet(2)
    assert r.fired == []


def test_frame_rms():
    import numpy as np

    assert g.frame_rms(np.zeros(160, dtype=np.int16).tobytes()) == 0.0
    loud = np.full(160, 1000, dtype=np.int16).tobytes()
    assert abs(g.frame_rms(loud) - 1000.0) < 1e-3
    assert g.frame_rms(b"") == 0.0


def test_install_wraps_sessions_and_restarts_on_stall(monkeypatch):
    import numpy as np

    class Frame:
        def __init__(self, level: int) -> None:
            self.samples_per_channel = 160
            self.sample_rate = 16000
            self.data = np.full(160, level, dtype=np.int16).tobytes()

    class Session:
        def __init__(self) -> None:
            self.pushed = 0
            self.restarts = 0

        def push_audio(self, frame) -> None:
            self.pushed += 1

        def _handle_server_content(self, content) -> None:
            pass

        def _mark_restart_needed(self, on_error: bool = False) -> None:
            self.restarts += 1

    class Model:
        def session(self):
            return Session()

    now = [0.0]
    monkeypatch.setattr(g.time, "monotonic", lambda: now[0])
    sess = g.install(Model()).session()

    def run(seconds: float, level: int) -> None:
        for _ in range(round(seconds / 0.01)):
            now[0] += 0.01
            sess.push_audio(Frame(level))

    for _ in range(4):
        run(3, 3000)
        run(5, 0)
    assert sess.restarts == 1
    assert sess.pushed == round(32 / 0.01)


def test_disabled_leaves_model_alone(monkeypatch):
    monkeypatch.setenv("JARVIS_GEMINI_WATCHDOG", "0")

    class Model:
        def session(self):
            return "plain"

    m = Model()
    original = m.session
    assert g.install(m).session == original
