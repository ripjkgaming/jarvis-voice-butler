from turn_manager import SLOW_BOOT_LINE, State, TurnManager, classify


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def ready_tm():
    tm = TurnManager(clock=Clock())
    tm.set_ready()
    return tm


def test_boot_queue_fifo_and_cap():
    clk = Clock()
    tm = TurnManager(clock=clk)
    assert tm.on_user_speech("first").kind == "queue"
    clk.t += 10
    tm.on_user_speech("second")
    clk.t += 25  # first is now 35 s old
    tm.on_user_speech("third")
    assert tm.user_spoke_during_boot
    assert tm.pop_boot_queue() == ["first", "second", "third"]
    assert tm.pop_boot_queue() == []


def test_slow_boot_line_once():
    clk = Clock()
    tm = TurnManager(clock=clk)
    assert tm.slow_boot_line(clk.t + 10) is None  # nobody spoke
    tm.on_user_speech("hello")
    assert tm.slow_boot_line(clk.t + 1) is None
    assert tm.slow_boot_line(clk.t + 3) == SLOW_BOOT_LINE
    assert tm.slow_boot_line(clk.t + 30) is None


def test_set_ready_transitions_and_notifies():
    tm = TurnManager()
    seen = []
    tm.on_state(seen.append)
    assert tm.state is State.LOADING
    tm.set_ready()
    assert tm.state is State.LISTENING
    assert seen == [State.LISTENING]


def test_classify():
    assert classify("stop", State.LISTENING) == "stop"
    assert classify("Never mind.", State.THINKING) == "stop"
    assert classify("forget it", State.THINKING) == "stop"
    assert classify("stop talking", State.SPEAKING) == "stop"
    assert classify("cancel my meeting", State.THINKING) == "new"
    assert classify("nonsense", State.THINKING) == "new"
    assert classify("no the other one", State.THINKING) == "continuation"
    assert classify("no the other one", State.LISTENING) == "new"
    assert classify("actually make it two", State.THINKING) == "continuation"
    assert classify("play something", State.THINKING) == "new"


def test_continuation_merge_cancels_previous():
    tm = ready_tm()
    hooks = []
    tm.register_cancel_hook("llm", hooks.append)
    t1 = tm.begin_turn("play jazz")
    act = tm.on_user_speech("no the other one")
    assert act.kind == "merge"
    assert act.merged_text == "play jazz no the other one"
    assert t1.cancelled
    assert act.turn.id > t1.id and act.turn.text == act.merged_text
    assert hooks == [t1.id]


def test_stop_word_cancels_all():
    tm = ready_tm()
    hooks = []
    tm.register_cancel_hook("tts", hooks.append)
    t1 = tm.begin_turn("tell me a story")
    act = tm.on_user_speech("stop")
    assert act.kind == "cancel_all"
    assert t1.cancelled and hooks == [t1.id]
    assert tm.state is State.LISTENING
    assert tm.deliver() is None


def test_new_request_during_side_effect_turn():
    tm = ready_tm()
    hooks = []
    tm.register_cancel_hook("llm", hooks.append)
    t1 = tm.begin_turn("send the email", side_effect=True)
    act = tm.on_user_speech("what time is it")
    assert act.kind == "new"
    assert not t1.cancelled and t1.superseded and hooks == []
    assert not tm.submit_reply(t1.id, "email sent")
    assert tm.submit_reply(act.turn.id, "3pm")
    assert tm.deliver() == (act.turn.id, "3pm")


def test_continuation_does_not_cancel_side_effect_turn():
    tm = ready_tm()
    t1 = tm.begin_turn("send the email", side_effect=True)
    act = tm.on_user_speech("also cc bob")
    assert act.kind == "new"
    assert not t1.cancelled


