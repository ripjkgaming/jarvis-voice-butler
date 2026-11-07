import agent
import character_runtime
import characters


def test_only_jarvis_is_available_even_with_stale_state(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_CHARACTER_STATE", str(tmp_path / "state.json"))
    (tmp_path / "state.json").write_text(
        '{"active":"ultron","target":"friday","phase":"idle"}'
    )
    rt = character_runtime.CharacterRuntime()
    assert list(rt.chars) == ["jarvis"]
    assert rt.active_id() == "jarvis"
    assert rt.snapshot()["target"] is None
    for name in ("friday", "ultron", "jarvis"):
        assert rt.switch(name)["switched"] is False
    assert rt.handle_text("switch to ultron") is None


def test_no_switch_tool_or_alternate_instructions():
    assert not hasattr(agent.Assistant, "switch_character")
    assert agent._extra_names() == frozenset()
    assert set(characters.load_all()) == {"jarvis"}
