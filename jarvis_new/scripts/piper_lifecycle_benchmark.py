#!/usr/bin/env python3
"""Compare Piper lifecycle work with a private fake model; never plays audio.

Run from jarvis_new with uv run scripts/piper_lifecycle_benchmark.py
--baseline-ref <commit> --output benchmarks/<result>.json. The baseline is read
with git show; neither the checkout nor any installed voice model is modified.
These are deterministic work counts, not real synthesis latency measurements.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import types
from pathlib import Path


class Emitter:
    def initialize(self, **_kwargs):
        pass

    def push(self, _pcm):
        pass

    def flush(self):
        pass


class FakeModel:
    def __init__(self):
        self.calls = []
        self.loads = 0
        self.active = 0
        self.peak = 0
        self.release = threading.Event()
        self.release_load = threading.Event()
        self.release_load.set()
        self.loading = threading.Event()

    def load(self, _path):
        self.loads += 1
        self.loading.set()
        if not self.release_load.wait(5):
            raise RuntimeError("fake load was not released")
        return types.SimpleNamespace(
            config=types.SimpleNamespace(sample_rate=16000), synthesize=self.synthesize
        )

    def synthesize(self, text, **_kwargs):
        self.calls.append(text)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if not self.release.wait(5):
                raise RuntimeError("fake inference was not released")
            yield types.SimpleNamespace(audio_int16_bytes=b"\x01\x02")
            yield types.SimpleNamespace(audio_int16_bytes=b"\x03\x04")
        finally:
            self.active -= 1

    def install(self):
        sys.modules["piper"] = types.SimpleNamespace(
            PiperVoice=types.SimpleNamespace(load=self.load),
            SynthesisConfig=lambda **kwargs: types.SimpleNamespace(**kwargs),
        )


async def until(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(poll(), 2)


async def render(module, plugin, text):
    stream = types.SimpleNamespace(_tts=plugin, _input_text=text)
    await module._PiperChunkedStream._run(stream, Emitter())


async def cancel(task):
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def interruption_probe(module, path):
    model = FakeModel()
    model.install()
    plugin = module.PiperTTS(model_path=path)
    tasks = []
    try:
        first = asyncio.create_task(render(module, plugin, "Old."))
        tasks.append(first)
        await until(lambda: model.active == 1)
        await cancel(first)
        for index in range(4):
            queued = asyncio.create_task(render(module, plugin, f"Obsolete {index}."))
            tasks.append(queued)
            await asyncio.sleep(0.05)
            await cancel(queued)
        current = asyncio.create_task(render(module, plugin, "Current."))
        tasks.append(current)
        await asyncio.sleep(0.05)
        active_before_release = model.active
        model.release.set()
        await current
        await until(lambda: model.active == 0)
        return {
            "cancelled_utterances": 5,
            "current_utterances": 1,
            "native_workers_before_release": active_before_release,
            "peak_concurrent_inferences": model.peak,
            "native_inference_calls": len(model.calls),
            "obsolete_queued_inferences": sum(
                text.startswith("Obsolete") for text in model.calls
            ),
            "model_loads": model.loads,
        }
    finally:
        model.release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await until(lambda: model.active == 0)
        await plugin.aclose()


async def preload_probe(module, path):
    model = FakeModel()
    model.install()
    model.release.set()
    model.release_load.clear()
    plugin = module.PiperTTS(model_path=path)
    tasks = [asyncio.create_task(render(module, plugin, "First."))]
    try:
        await until(model.loading.is_set)
        tasks.append(asyncio.create_task(render(module, plugin, "Second.")))
        await asyncio.sleep(0.05)
        model.release_load.set()
        await asyncio.gather(*tasks)
        return {"simultaneous_callers": 2, "model_loads": model.loads}
    finally:
        model.release_load.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await plugin.aclose()


async def close_probe(module, path):
    model = FakeModel()
    model.install()
    plugin = module.PiperTTS(model_path=path)
    task = asyncio.create_task(render(module, plugin, "Old."))
    closer = None
    try:
        await until(lambda: model.active == 1)
        await cancel(task)
        closer = asyncio.create_task(plugin.aclose())
        await asyncio.sleep(0.05)
        return {
            "native_workers_before_release": model.active,
            "close_returned_before_native_completion": closer.done(),
            "model_reference_cleared_before_native_completion": plugin._voice is None,
        }
    finally:
        model.release.set()
        await asyncio.gather(
            task, *([closer] if closer else []), return_exceptions=True
        )
        await until(lambda: model.active == 0)
        await plugin.aclose()


async def compare(sources):
    results = {}
    previous = sys.modules.get("piper")
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-piper-benchmark-") as workspace:
            path = Path(workspace) / "fake.onnx"
            path.touch()
            for name, source in sources.items():
                module = types.ModuleType(f"piper_benchmark_{name}")
                exec(
                    compile(source, f"<{name}-local_voice.py>", "exec"), module.__dict__
                )
                results[name] = {
                    "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                    "interruption": await interruption_probe(module, path),
                    "concurrent_preload": await preload_probe(module, path),
                    "close": await close_probe(module, path),
                }
    finally:
        if previous is None:
            sys.modules.pop("piper", None)
        else:
            sys.modules["piper"] = previous
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    baseline = subprocess.run(
        ["git", "show", f"{args.baseline_ref}:jarvis_new/src/local_voice.py"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout
    results = {
        "benchmark": "Piper fake-native lifecycle",
        "baseline_ref": args.baseline_ref,
        "limits": "Fake model only; no audio device, model weights, network or real synthesis latency measured.",
        **asyncio.run(
            compare(
                {
                    "before": baseline,
                    "after": (project / "src/local_voice.py").read_text(),
                }
            )
        ),
    }
    output = json.dumps(results, indent=2) + "\n"
    if args.output:
        args.output.write_text(output)
    print(output, end="")


if __name__ == "__main__":
    main()