def test_stale_reply_dropped():
    tm = ready_tm()
    t1 = tm.begin_turn("a")
    assert tm.submit_reply(t1.id, "old")
    t2 = tm.begin_turn("b")
    assert tm.deliver() is None
    assert not tm.submit_reply(t1.id, "late")
    assert not tm.is_current(t1.id) and tm.is_current(t2.id)
    tm.submit_reply(t2.id, "new")
    assert tm.deliver() == (t2.id, "new")


def test_deliver_newest_only():
    tm = ready_tm()
    t1 = tm.begin_turn("a", side_effect=True)
    t2 = tm.begin_turn("b")
    tm.submit_reply(t2.id, "B")
    assert tm.deliver() == (t2.id, "B")
    assert tm.deliver() is None
    assert t1.superseded


def test_single_speaker():
    tm = ready_tm()
    t = tm.begin_turn("x")
    tm.submit_reply(t.id, "hi")
    assert tm.start_speaking(t.id)
    assert tm.state is State.SPEAKING
    assert not tm.start_speaking(t.id)
    tm.submit_reply(t.id, "again")
    assert tm.deliver() is None  # busy speaking
    tm.finish_speaking(t.id)
    assert tm.state is State.LISTENING
    assert tm.deliver() == (t.id, "again")


def test_filler_once_per_turn():
    tm = ready_tm()
    t = tm.begin_turn("x")
    assert tm.filler(t) is True
    assert tm.filler(t) is False
    t2 = tm.begin_turn("y")
    tm.cancel_filler(t2)
    assert tm.filler(t2) is False
    t3 = tm.begin_turn("z")
    assert tm.filler(t3) is True


def test_barge_in_calls_hooks():
    tm = ready_tm()
    calls = []
    for name in ("stt", "llm", "tts", "link"):
        tm.register_cancel_hook(name, lambda tid, n=name: calls.append((n, tid)))
    t = tm.begin_turn("x")
    tm.submit_reply(t.id, "reply")
    tm.start_speaking(t.id)
    tm.barge_in()
    assert t.cancelled
    assert tm.state is State.LISTENING and not tm.speaking
    assert sorted(calls) == [
        ("link", t.id),
        ("llm", t.id),
        ("stt", t.id),
        ("tts", t.id),
    ]
    assert not tm.is_current(t.id)


def test_cancel_token_callbacks_fire_once():
    tm = ready_tm()
    t = tm.begin_turn("x")
    n = []
    t.cancel.add_callback(lambda: n.append(1))
    tm.begin_turn("y")
    tm.begin_turn("z")
    assert n == [1] and t.cancel.event.is_set()


def test_completed_request_is_not_merged_with_later_correction():
    tm = ready_tm()
    turn = tm.begin_turn("old request")
    tm.start_speaking(turn.id)
    tm.finish_speaking(turn.id)
    assert tm.on_user_speech("actually another request").kind == "new"


def test_explicit_cancel_propagates_once_and_rejects_playback():
    tm = ready_tm()
    seen = []
    tm.register_cancel_hook("audio", seen.append)
    turn = tm.begin_turn("story")
    tm.cancel(turn.id)
    tm.cancel(turn.id)
    assert seen == [turn.id]
    assert not tm.start_speaking(turn.id)


async def test_shielded_action_finishes_once_after_parent_cancel():
    import asyncio

    from turn_manager import complete_side_effect

    started = asyncio.Event()
    release = asyncio.Event()
    done = []

    async def action():
        started.set()
        await release.wait()
        done.append("sent")
        return "sent"

    task = asyncio.create_task(complete_side_effect(action()))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    assert await task == "sent"
    assert done == ["sent"]


