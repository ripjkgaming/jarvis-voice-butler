"""Always Aware file executor (PLAN.md §5): denylist, symlink safety, journaled ops.

A fake HOME and JARVIS_HOME under tmp_path; the allowed roots are tmp_path only.
"""

import os

import pytest

from aware import fileedit as fe
from aware import undo_journal as uj


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    jhome = home / ".jarvis"
    monkeypatch.setenv("JARVIS_HOME", str(jhome))
    policy = fe.Policy(home=home, jarvis_home=jhome, roots=(home, tmp_path / "data"))
    (tmp_path / "data").mkdir()
    j = uj.Journal.create("run-fe", kind="file", title="t")
    return home, policy, fe.Editor(j, policy), j


@pytest.mark.parametrize(
    "rel",
    [
        ".ssh/id_ed25519",
        ".ssh/config",
        ".gnupg/pubring.kbx",
        ".local/share/keyrings/login.keyring",
        ".password-store/x.gpg",
        ".config/BraveSoftware/Brave-Browser/Default/Cookies",
        ".config/google-chrome/Default/Login Data",
        ".mozilla/firefox/abc.default/logins.json",
        ".jarvis/keys.env",
        ".jarvis/bridge_token",
        ".jarvis/voice_secret",
        ".jarvis/consent/x.json",
        ".jarvis/brave-profile/Default/Cookies",
        ".jarvis/aware/journal/run/files/0",
        "proj/.env",
        "proj/.env.local",
        "proj/.git/config",
        "proj/github_token",
        ".config/gh/hosts.yml",
        ".aws/credentials",
        ".netrc",
        ".git-credentials",
        "certs/server.pem",
        "certs/server.key",
        ".claude/.credentials.json",
    ],
)
def test_denylist(env, rel):
    home, policy, *_ = env
    with pytest.raises(fe.Denied):
        fe.check_path(home / rel, policy)


@pytest.mark.parametrize(
    "rel", ["notes/todo.md", "proj/src/tokenizer.py", "proj/README.md"]
)
def test_allowed(env, rel):
    home, policy, *_ = env
    assert fe.check_path(home / rel, policy) == (home / rel)


def test_outside_roots_denied(env, tmp_path):
    _, policy, *_ = env
    for p in ("/etc/hosts", "/usr/bin/x", str(tmp_path / "elsewhere" / "f")):
        with pytest.raises(fe.Denied):
            fe.check_path(p, policy)


def test_relative_and_dotdot(env):
    home, policy, *_ = env
    with pytest.raises(fe.Denied):
        fe.check_path("notes/x", policy)
    with pytest.raises(fe.Denied):
        fe.check_path(str(home / "notes" / ".." / ".ssh" / "config"), policy)


def test_symlinked_parent_into_denied_area(env):
    home, policy, *_ = env
    (home / ".ssh").mkdir()
    (home / "innocent").symlink_to(home / ".ssh")
    with pytest.raises(fe.Denied):
        fe.check_path(home / "innocent" / "config", policy)


def test_symlinked_parent_outside_roots(env, tmp_path):
    home, policy, *_ = env
    out = tmp_path / "outside"
    out.mkdir()
    (home / "lnk").symlink_to(out)
    with pytest.raises(fe.Denied):
        fe.check_path(home / "lnk" / "f.txt", policy)


def test_final_symlink_refused(env):
    home, _policy, ed, _ = env
    (home / "real.txt").write_text("keep")
    (home / "link.txt").symlink_to(home / "real.txt")
    with pytest.raises(fe.Denied):
        ed.write_text(home / "link.txt", "x")
    assert (home / "real.txt").read_text() == "keep"


def test_symlink_within_roots_resolves_to_real_path(env):
    home, policy, *_ = env
    (home / "realdir").mkdir()
    (home / "alias").symlink_to(home / "realdir")
    assert fe.check_path(home / "alias" / "a.txt", policy) == home / "realdir" / "a.txt"


def test_write_replace_journaled_and_undo(env):
    home, _policy, ed, j = env
    f = home / "notes" / "a.md"
    f.parent.mkdir()
    f.write_text("hello world\n")
    ed.replace(f, "world", "Sir")
    ed.write_text(home / "notes" / "b.md", "new\n")
    assert f.read_text() == "hello Sir\n"
    assert "+hello Sir" in j.diff_text()
    rep = uj.undo(j)
    assert f.read_text() == "hello world\n"
    assert not (home / "notes" / "b.md").exists()
    assert len(rep.restored) == 2


