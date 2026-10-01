import google_api


def test_token_reused_until_expiry(monkeypatch):
    google_api.cache_token("k", "tok", 3600)
    assert google_api.cached_token("k") == "tok"
    t = [google_api.time.monotonic() + 4000]
    monkeypatch.setattr(google_api.time, "monotonic", lambda: t[0])
    assert google_api.cached_token("k") is None


def test_no_lifetime_means_never_cached():
    google_api.cache_token("k2", "tok", None)
    google_api.cache_token("k3", "tok", 60)  # inside the safety margin
    assert google_api.cached_token("k2") is None
    assert google_api.cached_token("k3") is None