async def test_native_correction_restarts_combined_text_once():
    import asyncio
    from types import SimpleNamespace

    from turn_manager import VoiceTurnBinding

    class Session:
        def __init__(self):
            self.events = {}
            self.replies = []
            self.interrupts = 0

        def on(self, name, callback):
            self.events[name] = callback

        def interrupt(self, **kwargs):
            self.interrupts += 1
            future = asyncio.get_running_loop().create_future()
            future.set_result(None)
            return future

        async def generate_reply(self, **kwargs):
            self.replies.append(kwargs["user_input"])

    session = Session()
    binding = VoiceTurnBinding(session, SimpleNamespace(is_echo=lambda text: False))
    binding.ready()
    binding.transcript(SimpleNamespace(transcript="play jazz", is_final=True))
    binding.transcript(SimpleNamespace(transcript="no I meant blues", is_final=True))
    await asyncio.sleep(0)
    assert session.replies == ["play jazz no I meant blues"]
    assert session.interrupts == 1
    binding.transcript(SimpleNamespace(transcript="stop", is_final=True))
    assert binding.manager.current_turn.cancelled
    assert session.interrupts == 2
    await binding.close()


async def test_native_echo_is_not_minted_and_interrupts_model_response():
    from types import SimpleNamespace

    from turn_manager import VoiceTurnBinding

    class Session:
        def __init__(self):
            self.interrupted = False

        def on(self, *args):
            pass

        def interrupt(self, **kwargs):
            self.interrupted = True

    session = Session()
    binding = VoiceTurnBinding(session, SimpleNamespace(is_echo=lambda text: True))
    binding.ready()
    binding.transcript(
        SimpleNamespace(transcript="our own spoken words", is_final=True)
    )
    assert session.interrupted
    assert binding.manager.current_turn is None
    await binding.close()


def test_mutation_classification_is_conservative():
    from turn_manager import tool_has_side_effect

    assert tool_has_side_effect("gmail_send")
    assert tool_has_side_effect("run_command")
    assert tool_has_side_effect("chess_move")
    assert tool_has_side_effect("unknown_new_tool")
    assert not tool_has_side_effect("tell_time")


async def test_stop_during_boot_queue_does_not_replay_remaining_requests():
    import asyncio
    from types import SimpleNamespace

    from turn_manager import VoiceTurnBinding

    release = asyncio.Event()
    replies = []

    class Session:
        def on(self, *args):
            pass

        def interrupt(self, **kwargs):
            future = asyncio.get_running_loop().create_future()
            future.set_result(None)
            return future

        async def generate_reply(self, **kwargs):
            replies.append(kwargs["user_input"])
            await release.wait()

    session = Session()
    binding = VoiceTurnBinding(session, SimpleNamespace(is_echo=lambda text: False))
    binding.transcript(SimpleNamespace(transcript="first", is_final=True))
    binding.transcript(SimpleNamespace(transcript="second", is_final=True))
    binding.ready()
    await asyncio.sleep(0)
    assert replies == ["first"]
    binding.transcript(SimpleNamespace(transcript="stop", is_final=True))
    release.set()
    await asyncio.sleep(0)
    assert replies == ["first"]
    await binding.close()


def test_turn_ids_are_unique_across_sessions_and_exact_in_javascript():
    first, second = ready_tm(), ready_tm()
    a, b = first.begin_turn("a"), second.begin_turn("b")
    assert a.id != b.id
    assert 0 < a.id < b.id < 2**53


async def test_completed_turn_does_not_resubmit_next_native_transcript():
    import asyncio
    from types import SimpleNamespace

    from turn_manager import VoiceTurnBinding

    class Session:
        def __init__(self):
            self.replies = []

        def on(self, *args):
            pass

        def interrupt(self, **kwargs):
            raise AssertionError("completed turns need no native interruption")

        async def generate_reply(self, **kwargs):
            self.replies.append(kwargs)

    session = Session()
    binding = VoiceTurnBinding(session, SimpleNamespace(is_echo=lambda text: False))
    binding.ready()
    first = binding.manager.begin_turn("first")
    binding.manager.start_speaking(first.id)
    binding.manager.finish_speaking(first.id)
    binding.transcript(SimpleNamespace(transcript="second", is_final=True))
    await asyncio.sleep(0)
    assert session.replies == []
    assert binding.manager.current_turn.text == "second"
    await binding.close()
