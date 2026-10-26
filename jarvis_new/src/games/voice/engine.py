"""Chess Voice Engine: mic -> VAD -> local whisper -> parser -> local Piper.

It never uses LiveKit or Gemini. "Hey Jarvis" always wins: when the wake
event fires (or the transcript starts with it) the engine goes deaf, cuts
Piper and cancels in-flight work, and resumes 1.5 s after the call ends.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from games.chess import commands, text_norm
from games.voice.vad import FRAME, RATE, Endpointer, Utterance, make_vad

_HALLUCINATIONS = {
    "you", "thank you", "thanks", "thanks for watching", "bye", "the end",
    "thank you for watching", "so", "uh", "um", "hmm", "okay", "ok", "oh", "yeah",
}  # fmt: skip
_RESUME_WORDS = {"resume chess", "back to chess", "resume the game", "resume"}


@dataclass
class Timing:
    vad_end: float = 0.0
    text_ms: float = 0.0
    total_ms: float = 0.0


class VoiceEngine:
    def __init__(
        self,
        game,
        *,
        stt,
        speaker,
        capture=None,
        vad=None,
        watcher=None,
        endpointer: Endpointer | None = None,
        chime: bool = True,
    ):
        self.game = game
        self.stt = stt
        self.speaker = speaker
        self.capture = capture
        self.watcher = watcher
        s = game.settings
        self.ep = endpointer or Endpointer(
            vad or make_vad(), end_silence_ms=s.end_silence_ms
        )
        self.paused = False  # Jarvis owns the mic
        self.resume_only = False  # 10 min deaf: only "resume chess" gets through
        self.running = False
        self._q: asyncio.Queue[Utterance] = asyncio.Queue(maxsize=8)
        self._speech_q: asyncio.Queue[str] = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self._barge_held = False
        self._epoch = 0  # bumped on wake so in-flight transcripts are discarded
        self.last_timing = Timing()
        self.chime = chime
        self.transcripts: list[str] = []  # tests / debugging
        self.dropped: list[tuple[str, str]] = []
        self.on_chime = None

    # ------------------------------------------------------------ lifecycle
    async def start(self, *, load_models: bool = True) -> None:
        loop = asyncio.get_running_loop()
        if load_models:
            await asyncio.to_thread(self.stt.load)
            await asyncio.to_thread(self._warm_speaker)
        g = self.game
        g.on_say = self.say
        g.on_stop_speech = self.cancel_speech
        g.user_speaking = self._user_is_speaking
        g.listening = True
        g.mic_state = "listening"
        self.running = True
        self._tasks = [
            asyncio.ensure_future(self._worker()),
            asyncio.ensure_future(self._speech_worker()),
        ]
        if self.capture is not None:
            self.capture.on_frame = self.on_frame
            self.capture.start(loop)
        if self.watcher is not None:
            self.watcher.on_wake = self.on_wake
            self.watcher.on_resume = self.on_resume
            self.watcher.on_deaf_limit = self.on_deaf_limit
            self.watcher.start()
        g._changed_soon()

    def _warm_speaker(self) -> None:
        try:
            self.speaker.load()
            self.speaker.warm_cache()
        except Exception:
            pass

    async def stop(self) -> None:
        self.running = False
        self.game.listening = False
        self.game.mic_state = "off"
        if self.capture is not None:
            self.capture.stop()
        if self.watcher is not None:
            await self.watcher.stop()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self.speaker.close()

    def _user_is_speaking(self) -> bool:
        """Share live VAD state with takeover, including endpointing silence."""
        return self.running and not self.paused and self.ep.in_speech

    # --------------------------------------------------------------- audio in
    def on_frame(self, frame: np.ndarray, t: float) -> None:
        """Called per 32 ms mic frame on the event loop."""
        if not self.running or self.paused:
            return
        was = self.ep.in_speech
        utt = self.ep.feed(frame, t)
        if self.ep.in_speech and not was:
            self._speech_started(frame)
        if utt is not None:
            with contextlib.suppress(asyncio.QueueFull):
                self._q.put_nowait(utt)
            self.game.mic_state = "thinking"
            self.game._changed_soon()

    def _speech_started(self, frame: np.ndarray) -> None:
        self.game.mic_state = "hearing"
        if self.speaker.speaking:
            gate_ok = True
            if self.game.settings.echo_gate:
                mic = float(np.sqrt(np.mean(frame * frame)))
                gate_ok = mic > self.speaker.out_rms * 1.5
            if gate_ok:
                self.speaker.hold(True)  # duck until we know if it is "stop"
                self._barge_held = True
        self.game._changed_soon()

    def feed_audio(self, audio: np.ndarray, t0: float | None = None) -> None:
        """Push mono 16 kHz audio through the pipeline (tests, file input)."""
        t = t0 if t0 is not None else time.monotonic()
        n = audio.size // FRAME
        for i in range(n):
            self.on_frame(audio[i * FRAME : (i + 1) * FRAME], t + i * FRAME / RATE)

    # ------------------------------------------------------------ wake / pause
    def on_wake(self) -> None:
        """ "Hey Jarvis": deaf within 150 ms. Sync work only."""
        self.paused = True
        self._epoch += 1
        self.ep.drop()
        while not self._q.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._q.get_nowait()
        self.cancel_speech()
        self.game.jarvis_wake()

    def on_resume(self) -> None:
        self.paused = False
        self.resume_only = False
        self.ep.reset()
        self.game.jarvis_done()
        if self.chime and self.on_chime:
            self.on_chime()

    def on_deaf_limit(self) -> None:
        self.resume_only = True
        self.game.banner = "Say 'resume chess' or press Space"
        self.game._changed_soon()

    def manual_resume(self) -> None:
        """Space key / button: end the pause regardless of what Jarvis is doing."""
        if self.watcher is not None:
            self.watcher.resume_now()
        elif self.paused:
            self.on_resume()

    # -------------------------------------------------------------- speaking
    async def say(self, text: str) -> None:
        if text and not self.paused:
            await self._speech_q.put(text)

    def cancel_speech(self) -> None:
        while not self._speech_q.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._speech_q.get_nowait()
        self.speaker.stop()
        self._barge_held = False
        self.game.speaking = False

    async def _speech_worker(self) -> None:
        while True:
            text = await self._speech_q.get()
            epoch = self._epoch
            while self._user_is_speaking():
                await asyncio.sleep(0.02)
            if self.paused or epoch != self._epoch:
                continue
            self.game.speaking = True
            self.game._changed_soon()
            try:
                await self.speaker.speak(text)
            except Exception:
                pass
            finally:
                self.game.speaking = self.speaker.speaking or not self._speech_q.empty()
                self.game.mic_state = "paused" if self.paused else "listening"
                self.game._changed_soon()

    # --------------------------------------------------------------- pipeline
    async def _worker(self) -> None:
        while True:
            utt = await self._q.get()
            epoch = self._epoch
            try:
                await self._process(utt, epoch)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            finally:
                if self._barge_held:
                    self.speaker.hold(False)
                    self._barge_held = False
                if not self.paused:
                    self.game.mic_state = "listening"
                    self.game._changed_soon()

    def _character_switch(self, text: str) -> bool:
        """"switch to Ultron" (any source): run it, never hand it to chess."""
        try:
            try:
                from character_runtime import get_runtime
                from switch_conductor import start_switch
            except ImportError:
                from src.character_runtime import get_runtime
                from src.switch_conductor import start_switch
            res = get_runtime().handle_text(text, "voice")
            if res is None:
                return False
            start_switch(res, "voice")
            return True
        except Exception:
            return False

    def _accept(self, text: str, utt: Utterance) -> str | None:
        """Gate a transcript; returns the (possibly de-prefixed) text or None."""
        clean = text_norm.clean(text)
        if not clean:
            return None
        if clean in _HALLUCINATIONS and not commands.is_stop(clean):
            self.dropped.append((text, "noise"))
            return None
        if text_norm.is_jarvis_wake(text):
            return text  # handled by caller as a wake backstop ("Hey Jarvis" wins)
        stop_like = (
            commands.is_stop(text) or commands.match_narrow_stop(text) is not None
        )
        # Self-echo guard: anything overlapping our own speech is dropped,
        # unless it is a stop phrase.
        if self.speaker.overlaps(utt.start, utt.end) and not stop_like:
            self.dropped.append((text, "echo"))
            return None
        if self._character_switch(text):
            return None  # a switch phrase is never a chess command
        if text_norm.is_wake_phrase(text):
            return text  # another character's wake name: same backstop
        if self.resume_only:
            return text if text_norm.strip_wrappers(text) in _RESUME_WORDS else None
        if self.game.settings.require_prefix and not stop_like:
            stripped = clean
            if not stripped.startswith("chess"):
                self.dropped.append((text, "no-prefix"))
                return None
            return re.sub(r"^chess\b[ ,]*", "", text.strip(), flags=re.I) or None
        return text

    async def _process(self, utt: Utterance, epoch: int) -> None:
        t0 = time.monotonic()
        text = await self.stt.transcribe(utt.audio)
        t1 = time.monotonic()
        self.last_timing = Timing(utt.end, (t1 - t0) * 1000, (t1 - utt.end) * 1000)
        if epoch != self._epoch or self.paused:
            return  # Jarvis woke while we were listening: discard
        self.transcripts.append(text)
        accepted = self._accept(text, utt)
        if accepted is None:
            return
        if text_norm.is_wake_phrase(accepted):
            # whisper caught "hey Jarvis" before the wake client did
            if self.watcher is not None:
                self.watcher.wake()
            else:
                self.on_wake()
            return
        if commands.is_stop(accepted) or commands.match_narrow_stop(accepted):
            self.speaker.stop()  # within 300 ms of the transcript, regardless
            self._barge_held = False
        elif self.resume_only:
            self.manual_resume()
            return
        self.game.heard = accepted
        self.game.mic_state = "thinking"
        reply = await self.game.handle_text(accepted)
        if epoch != self._epoch or self.paused:
            return
        if reply.say:
            await self.say(reply.say)
        self.game._changed_soon()

    # -------------------------------------------------------------- diagnostics
    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "paused": self.paused,
            "speaking": self.speaker.speaking,
            "last_text_ms": round(self.last_timing.text_ms),
            "last_total_ms": round(self.last_timing.total_ms),
        }
