"""Session-owned feature adapters. Only trusted SDK user events authorize a turn.

Host consent is explicit and expires. No source reads, captures, models or grants
occur at import/installation. Screen replies use isolated local OCR extraction by
default; a host-injected tool-less explain callback is a separate reviewed choice.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

from cancellation_control import classify_control
from screen_understanding import ScreenError, ScreenService, explicit_screen_request


@dataclass(frozen=True)
class HostConsent:
    screen_local_ocr: bool = False
    expires_at: float = 0
    librarian_grants: tuple[dict, ...] = field(default_factory=tuple)


def load_host_consent(path: str | None) -> HostConsent:
    """Read only an explicitly configured private owner consent file, never a scan."""
    if not path:
        return HostConsent()
    target = Path(path)
    if not target.is_absolute():
        raise ValueError("Consent path must be absolute")
    fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_size > 8192
        ):
            raise ValueError("Consent requires a private regular owner file")
        data = json.loads(os.read(fd, 8193))
    finally:
        os.close(fd)
    if (
        not isinstance(data, dict)
        or set(data)
        != {"version", "screen_local_ocr", "expires_at", "librarian_grants"}
        or type(data["version"]) is not int
        or data["version"] != 1
        or type(data["screen_local_ocr"]) is not bool
        or type(data["expires_at"]) not in (int, float)
        or not time.time() < data["expires_at"] <= time.time() + 10 * 365 * 86400
        or not isinstance(data["librarian_grants"], list)
        or len(data["librarian_grants"]) > 8
    ):
        raise ValueError("Invalid or expired explicit consent")
    return HostConsent(
        data["screen_local_ocr"], data["expires_at"], tuple(data["librarian_grants"])
    )


class FeatureSession:
    def __init__(
        self,
        session,
        *,
        task_tools=None,
        librarian=None,
        backend=None,
        authorized=None,
        explain=None,
        deliver=None,
    ):
        self.session = session
        self.task_tools = task_tools
        self.librarian = librarian
        self._owner = authorized or self._local_owner
        self.is_echo = lambda text: False
        self._expires = 0.0
        self._closed = False
        self._generation = 0
        self._seen = {}  # bounded IDs only; never transcripts or OCR
        self._tasks = set()
        self._screen_tasks = set()
        self._input_quarantine = False
        self._screen_release_enabled = os.environ.get("JARVIS_SCREEN_READING") == "1"
        self._handlers = []
        self._caption_quarantine = False
        self._tts = None
        self._caption_tail = None
        self._sensitive_depth = 0
        self._close_task = None
        self._grant_ids = set()
        self._explain = explain or self._extract
        self._deliver_override = deliver
        self.screen = ScreenService(
            backend,
            authorized=self.screen_authorized,
            explain=self._explain,
            deliver=self._deliver,
            sensitive=self.sensitive,
        )
        if os.environ.get("JARVIS_AUTO_APPROVE", "1") != "0":
            # Owner pre-approved screen reading (2026-10-30); revoke_authority still clears it.
            self._expires = time.time() + 10 * 365 * 86400
            self._screen_release_enabled = True
            self.screen.disclosure_accepted = True
        session.jarvis_features = self
        session.jarvis_screen_service = self.screen
        session.jarvis_screen_turn_id = None

    @staticmethod
    def _local_owner():
        from screen_understanding import local_owner_authorized

        return local_owner_authorized()

    def authorized(self):
        return not self._closed and self._owner()

    def screen_authorized(self):
        return self.authorized() and time.time() < self._expires

    @property
    def input_quarantined(self):
        return self._input_quarantine

    def stop_private_output(self):
        """Physical SDK/app stop path. Stops private output, grants no authority."""
        with contextlib.suppress(Exception):
            self.session.interrupt(force=True)
        self.revoke_turn()

    def _discard_quarantined_input(self, text, item=None, event=None):
        if not self._input_quarantine:
            return False
        if event is not None:
            event.transcript = ""
        if item is not None:
            item.content = ["[Private screen readback input withheld]"]
        with contextlib.suppress(Exception):
            self.session.clear_user_turn()
        control = classify_control(text)
        # Even an untrusted echoed stop may silence speech; it grants no effect,
        # approval, task cancellation or capture permission.
        if control is not None and control.action == "speech":
            self.stop_private_output()
        return True

    @property
    def captions_allowed(self):
        return not self._caption_quarantine

    @property
    def sensitive_active(self):
        return self._sensitive_depth > 0

    def bind_caption_tail(self, tail):
        self._caption_tail = tail

    def configure(self, consent: HostConsent, *, confirmed=False):
        """Host-only reviewed consent. Never exposed as a function tool."""
        if (
            confirmed is not True
            or not isinstance(consent, HostConsent)
            or not self._owner()
        ):
            raise ValueError("Explicit local owner consent is required")
        if (
            type(consent.screen_local_ocr) is not bool
            or not time.time() < consent.expires_at <= time.time() + 10 * 365 * 86400
        ):
            raise ValueError("Consent must expire within ten years")
        self.revoke_authority()
        self._expires = consent.expires_at
        # Only the bounded local extract implementation is approved by this bit.
        # A custom provider callback must be approved separately by its host.
        self.screen.disclosure_accepted = (
            consent.screen_local_ocr and self._screen_release_enabled
        )
        if self.librarian is not None:
            from librarian import Grant

            try:
                for fields in consent.librarian_grants:
                    allowed = {
                        "grant_id",
                        "source",
                        "scope",
                        "account",
                        "content",
                        "model_disclosure",
                        "sheet_range",
                        "retention_s",
                    }
                    if (
                        not isinstance(fields, dict)
                        or set(fields) - allowed
                        or fields.get("source") not in {"local", "drive"}
                        or type(fields.get("content", False)) is not bool
                        or type(fields.get("model_disclosure", False)) is not bool
                    ):
                        self.revoke_authority()
                        raise ValueError(
                            "Review one explicit local directory or direct-child Drive folder grant"
                        )
                    self.librarian.approve(
                        Grant(**fields, expires_at=consent.expires_at), confirmed=True
                    )
                    self._grant_ids.add(fields["grant_id"])
            except Exception:
                self.revoke_authority()
                raise

    def revoke_turn(self):
        self._generation += 1
        self.session.jarvis_screen_turn_id = None
        if self.screen._active is not None:
            with contextlib.suppress(Exception):
                self.session.interrupt(force=True)
        self.screen.revoke()
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        for task in tuple(self._screen_tasks):
            if task is not current and not task.done():
                task.cancel()

    def revoke_authority(self):
        self.revoke_turn()
        self.screen.disclosure_accepted = False
        if self.librarian is not None:
            # No inherited source scopes after guest/private/revocation.
            self.librarian.revoke_all()
            self._grant_ids.clear()

    async def _extract(self, question, evidence):
        # No model, tools, session history, image upload or persistence.
        text = evidence.text[:700]
        explanation = ""
        if "connection refused" in text.casefold():
            explanation = " This often means the destination service is not accepting connections; the snapshot does not establish why."
        elif "permission denied" in text.casefold():
            explanation = " Access was denied for that operation; the snapshot does not identify the permission policy."
        return (
            "The selected window says: "
            + text
            + (" (text truncated)" if evidence.truncated else "")
            + explanation
        )

    @contextlib.asynccontextmanager
    async def sensitive(self):
        self._caption_quarantine = True
        if self._caption_tail is None:
            raise ScreenError("Private caption suppression is not installed")
        output = getattr(self.session, "output", None)
        if output is None or not callable(
            getattr(output, "set_transcription_enabled", None)
        ):
            raise ScreenError("Private screen speech output is unavailable")
        input_stream = getattr(self.session, "input", None)
        if input_stream is None or not callable(
            getattr(input_stream, "set_audio_enabled", None)
        ):
            raise ScreenError("Private microphone gating is unavailable")
        # Gate upstream audio before the first private speech frame. SDK callbacks
        # lack a bounded late-transcript identity, so input stays off this session.
        # Physical app/SDK stop and close remain available; a new session restores
        # voice input. Do not use OCR/echo to grant re-enable or task authority.
        self._input_quarantine = True
        input_stream.set_audio_enabled(False)
        self.session.clear_user_turn()
        # Disable the SDK transcription chain itself, not only the HUD tail.
        output.set_transcription_enabled(False)
        if self._caption_tail is not None:
            self._caption_tail.clear_sensitive()
        self._sensitive_depth += 1
        try:
            yield
        finally:
            self._sensitive_depth -= 1
            self.session.jarvis_screen_turn_id = None
            # Keep assistant captions/transcription disabled until session close.
            # SDK text callbacks have no reply ID; restoring at scope exit could
            # publish a queued sensitive chunk. Audio remains usable.
            output.set_transcription_enabled(False)
            if self._caption_tail is not None:
                self._caption_tail.clear_sensitive()

    async def _deliver(self, text):
        if self._deliver_override is not None:
            await self._deliver_override(text)
            return
        # Supplying local audio bypasses Jarvis's realtime say->generate_reply
        # fallback, which would otherwise put sensitive instructions in history.
        if self._tts is None:
            from local_voice import PiperTTS

            self._tts = PiperTTS(strict_sentence_audio=True)
        emitted = 0

        async def audio():
            nonlocal emitted
            async with self._tts.synthesize(text) as stream:
                async for chunk in stream:
                    emitted += chunk.frame.samples_per_channel
                    yield chunk.frame

        speech = self.session.say(
            text, audio=audio(), add_to_chat_ctx=False, allow_interruptions=True
        )
        try:
            await speech.wait_for_playout()
        except asyncio.CancelledError:
            # Sensitive scope must not finish while owned speech can still play.
            with contextlib.suppress(Exception):
                speech.interrupt()
            drain = asyncio.create_task(speech.wait_for_playout())
            while not drain.done():
                try:
                    await asyncio.shield(drain)
                except asyncio.CancelledError:
                    continue
            with contextlib.suppress(Exception):
                drain.result()
            raise
        if speech.interrupted or speech.exception() is not None or emitted == 0:
            raise ScreenError("Screen reply was interrupted or unavailable")

    def install(self, *, realtime=False):
        self._realtime = realtime

        def transcript(event):
            if self._discard_quarantined_input(
                getattr(event, "transcript", ""), event=event
            ):
                return
            if not self.authorized():
                self.revoke_authority()
                return
            if not getattr(event, "is_final", False):
                return  # no VAD/interim task control or screen enrollment
            if not realtime:
                self.revoke_turn()  # authoritative ChatMessage hook owns text turns
                return
            self._schedule(
                getattr(event, "item_id", None), getattr(event, "transcript", "")
            )

        def item(event):
            message = getattr(event, "item", None)
            if getattr(
                message, "role", None
            ) == "user" and self._discard_quarantined_input(
                getattr(message, "text_content", ""), item=message
            ):
                return
            if realtime and getattr(message, "role", None) == "user":
                self._schedule(
                    getattr(message, "id", None), getattr(message, "text_content", "")
                )

        def closed(event):
            self.revoke_authority()
            if self._close_task is None:
                self._close_task = asyncio.create_task(self._shutdown())

        for name, handler in [
            ("user_input_transcribed", transcript),
            ("conversation_item_added", item),
            ("close", closed),
        ]:
            self.session.on(name, handler)
            self._handlers.append((name, handler))

    def _schedule(self, turn_id, text):
        # A provider with no correlated input ID cannot authorize side effects.
        if self._discard_quarantined_input(text):
            return
        if self.is_echo(text):
            return
        if not turn_id:
            self.revoke_turn()
            return
        if turn_id in self._seen:
            return
        # Reserve synchronously before mirrored event callbacks can run.
        control = classify_control(text)
        screen_request = explicit_screen_request(text)
        consumed = bool(control or screen_request)
        self._seen[turn_id] = consumed
        if len(self._seen) > 256:
            self.revoke_authority()
            self._closed = True
            return
        self.revoke_turn()
        generation = self._generation
        if not consumed:
            return
        with contextlib.suppress(Exception):
            self.session.interrupt(force=True)
        with contextlib.suppress(Exception):
            self.session.clear_user_turn()
        self._start_dispatch(turn_id, text, generation)

    def _start_dispatch(self, turn_id, text, generation):
        task = asyncio.create_task(self._dispatch(turn_id, text, generation))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        if classify_control(text) is None:
            self._screen_tasks.add(task)
            task.add_done_callback(self._screen_tasks.discard)
        task.add_done_callback(
            lambda done: None if done.cancelled() else done.exception()
        )
        return task

    async def handle_message(self, message):
        if getattr(message, "role", None) != "user":
            return False
        turn_id = getattr(message, "id", None)
        text = getattr(message, "text_content", "") or ""
        if self._discard_quarantined_input(text, item=message):
            return True
        if self.is_echo(text):
            return True
        if not self.authorized():
            self.revoke_authority()
            return False
        if not turn_id:
            return False
        if turn_id in self._seen:
            return self._seen[turn_id]
        consumed = bool(classify_control(text) or explicit_screen_request(text))
        self._seen[turn_id] = consumed
        if len(self._seen) > 256:
            self.revoke_authority()
            return consumed
        self.revoke_turn()
        if consumed:
            await self._start_dispatch(turn_id, text, self._generation)
        return consumed

    async def _say_safe(self, text):
        speech = self.session.say(text, add_to_chat_ctx=False)
        await speech.wait_for_playout()

    async def _dispatch(self, turn_id, text, generation):
        if generation != self._generation or not self.authorized():
            return
        control = classify_control(text)
        if control is not None:
            with contextlib.suppress(Exception):
                self.session.interrupt(force=True)
            if control.action == "speech":
                return
            if control.action == "task" and self.task_tools is not None:
                import action_guard

                try:
                    with action_guard.request_scope(turn_id):
                        receipt = await self.task_tools.cancel_task(
                            None, task_id=control.target
                        )
                except Exception:
                    receipt = {
                        "say": "Task control is unavailable; check task status before retrying."
                    }
                if generation == self._generation and self.authorized():
                    await self._say_safe(receipt["say"])
            else:
                await self._say_safe(
                    "Name the owned operation ID to cancel; no work was changed."
                )
            return
        if not self.screen.disclosure_accepted or time.time() >= self._expires:
            await self._say_safe(
                "Screen reading needs explicit local OCR and private speech consent. Use the owner feature-consent command."
            )
            return
        try:
            # New explicit turns revoke old inspection; settle its cleanup before
            # enrolling a replacement, without permanently closing the service.
            active = self.screen._active
            if active is not None and active is not asyncio.current_task():
                await asyncio.gather(active, return_exceptions=True)
            if generation != self._generation or not self.authorized():
                return
            if (
                await self.screen.enroll(turn_id, text)
                and generation == self._generation
                and self.authorized()
            ):
                self.session.jarvis_screen_turn_id = turn_id
                await self.screen.inspect(turn_id, text)
        except asyncio.CancelledError:
            if generation == self._generation:
                raise
        except ScreenError:
            # Fixed safe failure only; no OCR/backend exception sent to context.
            if generation == self._generation and self.authorized():
                await self._say_safe(
                    "Screen reading stopped or is unavailable. Ask again."
                )
        finally:
            self.session.jarvis_screen_turn_id = None

    async def drain(self):
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def close(self):
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._shutdown())
        await asyncio.shield(self._close_task)

    async def _shutdown(self):
        self.revoke_authority()
        self._closed = True
        for name, handler in self._handlers:
            with contextlib.suppress(Exception):
                self.session.off(name, handler)
        self._handlers.clear()
        for task in tuple(self._tasks):
            task.cancel()
        await self.drain()
        await self.screen.close()
        if self._tts is not None:
            await self._tts.aclose()
        if self.librarian is not None:
            self.librarian.close()
