from __future__ import annotations

import dataclasses

import pytest

from command_parse import SwitchCmd, parse_switch

NAMES = {
    "jarvis": ("jarvis",),
    "ultron": ("ultron",),
    "friday": ("friday",),
}


def sw(t: str) -> SwitchCmd:
    return SwitchCmd("switch", t, None)


def unk(raw: str) -> SwitchCmd:
    return SwitchCmd("unknown", None, raw)


CASES: list[tuple[str, SwitchCmd | None]] = [
    ("switch to ultron", sw("ultron")),
    ("switch to jarvis", sw("jarvis")),
    ("switch to friday", sw("friday")),
    ("Switch to Ultron.", sw("ultron")),
    ("please switch me to ultron", sw("ultron")),
    ("hey switch to friday", sw("friday")),
    ("okay switch to jarvis", sw("jarvis")),
    ("ok change to ultron", sw("ultron")),
    ("can you switch to friday", sw("friday")),
    ("could you swap to ultron", sw("ultron")),
    ("would you switch me over to jarvis", sw("jarvis")),
    ("change to friday", sw("friday")),
    ("swap to jarvis", sw("jarvis")),
    ("swap me to ultron", sw("ultron")),
    ("switch us to friday", sw("friday")),
    ("switch over to ultron", sw("ultron")),
    ("switch back to jarvis", sw("jarvis")),
    ("go to ultron", sw("ultron")),
    ("go back to jarvis", sw("jarvis")),
    ("go back to friday", sw("friday")),
    ("take me to ultron", sw("ultron")),
    ("take me over to friday", sw("friday")),
    ("jarvis, switch to ultron", sw("ultron")),
    ("jarvis switch to friday", sw("friday")),
    ("hey jarvis, switch to ultron", sw("ultron")),
    ("friday, please switch me to jarvis", sw("jarvis")),
    ("Friday take over", sw("friday")),
    ("ultron, take over", sw("ultron")),
    ("jarvis take over", sw("jarvis")),
    ("let ultron take over", sw("ultron")),
    ("let friday take over", sw("friday")),
    ("please let jarvis take over", sw("jarvis")),
    ("put ultron on", sw("ultron")),
    ("put friday on", sw("friday")),
    ("bring back jarvis", sw("jarvis")),
    ("bring back ultron", sw("ultron")),
    ("/ultron", sw("ultron")),
    ("/ULTRON", sw("ultron")),
    ("/jarvis", sw("jarvis")),
    ("/friday", sw("friday")),
    # typos
    ("swich to ultorn", sw("ultron")),
    ("swtich to jarvis", sw("jarvis")),
    ("chnage to friday", sw("friday")),
    ("switch to ultrn", sw("ultron")),
    ("switch to jarvs", sw("jarvis")),
    ("switch to fridai", sw("friday")),
    ("switch to jarviss", sw("jarvis")),
    ("brng back jarvis", sw("jarvis")),
    ("fridya take over", sw("friday")),
    # unknown targets
    ("switch to vision", unk("vision")),
    ("change to hal", unk("hal")),
    ("please swap to optimus prime", unk("optimus prime")),
    # must be None
    ("/vision", None),
    ("/", None),
    ("switch to ultron?", None),
    ("can you switch to ultron?", None),
    ("tell me about ultron", None),
    ("who is friday", None),
    ("why did ultron switch", None),
    ("how do i switch to ultron", None),
    ("what is jarvis", None),
    ("explain ultron", None),
    ("describe friday", None),
    ("about ultron", None),
    ("is ultron there", None),
    ("are you jarvis", None),
    ("does friday switch to ultron", None),
    ("did jarvis go to ultron", None),
    ("when does ultron take over", None),
    ("where is friday", None),
    ("ultron was a villain", None),
    ("i like jarvis", None),
    ("jarvis", None),
    ("ultron", None),
    ("switch the lights to red", None),
    ("change the channel to friday night", None),
    ("switch to the next song", None),
    ("go to sleep", None),
    ("go to the store", None),
    ("take me to the airport", None),
    ("switch", None),
    ("", None),
    ("   ", None),
    ("play some music", None),
    ("switch to ultron and then tell me the weather and also play music now", None),
    ("change the volume", None),
    ("tell jarvis to switch to ultron", None),
]


@pytest.mark.parametrize(("text", "expected"), CASES)
def test_parse_switch(text: str, expected: SwitchCmd | None) -> None:
    assert parse_switch(text, NAMES) == expected


def test_case_count() -> None:
    assert len(CASES) >= 60


def test_multiple_aliases() -> None:
    names = {"ultron": ("ultron", "ult"), "jarvis": ("jarvis", "jay")}
    assert parse_switch("switch to ult", names) == sw("ultron")
    assert parse_switch("/jay", names) == sw("jarvis")


def test_dataclass_frozen() -> None:
    cmd = sw("ultron")
    with pytest.raises(dataclasses.FrozenInstanceError):
        cmd.target = "x"  # type: ignore[misc]


@pytest.mark.parametrize(
    "text, target",
    [
        ("swap to friday please", "friday"),
        ("switch to ultron now", "ultron"),
        ("go back to jarvis sir", "jarvis"),
    ],
)
def test_trailing_filler(text: str, target: str) -> None:
    cmd = parse_switch(text, NAMES)
    assert cmd is not None and cmd.kind == "switch" and cmd.target == target
