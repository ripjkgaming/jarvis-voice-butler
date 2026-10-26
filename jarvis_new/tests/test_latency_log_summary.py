import importlib.util
from pathlib import Path


def test_offline_summary_excludes_zero_fabrication_content_and_summary_duplicates(
    tmp_path,
):
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "summarize_voice_latency.py"
    )
    spec = importlib.util.spec_from_file_location("latency_log_summary", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    log = tmp_path / "actions.log"
    log.write_text(
        "date other vad_end_to_agent_speaking_s=999\n"
        "date latency voice_observed agent_speaking_before_vad_end=true\n"
        "date latency voice_observed vad_end_to_agent_speaking_s=2\n"
        "date latency voice_observed vad_end_to_agent_speaking_s=4\n"
        "date latency summary metric=vad_end_to_agent_speaking_s n=2 p50_s=2\n"
        "date latency sdk_realtime provider_generation_to_first_audio_s=0.3 private-text\n"
    )
    result = module.summarize(log)
    assert result["metrics"]["vad_end_to_agent_speaking_s"] == {
        "n": 2,
        "p50_s": 2,
        "p95_s": 4,
    }
    assert result["metrics"]["vad_end_to_verified_workflow_s"] == {
        "n": 0,
        "p50_s": None,
        "p95_s": None,
    }
    assert result["overlap_observations_in_log"] == 1
    assert "private-text" not in str(result)
