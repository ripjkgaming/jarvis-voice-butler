# Transcription recovery — 2 October 2026

## Confirmed defects and changes

- Normal wake/PTT discarded the existing microphone queue and opened another stream after joining. Speech during connection could be lost before the agent subscribed. These calls now retain the original capture stream, a short onset tail, and bounded join audio, then replay once after readiness. School's already-transcribed opening question keeps its existing backlog-discard policy.
- A completed greeting with fractional timestamps could cover a later user caption stored in the same whole second. An unfinished greeting could also override every newer user line. All three caption surfaces now share chronological selection with a user-preserving tie rule.
- A follow-up watcher started after greeting ignored speech already in progress. Agent state changes could also arm an idle deadline during user speech. The watcher now tracks both states, gives a continuous-audio pause prompt, and uses a bounded 60-second hold without forcing possible noise into a model/tool turn.
- Failed LiveKit SpeechHandles resolve their awaitable without raising. Presence speech now checks the stored error; interrupted greetings neither append a false full caption nor retry over an active conversation.

The capture queue aborts with a retry caption on overflow, rather than forwarding a truncated command. Muting invalidates buffered audio. Catch-up removes only confirmed digital zero; nonzero ambient audio can retain join delay because quiet speech must not be discarded speculatively. Microphone gain, AEC settings, voice model chain, and speech thresholds are unchanged.

## Validation

- 311 focused Python tests passed; 3 opt-in live-model tests skipped.
- After independent review added mute-race and configured-endpoint cases, the final capture suite passed all 83 tests.
- 178 frontend Node tests passed.
- Production frontend build and native debug/release builds passed.
- Final production-export headless caption checks passed across normal, school, and return, including an in-flight poll; no page errors.
- Final joining-animation headless check passed 18 lifecycle scenarios at DPR 2.
- Direct synthetic provider and roomless framework probes both transcribed the known phrase.
- Integrated capture-buffer/framework probe began speech two seconds before readiness. All words survived and the provider/framework produced the exact intended transcription in one bounded provider connection. No physical microphone, speaker, real room, or tools were used in this probe.

The integrated probe's reply repeated its greeting instead of returning only the requested word. This is recorded separately from transcription success; it does not establish general answer correctness. The event trace showed no duplicate trailing empty client turn. No provider-protocol change was inferred from that one response.

Live microphone amplitude/VAD diagnosis retained no recordings. The observed continuous VAD activity is evidence of a possible missing endpoint, not proof that background noise caused every reported call failure. Physical microphone-to-screen confirmation after activation remains a separate check.

The updated native app was restarted on 2 October at 10:59 Singapore time. The bridge was healthy after 50 seconds; the worker registered successfully and wake listening resumed with the microphone unmuted and normal mode preserved.

## Related requested changes

The separate GPT-6 Astra High joining-animation chat delivered stage-specific reactor and school-bar motion, truthful slow-join explanations, reduced-motion behavior, visibility suspension, and failure/retry states. The unified export passed its 18-scenario headless lifecycle check.

The computer-use worker now defaults to GPT-6 Luna, retaining low effort and all existing action/permission controls. No persisted Terra override was found. Its 44 focused tests passed, with one optional CLI smoke skipped; no desktop action or live navigation-model turn was performed.
