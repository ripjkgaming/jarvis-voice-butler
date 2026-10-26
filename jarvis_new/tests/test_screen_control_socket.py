"""Native peer authentication and protocol tests; no real socket connection."""

import asyncio
import os
import socket
import stat
import struct
from types import SimpleNamespace

import pytest

from screen_control_socket import NativeControlServer, NativePeerVerifier


def info(inode=11, *, mode=stat.S_IFREG | 0o700, uid=None):
    return SimpleNamespace(
        st_ino=inode, st_dev=2, st_uid=os.getuid() if uid is None else uid, st_mode=mode
    )


class Peer:
    def __init__(self, uid=None, pid=123):
        self.uid = os.getuid() if uid is None else uid
        self.pid = pid

    def getsockopt(self, level, option, length):
        assert (level, option, length) == (socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        return struct.pack("3i", self.pid, self.uid, os.getgid())


class Writer:
    def __init__(self, peer):
        self.peer = peer
        self.raw = b""
        self.closed = False

    def get_extra_info(self, key):
        assert key == "socket"
        return self.peer

    def write(self, raw):
        self.raw += raw

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class Holder:
    generation = 1
    clock = staticmethod(__import__("time").monotonic)

    def __init__(self):
        self.calls = []

    async def resume(self, **kwargs):
        assert kwargs["quiet_confirmed"] is True
        self.calls.append("resume")
        return {"status": "listening", "generation": 2}

    async def stop(self):
        self.calls.append("stop")

    async def hangup(self):
        self.calls.append("hangup")


def verifier(inspect=None):
    return NativePeerVerifier(
        "/explicit/native-shell", inspect=inspect or (lambda path: info())
    )


def test_native_auth_uses_kernel_uid_and_executable_inode():
    auth = verifier()
    assert auth.allows(Peer())
    assert not auth.allows(Peer(uid=os.getuid() + 1))
    assert not auth.allows(Peer(pid=0))
    auth.inspect = lambda path: info(inode=99)
    assert not auth.allows(Peer())


def test_pin_refuses_relative_group_writable_and_non_owner_executable():
    with pytest.raises(ValueError):
        NativePeerVerifier("relative")
    for invalid in (
        info(mode=stat.S_IFREG | 0o722),
        info(uid=os.getuid() + 1),
        info(mode=stat.S_IFLNK | 0o700),
    ):
        with pytest.raises(ValueError):
            verifier(lambda path, pinned=invalid: pinned)


async def request(server, raw, peer=None):
    reader = asyncio.StreamReader()
    reader.feed_data(raw)
    reader.feed_eof()
    writer = Writer(peer or Peer())
    await server.handle(reader, writer)
    assert writer.closed
    return writer.raw


@pytest.mark.asyncio
async def test_native_protocol_dispatches_only_authenticated_actions(tmp_path):
    holder = Holder()
    post = []
    server = NativeControlServer(
        tmp_path / "control.sock",
        holder,
        verifier(),
        on_resume=lambda: post.append(True),
    )
    assert await request(server, b"resume_quiet\n") == b"ok listening 2\n"
    assert post == [True]
    assert await request(server, b"stop\n") == b"ok stopped 0\n"
    assert await request(server, b"hangup\n") == b"ok closed 0\n"
    assert holder.calls == ["resume", "stop", "hangup"]
    assert not server._authenticated


@pytest.mark.asyncio
async def test_other_uid_or_executable_cannot_authorize_resume(tmp_path):
    holder = Holder()
    server = NativeControlServer(tmp_path / "control.sock", holder, verifier())
    assert (
        await request(server, b"resume_quiet\n", Peer(uid=os.getuid() + 1))
        == b"error refused\n"
    )
    server.verifier.inspect = lambda path: info(inode=999)
    assert await request(server, b"resume_quiet\n") == b"error refused\n"
    assert not holder.calls


@pytest.mark.asyncio
async def test_body_cannot_claim_source_auth_or_capture_consent(tmp_path):
    holder = Holder()
    server = NativeControlServer(tmp_path / "control.sock", holder, verifier())
    for raw in [
        b"resume\n",
        b'{"source":"native", "approved":true}\n',
        b"capture\n",
        b"resume_quiet\nstop\n",
        b"x" * 129 + b"\n",
    ]:
        assert await request(server, raw) == b"error refused\n"
    assert not holder.calls


@pytest.mark.asyncio
async def test_peer_is_rechecked_after_awaiting_payload(tmp_path):
    holder = Holder()
    count = {"n": 0}

    def inspect(path):
        count["n"] += 1
        return info(inode=11 if count["n"] < 3 else 12)

    server = NativeControlServer(tmp_path / "control.sock", holder, verifier(inspect))
    assert await request(server, b"resume_quiet\n") == b"error refused\n"
    assert not holder.calls


@pytest.mark.asyncio
async def test_post_resume_consent_error_stops_new_input(tmp_path):
    holder = Holder()

    def invalid_consent():
        raise ValueError("Synthetic private file failure; no content may escape")

    server = NativeControlServer(
        tmp_path / "control.sock", holder, verifier(), on_resume=invalid_consent
    )
    assert await request(server, b"resume_quiet\n") == b"error refused\n"
    assert holder.calls == ["resume", "stop"]
