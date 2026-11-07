"""Durable WhatsApp drafts and a per-private-phone spoken send gate.

Approval enters only from installed SDK user-input callbacks, never a model
tool argument. Recipient DOM is data. One reviewed revision gets one attempt.
SDK contract: https://docs.livekit.io/reference/agents/events/
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path

CONFIRM_TTL = 60.0


def revision(row):
    data = {k: row.get(k) for k in ("id", "chat", "text", "image", "ts")}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _rows(path):
    try:
        rows = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError("WhatsApp draft queue is unreadable; it was preserved.")
    out = []
    for i, raw in enumerate(rows):
        row = dict(raw)
        # Reading legacy entries is non-mutating and gives identical entries
        # distinct, stable IDs. Existing history is never treated as approval.
        row.setdefault(
            "id",
            "legacy-"
            + hashlib.sha256(
                (str(i) + json.dumps(raw, sort_keys=True)).encode()
            ).hexdigest()[:20],
        )
        row.setdefault("status", "pending")
        row["revision"] = revision(row)
        out.append(row)
    return out


@contextlib.contextmanager
def _locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _write(path, rows):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".wa-draft-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(rows, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


def pending(path):
    return [r for r in _rows(Path(path)) if r["status"] == "pending"]


def queue(path, chat, text, *, request_id=None, image=None):
    path = Path(path)
    with _locked(path):
        rows = _rows(path)
        if request_id:
            duplicate = next(
                (
                    r
                    for r in rows
                    if r.get("request_id") == request_id
                    and r.get("chat") == chat
                    and r.get("text") == text
                    and r.get("image") == image
                ),
                None,
            )
            if duplicate is not None:
                return duplicate
        row = {
            "id": uuid.uuid4().hex,
            "chat": chat,
            "text": text,
            "ts": time.time(),
            "status": "pending",
        }
        if image is not None:
            from wa_image import verify_image

            if not verify_image(image):
                raise ValueError("Image changed or is unavailable; capture it again.")
            row["image"] = image
        if request_id:
            row["request_id"] = request_id
        row["revision"] = revision(row)
        rows.append(row)
        _write(path, rows)
        return row


def _get(path, draft_id):
    hits = [r for r in _rows(Path(path)) if r["id"] == draft_id]
    if len(hits) != 1 or hits[0]["status"] != "pending":
        raise ValueError("That WhatsApp draft is not pending. List drafts again.")
    return hits[0]


def claim(path, draft_id, expected_revision, expected_queue=None):
    path = Path(path)
    with _locked(path):
        rows = _rows(path)
        row = _get(path, draft_id)
        if row["revision"] != expected_revision or (
            expected_queue is not None and queue_stamp(path) != expected_queue
        ):
            raise ValueError("The draft changed. Review it again before sending.")
        row["status"] = "sending"
        _write(path, [row if r["id"] == draft_id else r for r in rows])
        row["_claim_stamp"] = queue_stamp(path)
        return row


def settle(path, draft_id, status, expected_revision=None):
    path = Path(path)
    with _locked(path):
        rows = _rows(path)
        for row in rows:
            if row["id"] == draft_id and row["status"] == "sending":
                row["status"] = (
                    status
                    if expected_revision is None or row["revision"] == expected_revision
                    else "uncertain"
                )
        _write(path, rows)


def discard(path, draft_id):
    path = Path(path)
    with _locked(path):
        row = _get(path, draft_id)
        rows = _rows(path)
        row["status"] = "discarded"
        _write(path, [row if r["id"] == draft_id else r for r in rows])


def _norm(text):
    words = re.findall(r"[a-z0-9]+", str(text).casefold())
    digits = dict(
        zip(
            [
                "zero",
                "one",
                "two",
                "three",
                "four",
                "five",
                "six",
                "seven",
                "eight",
                "nine",
            ],
            "0123456789",
        )
    )
    normalized = " ".join(digits.get(w, w) for w in words)
    return re.sub(r"(?<=\d)\s+(?=\d)", "", normalized)


def queue_stamp(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class SpokenApproval:
    def __init__(
        self, session, identity, owner_identity, *, resolve=None, send=None, tts=None
    ):
        from system.whatsapp import resolve_approval_recipient, send_approved_chat

        self.session = session
        self.identity = identity
        self._owner_identity = owner_identity
        self._resolve = resolve or resolve_approval_recipient
        self._send = send or send_approved_chat
        self._pending = None
        self._ready_mono = 0.0
        self._ready_wall = 0.0
        self._utterance_wall = 0.0
        self._presenting = None
        self._generation = 0
        self.confirmation = ""
        self._seen = set()
        self._tasks = set()
        self._dispatch_tasks = set()
        self._closed = False
        self._tts = tts

    def authorized(self):
        return (
            not self._closed
            and bool(self.identity)
            and self._owner_identity() == self.identity
        )

    @property
    def transaction_active(self):
        return not self._closed and bool(
            self._presenting or self._pending or self._dispatch_tasks
        )

    def invalidate(self):
        import phone_session

        phone_session.advance_approval_epoch()
        self._generation += 1
        self._pending = None
        self._presenting = None
        self.confirmation = ""
        self._utterance_wall = 0.0

    def close(self, *_):
        self._closed = True
        self.invalidate()

    def start_review(self, path, draft_id):
        self.invalidate()
        generation = self._generation
        self._presenting = generation

        async def present():
            result = await self.review(path, draft_id, generation=generation)
            if (
                not self.confirmation
                and self.authorized()
                and generation == self._generation
            ):
                with contextlib.suppress(Exception):
                    await self._speak(result["say"])

        task = asyncio.create_task(present())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _speak(self, line):
        kwargs = {"allow_interruptions": True}
        emitted = 0
        if self._tts is not None:

            async def audio():
                nonlocal emitted
                async with self._tts.synthesize(line) as stream:
                    async for chunk in stream:
                        emitted += chunk.frame.samples_per_channel
                        yield chunk.frame

            kwargs["audio"] = audio()
        speech = self.session.say(line, **kwargs)
        await speech.wait_for_playout()
        return (
            not speech.interrupted
            and speech.exception() is None
            and (self._tts is None or emitted > 0)
        )

    async def review(self, path, draft_id, *, generation=None):
        if generation is None:
            self.invalidate()
            generation = self._generation
        elif generation != self._generation:
            return {"say": "Review cancelled. Nothing sent."}
        if not self.authorized():
            return {
                "say": "Spoken sending needs an authenticated private owner phone call."
            }
        self._presenting = generation
        try:
            row = _get(path, draft_id)
            if (
                not isinstance(row.get("text"), str)
                or (not row["text"].strip() and not row.get("image"))
                or len(row["text"]) > 500
                or row["text"] != row["text"].strip()
                or not isinstance(row.get("chat"), str)
                or not row["chat"].strip()
            ):
                return {
                    "say": "That draft needs an explicit revision before send review. Nothing sent."
                }
            if row.get("image"):
                from wa_image import verify_image

                if not verify_image(row["image"]):
                    return {
                        "say": "The attached image changed. Nothing sent; capture it again."
                    }
            stamp = queue_stamp(path)
            target = await self._resolve(row["chat"])
            if not target.get("ok"):
                return {
                    "say": "Draft saved. "
                    + str(target.get("err", "Recipient could not be verified."))
                }
            if generation != self._generation or not self.authorized():
                return {"say": "The owner phone call ended; nothing was sent."}
            target = {"name": target["name"], "jid": target["jid"]}
            digits = [
                "zero",
                "one",
                "two",
                "three",
                "four",
                "five",
                "six",
                "seven",
                "eight",
                "nine",
            ]
            code = " ".join(
                digits[int(d)] for d in str(uuid.uuid4().int % 10000).zfill(4)
            )
            phrase = f"confirm WhatsApp dispatch {code}"
            attachment_description = (
                f" PNG image {row['image']['width']} by {row['image']['height']} pixels, "
                f"SHA 256 checksum {row['image']['sha256']}."
                if row.get("image")
                else ""
            )
            line = (
                f"Sending review. WhatsApp {'group' if target['jid'].endswith('@g.us') else 'chat'}: {target['name']}. "
                f"Conversation identifier: {target['jid']}. "
                f"Exact message: {row['text'] or 'no caption'}. End of message. "
                f"Attachment:{attachment_description} "
                f"Review code: {code}. To send this exact draft, say confirm WhatsApp dispatch, "
                "followed by that review code. Any other reply cancels this send review."
            )
            played = await self._speak(line)
            if not played or generation != self._generation or not self.authorized():
                return {
                    "say": "Review interrupted or unavailable. Nothing was sent; review again."
                }
            if (
                _get(path, draft_id)["revision"] != row["revision"]
                or queue_stamp(path) != stamp
            ):
                return {"say": "The draft changed during review. Review it again."}
            self._pending = (Path(path), row, target, stamp)
            self.confirmation = phrase
            self._ready_mono = time.monotonic()
            self._ready_wall = time.time()
            self._utterance_wall = 0.0
            return {
                "say": "Exact draft reviewed. Awaiting your specific spoken send confirmation; nothing sent yet."
            }
        except Exception as exc:
            return {"say": "Review unavailable; nothing was sent. " + str(exc)[:160]}
        finally:
            if self._presenting == generation:
                self._presenting = None

    def user_state(self, event):
        if getattr(event, "new_state", "") == "speaking":
            if self._presenting or self._dispatch_tasks:
                self.invalidate()
            else:
                self._utterance_wall = time.time()

    def playback_started(self, event):
        # SDK AudioOutput emits synchronously at playback start, before the
        # assistant conversation item is committed. Repeated speaking state
        # transitions may be coalesced; every actual audio segment must disarm.
        if self._pending or self._dispatch_tasks:
            self.invalidate()

    def agent_state(self, event):
        if getattr(event, "new_state", "") == "speaking":
            self.playback_started(event)

    def agent_item(self, event):
        # The model speaking the challenge is never proof of user intent and
        # can contaminate the mic through echo. Any post-review speech disarms.
        item = getattr(event, "item", None)
        if self._pending and getattr(item, "role", "") == "assistant":
            self.invalidate()

    def user_input(self, event):
        text = str(getattr(event, "transcript", "") or "")
        created = getattr(event, "created_at", 0)
        key = getattr(event, "item_id", None) or (created, text)
        if getattr(event, "is_final", False) and key in self._seen:
            return
        if self._dispatch_tasks and text:
            self.invalidate()  # cancellation/new speech during a queued dispatch
            return
        if self._presenting and text:
            self.invalidate()
        if not self._pending:
            return
        if re.search(r"\b(cancel|stop|no|don't|dont)\b", text.casefold()):
            self.invalidate()
            return
        if not getattr(event, "is_final", False):
            return
        self._seen.add(key)
        if len(self._seen) > 512:
            self.close()  # bounded lifetime, never forget a replay ledger
            return
        accepted = (
            self.authorized()
            and self._utterance_wall > self._ready_wall
            and isinstance(created, (int, float))
            and created >= self._utterance_wall
            and created <= time.time() + 1
            and time.time() - created < 5
            and time.monotonic() - self._ready_mono < CONFIRM_TTL
            and _norm(text) == _norm(self.confirmation)
        )
        pending_send = self._pending
        self.invalidate()  # one final utterance always consumes the review
        if accepted:
            generation = self._generation
            task = asyncio.create_task(self._dispatch(pending_send, generation))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            self._dispatch_tasks.add(task)
            task.add_done_callback(self._dispatch_tasks.discard)

    async def _dispatch(self, pending_send, generation):
        path, row, target, stamp = pending_send
        claimed_stamp = None

        def valid():
            if not self.authorized() or self._generation != generation:
                return False
            try:
                return claimed_stamp is None or queue_stamp(path) == claimed_stamp
            except OSError:
                return False

        if not valid():
            return
        try:
            claimed = claim(path, row["id"], row["revision"], stamp)
            claimed_stamp = claimed["_claim_stamp"]
        except ValueError:
            return
        result = {"ok": False}
        try:
            if valid():
                if row.get("image"):
                    from system.whatsapp import send_approved_image
                    from wa_image import verify_image

                    if verify_image(row["image"]):
                        result = await send_approved_image(
                            target, row["text"], row["image"], valid
                        )
                else:
                    result = await self._send(target, row["text"], valid)
        except Exception:
            pass  # transport failure may have already dispatched the message
        finally:
            settle(
                path,
                row["id"],
                "sent" if result.get("ok") is True else "uncertain",
                row["revision"],
            )
        if self.authorized():
            with contextlib.suppress(Exception):
                await self._speak(
                    "WhatsApp message sent."
                    if result.get("ok")
                    else "WhatsApp send was not verified. I will not retry automatically."
                )

    async def drain(self):
        if self._tasks:
            await asyncio.gather(*list(self._tasks))


def install(session, *, tts=None):
    import phone_session

    owner = phone_session.identity()
    if not owner:
        return None
    gate = SpokenApproval(session, owner, phone_session.identity, tts=tts)
    phone_session.bind_approval_gate(gate)
    session._jarvis_whatsapp_approval = gate
    session.on("user_input_transcribed", gate.user_input)
    session.on("user_state_changed", gate.user_state)
    session.on("conversation_item_added", gate.agent_item)
    session.on("agent_state_changed", gate.agent_state)
    audio = getattr(getattr(session, "output", None), "audio", None)
    if audio is not None:
        audio.on("playback_started", gate.playback_started)
    session.on("close", gate.close)
    return gate
