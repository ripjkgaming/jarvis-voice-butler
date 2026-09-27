"""KWin window control for Plasma Wayland (wmctrl is blind there)."""

import types

from system import kwin_windows as K

REPLY = '''method return time=1 sender=:1.9 -> destination=:1.2 serial=5 reply_serial=2
   array [
      struct {
         string "0_{4116ebc8-2cd7-461f-82ba-2bac6dc75932}"
         string "Finished projects — Dolphin"
         string "org.kde.dolphin"
         uint32 100
         double 0.7
      }
      struct {
         string "0_{d8b72e34-47fd-4b9a-afc3-825e4dc72628}"
         string "house party playlist - YouTube - Brave"
         string ""
         uint32 100
         double 0.6
      }
   ]
'''


def test_parse_matches():
    got = K.parse_matches(REPLY)
    assert got[0] == ("{4116ebc8-2cd7-461f-82ba-2bac6dc75932}", "Finished projects — Dolphin")
    assert got[1][1].startswith("house party")
    assert K.parse_matches("") == []


def test_act_closes_best_match_with_action_prefix():
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        out = REPLY if argv[-1].startswith("string:") and "Match" in argv[-2] else ""
        return types.SimpleNamespace(returncode=0, stdout=out)

    title, n = K.act("close", "dolphin", run=run)
    assert title.endswith("Dolphin") and n == 2
    assert calls[-1][-2] == "string:1_{4116ebc8-2cd7-461f-82ba-2bac6dc75932}"
    assert K.act("close", "", run=run) is None


def test_available_only_on_plasma_wayland():
    assert K.available({"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "KDE"})
    assert not K.available({"WAYLAND_DISPLAY": "", "XDG_CURRENT_DESKTOP": "KDE"})
    assert not K.available({"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "GNOME"})
