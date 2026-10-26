import numpy as np

import mic_uplink as uplink


class PacketSocket:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.sent = []
        self.closed = False

    def settimeout(self, timeout):
        pass

    def recv(self, count):
        return next(self.chunks, b"")

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True


class Model:
    def __init__(self):
        self.frames = []
        self.resets = 0

    def reset(self):
        self.resets += 1

    def predict(self, frame):
        self.frames.append(frame.copy())
        return {uplink.WAKE_MODEL: 0}


def test_handshake_and_audio_in_one_packet_are_preserved():
    pcm = np.arange(1280, dtype="<i2").tobytes()
    sock = PacketSocket([b'{"rate":16000,"channels":1}\n' + pcm])
    model = Model()
    uplink.serve_client(sock, ("mock", 1), model, 0.5)
    assert sock.sent == [b'{"ok": true}\n']
    assert len(model.frames) == 1
    np.testing.assert_array_equal(model.frames[0], np.arange(1280, dtype=np.int16))
    assert model.resets == 1
    assert sock.closed


def test_odd_tcp_boundaries_do_not_drop_sample_bytes():
    pcm = np.arange(1280, dtype="<i2").tobytes()
    sock = PacketSocket(
        [b'{"rate":16000,"channels":1}\n', pcm[:1], pcm[1:18], pcm[18:101], pcm[101:]]
    )
    model = Model()
    uplink.serve_client(sock, ("mock", 1), model, 0.5)
    assert len(model.frames) == 1
    np.testing.assert_array_equal(model.frames[0], np.arange(1280, dtype=np.int16))


def test_second_stream_cannot_corrupt_active_models_state():
    sock = PacketSocket([b'{"rate":16000,"channels":1}\n'])
    model = Model()
    with uplink._model_lock:
        uplink.serve_client(sock, ("mock", 1), model, 0.5)
    assert b"another microphone stream" in sock.sent[0]
    assert not model.frames and model.resets == 0
