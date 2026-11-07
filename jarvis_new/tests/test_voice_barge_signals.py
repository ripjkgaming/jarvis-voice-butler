"""Model-authoritative mute/reply sequencing, no real account or devices."""
import json

from voice_barge_signals import TOPIC, BargeSync, ProviderEvents, decode, encode


def test_packets_validate_topic_and_payload():
    provider = ProviderEvents()
    user = provider.user_speaking()
    reply = provider.reply_created()
    assert decode(encode(user), TOPIC) == {"kind": "user_start", "user_seq": 1}
    assert decode(encode(reply), TOPIC) == {
        "kind": "reply_start", "user_seq": 1, "reply_seq": 1
    }
    assert decode(encode(user), "unknown") is None
    assert decode(b"not json", TOPIC) is None
    assert decode(json.dumps({"kind": "user_start", "user_seq": True}).encode(), TOPIC) is None
    assert decode(encode({"kind": "reply_start", "user_seq": 1, "reply_seq": -1}), TOPIC) is None
    assert decode(b"X" * 257, TOPIC) is None


def test_old_reply_cannot_unmute_until_new_user_and_new_reply():
    s = BargeSync()
    assert not s.ingest({"kind": "reply_start", "reply_seq": 1, "user_seq": 0}, 9.0)
    s.interrupt(10.0)
    assert not s.ready
    assert not s.ingest({"kind": "reply_start", "reply_seq": 1, "user_seq": 0}, 10.01)
    assert not s.ingest({"kind": "reply_start", "reply_seq": 2, "user_seq": 0}, 10.02)
    assert not s.ingest({"kind": "user_start", "user_seq": 1}, 10.08)
    assert not s.ready
    assert s.ingest({"kind": "reply_start", "reply_seq": 3, "user_seq": 1}, 10.20)
    assert s.ready
    s.complete()
    assert not s.ready


def test_model_user_start_may_precede_local_64ms_detection():
    s = BargeSync()
    s.ingest({"kind": "reply_start", "reply_seq": 1, "user_seq": 0}, 4.0)
    s.ingest({"kind": "user_start", "user_seq": 1}, 10.0)
    s.interrupt(10.064)
    assert s.ingest({"kind": "reply_start", "reply_seq": 2, "user_seq": 1}, 10.4)


def test_stale_user_signal_cannot_authorize_new_reply():
    s = BargeSync()
    s.ingest({"kind": "user_start", "user_seq": 1}, 1.0)
    s.ingest({"kind": "reply_start", "reply_seq": 1, "user_seq": 1}, 1.5)
    s.interrupt(10.0)
    assert not s.ingest({"kind": "reply_start", "reply_seq": 2, "user_seq": 1}, 10.2)
    assert not s.ingest({"kind": "user_start", "user_seq": 2}, 10.3)
    assert s.ingest({"kind": "reply_start", "reply_seq": 3, "user_seq": 2}, 10.5)


def test_bad_seq_or_packet_order_does_not_unmute():
    s = BargeSync()
    s.ingest({"kind": "reply_start", "reply_seq": 5, "user_seq": 2}, 1.0)
    s.interrupt(10.0)
    assert not s.ingest({"kind": "reply_start", "reply_seq": 4, "user_seq": 100}, 10.1)
    assert not s.ingest({"kind": "user_start", "user_seq": 2}, 10.2)
    assert not s.ingest({"kind": "reply_start", "reply_seq": 6, "user_seq": 2}, 10.3)
    assert not s.ready
    assert not s.ingest({"kind": "user_start", "user_seq": 3}, 10.4)
    assert s.ingest({"kind": "reply_start", "reply_seq": 7, "user_seq": 3}, 10.5)


def test_no_timed_auto_resume_of_old_reply():
    s = BargeSync()
    s.ingest({"kind": "reply_start", "reply_seq": 1, "user_seq": 0}, 0.0)
    s.interrupt(10.0)
    assert not s.ready
    assert not s.ingest({"kind": "reply_start", "reply_seq": 1, "user_seq": 0}, 1000.0)
    assert not s.ready
