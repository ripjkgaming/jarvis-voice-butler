# Voice repetition fixes

The November 7 runtime captions showed repeated service-offer prefixes and repeated answers in separate model turns following small uncertain speech fragments. Partial and final caption entries are separate subtitle states; their presence alone is not evidence of duplicate audio. The previous reply-loop guard detected only three repeated sentences inside one reply, so it did not address these cases.

The engaged voice instructions now answer directly, greet only when greeted, avoid repeated service offers and just-given confirmations, and wait for meaningful continuations rather than answer noise. Explicit requests to repeat, clarify or verify remain valid. This improves model guidance; it does not claim a deterministic guarantee about every generated sentence.

Runtime protections cover concrete duplicate-delivery paths:

- Realtime generations carry utterance IDs. Session dispatch normalizes whitespace, drops unchanged partial updates, publishes each final once, and ignores canceled or superseded finals. The replay window is bounded to 128 utterances. A new utterance with identical words is accepted.
- Tool-call IDs are reserved before asynchronous execution, preventing overlapping callbacks from running the same provider operation twice. The window is 256 call IDs per session; a fresh call ID remains eligible, including a deliberate repeated request.
- Notification producers serialize the receipt check and delivery with a dedicated file lock. It is distinct from queue and queue-drain locks, so a notification can safely enqueue while holding its delivery lock.
- Wake announcement consumption normalizes whitespace and uses an atomic replacement under a cross-process file lock. Its clock is sampled inside the lock, avoiding older waiting workers discarding a newer receipt.
- The text echo backstop receives text during synchronized audio playout. A genuine request beginning with “repeat” or “restate” is preserved rather than mistaken for echo of the preceding answer.

`tests/test_voice_repetition.py` exercises actual session callbacks and listeners, canceled late finals, repeated identical user requests, overlapping mutation calls, overlapping notification producers, announcement races, and echo capture during playout. Tests use synthetic data and do not send external messages. The existing voice, reminder, school and notification tests are also run after the changes.
