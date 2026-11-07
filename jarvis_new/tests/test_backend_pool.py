import backend_pool


def test_limit_moves_to_other_provider_and_cools_down(monkeypatch):
    backend_pool._rest_until.clear()
    seen = []

    def chatgpt(*a, **k):
        seen.append("chatgpt")
        return "", "ChatGPT usage limit reached"

    def claude(*a, **k):
        seen.append("claude")
        return "A plan", None

    providers = {"chatgpt": chatgpt, "claude": claude}
    assert backend_pool.reply("plan", system="s", providers=providers) == (
        "A plan",
        None,
    )
    assert seen == ["chatgpt", "claude"]
    seen.clear()
    backend_pool.reply("plan", system="s", providers=providers)
    assert seen == ["claude"]
    backend_pool._rest_until.clear()


def test_does_not_replay_on_timeout():
    providers = {
        "chatgpt": lambda *a, **k: ("", "ChatGPT timed out"),
        "claude": lambda *a, **k: (_ for _ in ()).throw(AssertionError("replayed")),
    }
    assert (
        backend_pool.reply("task", system="s", providers=providers)[1]
        == "ChatGPT timed out"
    )


def test_explicit_provider_first_and_no_fallback_when_forbidden():
    seen = []

    def claude(*a, **k):
        seen.append("claude")
        return "", "rate limit exceeded"

    providers = {"chatgpt": lambda *a, **k: ("answer", None), "claude": claude}
    assert backend_pool.reply(
        "task",
        system="s",
        preferred="claude",
        providers=providers,
        allow_fallback=False,
    )[1]
    assert seen == ["claude"]
    backend_pool._rest_until.clear()