def test_replace_missing_text_raises(env):
    home, _, ed, _ = env
    f = home / "a.txt"
    f.write_text("abc")
    with pytest.raises(fe.OpError):
        ed.replace(f, "zzz", "y")
    assert f.read_text() == "abc"


def test_move_delete_mkdir(env, tmp_path):
    home, _, ed, j = env
    src = home / "Downloads" / "inv.pdf"
    src.parent.mkdir()
    src.write_bytes(b"%PDF")
    dest = tmp_path / "data" / "Invoices"
    ed.mkdir(dest)
    ed.move(src, dest / "inv.pdf")
    junk = home / "Downloads" / "junk.tmp"
    junk.write_text("j")
    ed.delete(junk)
    assert (dest / "inv.pdf").read_bytes() == b"%PDF" and not junk.exists()
    uj.undo(j)
    assert src.read_bytes() == b"%PDF" and junk.read_text() == "j"
    assert not dest.exists()


def test_move_into_denied_refused(env):
    home, _, ed, _ = env
    src = home / "a.txt"
    src.write_text("a")
    (home / ".ssh").mkdir()
    with pytest.raises(fe.Denied):
        ed.move(src, home / ".ssh" / "a.txt")
    assert src.exists()


def test_not_writable_refused(env):
    home, _, ed, _ = env
    d = home / "ro"
    d.mkdir()
    f = d / "f.txt"
    f.write_text("x")
    os.chmod(d, 0o500)
    try:
        if os.access(d, os.W_OK):
            pytest.skip("running as a user that ignores modes")
        with pytest.raises(fe.Denied):
            ed.write_text(f, "y")
    finally:
        os.chmod(d, 0o700)


def test_apply_ops_and_verify(env, tmp_path):
    home, _, ed, _j = env
    a = home / "in" / "a.txt"
    a.parent.mkdir()
    a.write_text("A")
    dest = home / "out"
    ops = [
        {"op": "mkdir", "path": str(dest)},
        {"op": "move", "src": str(a), "dst": str(dest / "a.txt")},
        {"op": "write", "path": str(dest / "index.md"), "content": "a.txt\n"},
    ]
    events = []
    fe.apply_ops(ed, ops, on_event=lambda k, t: events.append(k))
    ok, evidence = fe.verify_ops(ops, before={str(a): uj.sha256_bytes(b"A")})
    assert ok, evidence
    assert "3 of 3" in evidence
    assert events.count("action") == 3
    # an outside change breaks verification
    (dest / "index.md").write_text("tampered")
    ok, evidence = fe.verify_ops(ops, before={str(a): uj.sha256_bytes(b"A")})
    assert not ok and "index.md" in evidence


def test_verify_expect_map(env):
    home, *_ = env
    f = home / "x"
    f.write_text("x")
    ok, _ = fe.verify_ops([], expect={str(f): "exists", str(home / "nope"): "absent"})
    assert ok
    ok, _ev = fe.verify_ops([], expect={str(f): uj.sha256_bytes(b"other")})
    assert not ok


def test_derive_ops_from_paths_dest(env):
    home, *_ = env
    ops = fe.derive_ops(
        {"paths": [str(home / "a.pdf"), str(home / "b.pdf")], "dest": str(home / "D")}
    )
    assert ops == [
        {"op": "mkdir", "path": str(home / "D")},
        {"op": "move", "src": str(home / "a.pdf"), "dst": str(home / "D" / "a.pdf")},
        {"op": "move", "src": str(home / "b.pdf"), "dst": str(home / "D" / "b.pdf")},
    ]
    assert fe.derive_ops({"ops": [{"op": "mkdir", "path": "/x"}]}) == [
        {"op": "mkdir", "path": "/x"}
    ]
    assert fe.derive_ops({}) == []


def test_unknown_op(env):
    _, _, ed, _ = env
    with pytest.raises(fe.OpError):
        fe.apply_ops(ed, [{"op": "chmod", "path": "/x"}])
