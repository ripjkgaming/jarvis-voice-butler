"""Native memory attribution: modification timestamps never imply factual truth."""
import memory


def test_native_recall_source_attributed_not_verified(monkeypatch, tmp_path):
    monkeypatch.setenv('JARVIS_HOME', str(tmp_path))
    monkeypatch.setenv('JARVIS_POWER_MEMORY_EVIDENCE', '1')
    vault = tmp_path / 'vault' / 'Memory'
    vault.mkdir(parents=True)
    (vault / 'Notes.md').write_text('Physics wave speed uses frequency and wavelength')
    result = memory.recall('physics waves speed', brain=lambda _q, _n: [])
    assert len(result) == 1
    entry = result[0]
    assert entry['source'] == 'Memory/Notes.md'
    assert entry['evidence_status'] == 'attributed_unverified'
    assert entry['contradiction_status'] == 'not_assessed'
    assert isinstance(entry['source_age_days'], float)
    assert 'SECRET' not in str(entry)


def test_provenance_stays_off_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv('JARVIS_HOME', str(tmp_path))
    monkeypatch.delenv('JARVIS_POWER_MEMORY_EVIDENCE', raising=False)
    vault = tmp_path / 'vault' / 'Memory'
    vault.mkdir(parents=True)
    (vault / 'Notes.md').write_text('Chemistry periodic tables')
    result = memory.recall('chemistry periodic', brain=lambda _q, _n: [])
    assert result[0] == {'source': 'Memory/Notes.md', 'text': 'Chemistry periodic tables'}
